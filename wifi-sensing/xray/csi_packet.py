"""Parse RuView ESP32 raw CSI datagrams (ADR-018, magic 0xC5110001).

Layout, matching the server parser (ruview-source v2/crates/
wifi-densepose-sensing-server/src/main.rs, parse_esp32_frame) and the firmware
writer (firmware/esp32-csi-node/main/csi_collector.c):

  off size  field
  0   4     magic 0xC5110001 (little-endian)
  4   1     node_id
  5   1     n_antennas
  6   2     n_subcarriers (LE u16)
  8   4     freq_mhz (LE u32)
  12  4     sequence (LE u32)
  16  1     rssi (i8; the server forces it negative)
  17  1     noise_floor (i8)
  18  1     ppdu_type
  19  1     flags
  20  2*n   I/Q payload, n = n_antennas * n_subcarriers (the firmware's len / 2)

Two payload encodings exist (measured 2026-11-07 on our ESP32-C5, firmware as flashed):
  int8  2 bytes per subcarrier: int8 I, int8 Q (the documented ESP32/ESP32-S3 layout).
  s12   the ESP32-C5 sends little-endian 16-bit words holding SIGNED 12-BIT values; word
        pairs are (I, Q), so a 106-byte payload is 26 complex subcarriers (+1 spare word).
        Read as int8 pairs, this looks like noise: in an empty room the frame-to-frame
        correlation of the amplitude profile was 0.01 as int8 and 0.998 as s12.
        Which word of a pair is real and which imaginary is NOT verified, so the phase
        convention is unverified; amplitudes are unaffected.
Format "auto" (default) picks s12 when every 16-bit word fits in 12 bits, which int8 data
with signal almost never does (any negative Q byte breaks it). Force one with the
XRAY_CSI_FORMAT environment variable (auto | int8 | s12).
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

import numpy as np

MAGIC_CSI = 0xC5110001
HEADER = struct.Struct("<IBBHIIbbBB")
assert HEADER.size == 20
FORMAT = os.environ.get("XRAY_CSI_FORMAT", "auto")


@dataclass(frozen=True)
class CsiFrame:
    node_id: int
    n_antennas: int
    n_subcarriers: int
    freq_mhz: int
    sequence: int
    rssi: int
    noise_floor: int
    ppdu_type: int
    flags: int
    csi: np.ndarray        # complex64, shape (n_antennas, n_subcarriers decoded)
    fmt: str = "int8"      # payload encoding: int8 | s12

    @property
    def amplitude(self) -> np.ndarray:
        """|H| of antenna 0, float32, shape (n_subcarriers,)."""
        return np.abs(self.csi[0]).astype(np.float32)


def parse_frame(buf: bytes) -> CsiFrame | None:
    """Return a CsiFrame, or None for anything that isn't a well-formed CSI datagram."""
    if len(buf) < HEADER.size:
        return None
    magic, node, n_ant, n_sc, freq, seq, rssi, noise, ppdu, flags = HEADER.unpack_from(buf)
    if magic != MAGIC_CSI or n_ant == 0 or n_sc == 0:
        return None
    n = n_ant * n_sc
    if len(buf) < HEADER.size + 2 * n:
        return None
    fmt = FORMAT
    words = np.frombuffer(buf, dtype="<u2", count=n, offset=HEADER.size)
    if fmt == "auto":
        fmt = "s12" if bool((words < 0x1000).all()) else "int8"
    if fmt == "s12":
        v = words.astype(np.int32)
        v = np.where(v >= 0x800, v - 0x1000, v).astype(np.float32)
        m = (n // 2) // n_ant * n_ant                     # complex values that fill whole antennas
        csi = (v[0:2 * m:2] + 1j * v[1:2 * m:2]).astype(np.complex64).reshape(n_ant, m // n_ant)
    else:
        iq = np.frombuffer(buf, dtype=np.int8, count=2 * n, offset=HEADER.size).astype(np.float32)
        csi = (iq[0::2] + 1j * iq[1::2]).astype(np.complex64).reshape(n_ant, n_sc)
    return CsiFrame(node, n_ant, csi.shape[1], freq, seq, -rssi if rssi > 0 else rssi, noise, ppdu, flags, csi, fmt)


def build_frame(csi: np.ndarray, node_id: int = 1, sequence: int = 0, rssi: int = -50,
                freq_mhz: int = 2437, noise_floor: int = -95, ppdu_type: int = 0, flags: int = 0,
                fmt: str = "int8") -> bytes:
    """Encode a datagram (tests and replay tools only). csi: complex, shape (n_ant, n_sc).
    fmt="s12" writes the ESP32-C5 layout: n_sc complex values -> 2*n_sc 12-bit words, and the
    header's n_subcarriers is the word count, as the firmware reports it."""
    csi = np.atleast_2d(csi)
    n_ant, n_sc = csi.shape
    if fmt == "s12":
        w = np.empty(2 * csi.size, dtype=np.int32)
        w[0::2] = np.clip(np.round(csi.real.ravel()), -2048, 2047)
        w[1::2] = np.clip(np.round(csi.imag.ravel()), -2048, 2047)
        payload = (w & 0xFFF).astype("<u2").tobytes()
        n_sc_hdr = 2 * n_sc
    else:
        iq = np.empty(2 * csi.size, dtype=np.int8)
        iq[0::2] = np.clip(np.round(csi.real.ravel()), -128, 127)
        iq[1::2] = np.clip(np.round(csi.imag.ravel()), -128, 127)
        payload, n_sc_hdr = iq.tobytes(), n_sc
    head = HEADER.pack(MAGIC_CSI, node_id, n_ant, n_sc_hdr, freq_mhz, sequence & 0xFFFFFFFF, rssi,
                       noise_floor, ppdu_type, flags)
    return head + payload
