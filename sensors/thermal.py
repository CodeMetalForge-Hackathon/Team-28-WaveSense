"""HT-203U thermal camera.

Tries OpenCV CAP_V4L2 first (raw buffer, no colour conversion). On this
firmware OpenCV cannot open the device (UVC GET_MIN probe fails), so it falls
back to ONE long-lived `v4l2-ctl --stream-mmap --stream-to=-` process whose
stdout is split into 200,704-byte frames (re-synchronised on the layout
markers if a frame is ever misaligned). Decoding reuses sensors/thermal_grab.py.

snapshot()["data"] is a dict:
  color      BGR uint8 (192, 256, 3)  colormapped relative intensity
  intensity  float32 (192, 256), 0..1, per-frame relative (NOT temperature)
  hot        hottest pixel (x, y), and centroid/bbox of the warmest 2 %
  temp       camera's own summary, UNCALIBRATED: max_c, max_xy, min_c, mean_c,
             center_c, plus age_ms (it only updates ~2.5 Hz); None until seen
  backend    "opencv" or "v4l2-ctl"
Show temperatures as "~NN °C (uncal.)".
"""
import os, selectors, struct, subprocess, time

import cv2
import numpy as np

from sensors.base import StreamingSensor, device_path, now_ms
from sensors.thermal_grab import FRAME, decode

MARK_OFF, MARK = 193 * 512, struct.pack("<I", 0xAABBCCDD)
SIG_OFF, SIG = 388 * 512, struct.pack("<2H", 0xFFFF, 0xEEEE)


def frame_ok(buf, start=0):
    return (buf[start + MARK_OFF:start + MARK_OFF + 4] == MARK
            and buf[start + SIG_OFF:start + SIG_OFF + 4] == SIG)


class Thermal(StreamingSensor):
    name = "thermal"
    stale_after = 0.5
    stall_s = 2.0

    def __init__(self, path=None):
        super().__init__(path or device_path("thermal"))
        self.backend = None          # decided on first open
        self.opencv_error = ""
        self.resyncs = 0
        self.cap = self.proc = self.sel = None
        self._buf = bytearray()
        self._summary = None         # last valid camera summary + capture time

    # ---- open/close -----------------------------------------------------
    def _open(self):
        dev = os.path.realpath(self.path)
        if self.backend in (None, "opencv"):
            cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
            ok = cap.isOpened()
            if ok:
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
                ok, raw = cap.read()
                ok = ok and raw is not None and raw.nbytes == FRAME and frame_ok(raw.tobytes())
            if ok:
                self.cap, self.backend = cap, "opencv"
                return
            cap.release()
            self.opencv_error = "OpenCV CAP_V4L2 could not open/read a valid raw frame"
            self.backend = "v4l2-ctl"
        self._buf = bytearray()
        self.proc = subprocess.Popen(
            ["v4l2-ctl", "-d", dev, "--stream-mmap=4", "--stream-to=-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout, selectors.EVENT_READ)
        self._last_bytes = time.monotonic()
        self._set_status("starting", f"{dev} via v4l2-ctl")

    def _close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=1.0)
            self.proc.stdout.close()
            self.proc = None
        if self.sel is not None:
            self.sel.close()
            self.sel = None

    # ---- reading ----------------------------------------------------------
    def _read(self):
        if self.cap is not None:
            ok, raw = self.cap.read()
            if not ok:
                raise IOError("OpenCV read failed")
            self._handle(raw.tobytes())
            return
        if not self.sel.select(timeout=0.2):
            if self.proc.poll() is not None:
                raise IOError(f"v4l2-ctl exited ({self.proc.returncode})")
            if time.monotonic() - self._last_bytes > self.stall_s:
                raise TimeoutError(f"no thermal data for {self.stall_s:.0f} s")
            return
        chunk = os.read(self.proc.stdout.fileno(), 262144)
        if not chunk:
            raise IOError(f"v4l2-ctl stream ended (rc={self.proc.poll()})")
        self._last_bytes = time.monotonic()
        self._feed(chunk)

    def _feed(self, chunk):
        """Append stream bytes; decode every complete, marker-checked frame."""
        self._buf += chunk
        while len(self._buf) >= FRAME:
            if frame_ok(self._buf):
                frame = bytes(self._buf[:FRAME])
                del self._buf[:FRAME]
                self._handle(frame)
            else:
                self._resync()

    def _resync(self):
        """Drop bytes until a frame start whose markers check out."""
        self.resyncs += 1
        i = self._buf.find(MARK, 1)
        while i != -1:
            start = i - MARK_OFF
            if start > 0:
                if len(self._buf) - start < SIG_OFF + 4:   # can't verify yet: keep it, wait for data
                    del self._buf[:start]
                    return
                if frame_ok(self._buf, start):
                    del self._buf[:start]
                    return
            i = self._buf.find(MARK, i + 1)
        del self._buf[:-(MARK_OFF + 4)]   # keep a tail that may hold the next marker

    def _handle(self, frame):
        ts = now_ms()
        counts, summary = decode(frame)
        if summary:
            self._summary = dict(summary, seen_ms=ts)
        c = counts.astype(np.float32)
        lo, hi = np.percentile(c, (1, 99.5))
        inten = np.clip((c - lo) / max(hi - lo, 1.0), 0, 1)
        color = cv2.applyColorMap((inten * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        hy, hx = np.unravel_index(int(np.argmax(c)), c.shape)
        ys, xs = np.nonzero(c >= np.percentile(c, 98))
        hot = dict(x=int(hx), y=int(hy), centroid=(int(xs.mean()), int(ys.mean())),
                   bbox=(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                   counts_max=int(c.max()), counts_median=float(np.median(c)))
        temp = None
        if self._summary:
            s = self._summary
            temp = dict(max_c=s["max_c"], max_xy=s["max_xy"], min_c=s["min_c"], mean_c=s["mean_c"],
                        center_c=s["center_c"], age_ms=ts - s["seen_ms"], calibrated=False)
        self._publish(dict(color=color, intensity=inten, hot=hot, temp=temp, backend=self.backend), ts)
