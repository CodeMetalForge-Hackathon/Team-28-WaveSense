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
  20  2*n   I/Q pairs, int8 I then int8 Q, n = n_antennas * n_subcarriers
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

MAGIC_CSI = 0xC5110001
HEADER = struct.Struct("<IBBHIIbbBB")
assert HEADER.size == 20


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
    csi: np.ndarray        # complex64, shape (n_antennas, n_subcarriers)

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
    iq = np.frombuffer(buf, dtype=np.int8, count=2 * n, offset=HEADER.size).astype(np.float32)
    csi = (iq[0::2] + 1j * iq[1::2]).astype(np.complex64).reshape(n_ant, n_sc)
    return CsiFrame(node, n_ant, n_sc, freq, seq, -rssi if rssi > 0 else rssi, noise, ppdu, flags, csi)


def build_frame(csi: np.ndarray, node_id: int = 1, sequence: int = 0, rssi: int = -50,
                freq_mhz: int = 2437, noise_floor: int = -95, ppdu_type: int = 0, flags: int = 0) -> bytes:
    """Encode a datagram (tests and replay tools only). csi: complex, shape (n_ant, n_sc)."""
    csi = np.atleast_2d(csi)
    n_ant, n_sc = csi.shape
    iq = np.empty(2 * csi.size, dtype=np.int8)
    iq[0::2] = np.clip(np.round(csi.real.ravel()), -128, 127)
    iq[1::2] = np.clip(np.round(csi.imag.ravel()), -128, 127)
    head = HEADER.pack(MAGIC_CSI, node_id, n_ant, n_sc, freq_mhz, sequence & 0xFFFFFFFF, rssi,
                       noise_floor, ppdu_type, flags)
    return head + iq.tobytes()
