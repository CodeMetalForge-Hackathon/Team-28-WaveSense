"""Headless access to an Analog Devices EV-TINYRAD24G (24 GHz FMCW, 2 TX / 4 RX) over USB.

Uses ADI's own TinyRadTool code (https://github.com/analogdevicesinc/TinyRadTool, BSD-style
licence), which is NOT vendored here: point TINYRAD_TOOL at a clone (default ~/tinyrad/TinyRadTool).
It needs pyusb and numpy; its GUI dependency (pyqtgraph) is only used for plotting, so a stub is
installed before import. USB access needs a udev rule for 064b:7823 (see radar/README.md).

    dev = TinyRadDevice(); dev.start()
    for t, frame in dev.frames():      # frame: int16 (N samples, 4 RX channels), t: receive time
        ...
"""
from __future__ import annotations

import os
import struct
import sys
import time
import types

import numpy as np

TOOL = os.path.expanduser(os.environ.get("TINYRAD_TOOL", "~/tinyrad/TinyRadTool"))
C = 299_792_458.0


def _stub_pyqtgraph() -> None:
    class _Any:
        def __getattr__(self, name): return _Any()
        def __call__(self, *a, **k): return _Any()
    pg, qt = types.ModuleType("pyqtgraph"), types.ModuleType("pyqtgraph.Qt")
    qt.QtGui = qt.QtCore = qt.QtWidgets = _Any()
    pg.Qt = qt
    pg.__getattr__ = lambda name: _Any()
    sys.modules.setdefault("pyqtgraph", pg)
    sys.modules.setdefault("pyqtgraph.Qt", qt)


def read_factory_calibration(rad, channels: int = 4) -> np.ndarray:
    """Factory RX correction for TX1, read from the board's EEPROM (read-only).

    Same layout as ADI's BrdGetCalDat: little-endian signed 32-bit re/im pairs scaled by 2**24.
    BrdGetCalDat itself cannot be used under NumPy 2: its `uint8 << 24` stays uint8, so only the
    low byte of each word survives and every coefficient comes out near 1e-6."""
    raw = bytes(int(rad.BrdRdEEPROM(addr)[1][0]) & 0xFF for addr in range(channels * 8))
    words = struct.unpack(f"<{channels * 2}i", raw)
    return np.array([complex(words[2 * i], words[2 * i + 1]) / 2**24 for i in range(channels)])


class TinyRadDevice:
    """One chirp per frame: 24.00-24.25 GHz, 256 us up-ramp sampled at 1 MHz (N=256), TX1 at
    full amplitude, all 4 RX channels: the same settings as ADI's FMCW mode. Measured 2026-11-07:
    250 frames/s readable from Python on a Raspberry Pi 5, no DMA errors."""

    def __init__(self, f_start=24.0e9, f_stop=24.25e9, n=256, period=2e-3, tx=1, tx_amp=100):
        self.f_start, self.f_stop, self.n, self.period, self.tx, self.tx_amp = f_start, f_stop, n, period, tx, tx_amp
        self.rad = None
        self.rx_calibration = None

    @property
    def bandwidth(self) -> float:
        return self.f_stop - self.f_start

    @property
    def wavelength(self) -> float:
        return C / ((self.f_start + self.f_stop) / 2)

    def range_axis(self, nfft: int) -> np.ndarray:
        """Range in metres of each FFT bin (beat frequency * c / (2 * slope))."""
        slope = self.bandwidth / (self.n * 1e-6)
        return np.arange(nfft // 2 + 1) * (1e6 / nfft) * C / (2 * slope)

    @staticmethod
    def usb_reset() -> None:
        """Reset the radar's USB connection and drain stale bulk data. A previous process that died
        while streaming leaves data queued, and the next session's first command then fails with
        USB 'Overflow' or a timeout (seen 2026-11-07). Equivalent to unplugging it."""
        import usb.core
        import usb.util
        d = usb.core.find(idVendor=0x064B, idProduct=0x7823)
        if d is None:
            raise RuntimeError("TinyRad (USB 064b:7823) not found: is it plugged in?")
        try:
            d.reset()
        except usb.core.USBError:
            pass
        time.sleep(1.0)
        d = usb.core.find(idVendor=0x064B, idProduct=0x7823)
        try:
            d.set_configuration()
            ep = usb.util.find_descriptor(d.get_active_configuration()[(0, 0)],
                                          custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
            for _ in range(2000):                       # drain whatever is queued
                ep.read(512 * 64, timeout=50)
        except usb.core.USBError:
            pass
        finally:
            usb.util.dispose_resources(d)

    def start(self) -> "TinyRadDevice":
        self.usb_reset()
        _stub_pyqtgraph()
        if TOOL not in sys.path:
            sys.path.insert(0, TOOL)
        from src.cmd_modules.TinyRad import TinyRad  # noqa: E402  (ADI code)
        from src.cmd_modules import Connection
        from radar_threading import guard_vendor_signals
        guard_vendor_signals(Connection)  # USB recovery runs in the acquisition thread.
        self.rad = TinyRad()
        self.rad.BrdRst()
        self.rx_calibration = None
        try:
            factory = read_factory_calibration(self.rad)
            if np.isfinite(factory).all() and np.all((np.abs(factory)>.01)&(np.abs(factory)<100)):
                self.rx_calibration = factory
                print("radar: factory RX correction loaded:", np.round(factory, 4).tolist(), flush=True)
            else:
                print("radar: factory RX correction rejected, direction unavailable:", np.round(factory, 6).tolist(), flush=True)
        except Exception as error:
            # Range detection continues; never invent direction without correction.
            print("radar: factory RX correction unreadable, direction unavailable:", type(error).__name__, flush=True)
        self.rad.RfRxEna()
        self.rad.RfTxEna(self.tx, self.tx_amp)
        self.rad.RfMeas({"fStrt": self.f_start, "fStop": self.f_stop, "TRampUp": self.n * 1e-6, "N": self.n,
                         "Perd": self.period, "CycSiz": 4, "Seq": [1], "FrmSiz": 2, "FrmMeasSiz": 1})
        return self

    def version(self) -> dict:
        return self.rad.BrdGetSwVers()

    def read(self) -> np.ndarray:
        return np.asarray(self.rad.BrdGetData()).reshape(self.n, -1)

    def frames(self, stop=None):
        while stop is None or not stop.is_set():
            d = self.read()
            yield time.time(), d

    def close(self) -> None:
        if self.rad is not None:
            try:
                self.rad.BrdRst()          # stops the transmitter
            except Exception:
                pass
            self.rad = None
