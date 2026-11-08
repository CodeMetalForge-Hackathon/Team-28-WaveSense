"""RPLIDAR C1, standard (legacy) scan protocol implemented directly.

Why not rplidar-roboticia: on this C1 it returned info/health but no scans
within 5 s (two runs). Its reader also has no resync and no read timeout.

Protocol: STOP A5 25, GET_HEALTH A5 52, SCAN A5 20 -> 7-byte descriptor
A5 5A 05 00 00 40 81, then 5-byte nodes:
  b0: bit0 S (start of revolution), bit1 !S, bits2-7 quality
  b1: bit0 check bit (=1), bits1-7 angle low; b2: angle high   angle = q6 / 64 deg
  b3,b4: distance q2 / 4 mm (0 = invalid)
Motor: this C1 is run by the adapter's DTR line (False = spin, True = stop),
as the Slamtec SDK does for it. Data starts ~2.05 s after SCAN (spin-up).

Closing the CP2102N port takes ~5 s in the kernel, so close() runs in a
background thread; nothing (in particular no motor stop) waits for it. Only a
reopen of the same port waits for the previous close to finish.

snapshot()["data"] is a float32 array (N, 3): angle_deg, dist_mm, quality,
one full revolution (invalid points kept with dist 0 / quality 0).
"""
import threading, time

import numpy as np
import serial

from sensors.base import StreamingSensor, device_path, now_ms

STOP, HEALTH, SCAN = b"\xA5\x25", b"\xA5\x52", b"\xA5\x20"
SCAN_DESC = bytes.fromhex("a55a0500004081")
HEALTH_DESC = bytes.fromhex("a55a0300000006")


class Lidar(StreamingSensor):
    name = "lidar"
    stale_after = 0.5
    first_data_s = 5.0     # spin-up allowance after SCAN (measured 2.05 s)
    stall_s = 1.0          # no bytes for this long once streaming -> reconnect

    def __init__(self, path=None, baud=460800):
        super().__init__(path or device_path("lidar"))
        self.baud = baud
        self.ser = None
        self.health = None
        self.resyncs = 0
        self.bad_nodes = 0
        self._closer = None
        self._buf = bytearray()
        self._rev = []
        self._started = False
        self._last_angle = 0.0

    # ---- open/close -------------------------------------------------------
    def _read_exact(self, n, timeout):
        end, out = time.monotonic() + timeout, bytearray()
        while len(out) < n and time.monotonic() < end:
            out += self.ser.read(n - len(out))
        return bytes(out)

    def _open(self):
        if self._closer is not None and self._closer.is_alive():
            self._set_status("connecting", "waiting for previous port close")
            self._closer.join(12.0)
        s = serial.Serial(None, self.baud, timeout=0.05)
        s.dtr = True                 # motor off while we set up
        s.port = self.path
        s.open()
        self.ser = s
        s.write(STOP)
        s.flush()
        time.sleep(0.05)
        s.reset_input_buffer()
        s.write(HEALTH)
        r = self._read_exact(10, 1.0)
        if r[:7] != HEALTH_DESC:
            raise IOError(f"bad GET_HEALTH reply {r.hex(' ')}")
        self.health = dict(status=("good", "warning", "error")[min(r[7], 2)], code=r[8] | r[9] << 8)
        if r[7] == 2:
            raise IOError(f"lidar reports error state, code {self.health['code']}")
        s.dtr = False                # motor on
        s.write(SCAN)
        d = self._read_exact(7, 1.0)
        if d != SCAN_DESC:
            raise IOError(f"bad SCAN descriptor {d.hex(' ')}")
        self._buf, self._rev, self._started, self._last_angle = bytearray(), [], False, 0.0
        self._t_scan = self._t_bytes = time.monotonic()
        self._got_data = False
        self._set_status("starting", "spinning up (~2 s)")

    def _close(self):
        s, self.ser = self.ser, None
        if s is None:
            return
        try:                         # stop scanning + motor off: fast, never blocks on close
            s.write(STOP)
            s.flush()
            s.dtr = True
        except Exception:
            pass                     # unplugged: nothing to stop
        self._closer = threading.Thread(target=s.close, name="lidar-close", daemon=True)
        self._closer.start()

    # ---- reading ------------------------------------------------------------
    def _read(self):
        # up to 50 ms wait for ~1 KB (~20 ms of data at 5k nodes/s): few syscalls, low latency
        chunk = self.ser.read(max(self.ser.in_waiting, 1024))
        now = time.monotonic()
        if not chunk:
            limit = self.stall_s if self._got_data else self.first_data_s
            if now - self._t_bytes > limit:
                raise TimeoutError(f"no lidar data for {limit:.0f} s")
            return
        self._t_bytes, self._got_data = now, True
        self._buf += chunk
        self._parse()

    def _parse(self):
        while len(self._buf) >= 5:
            n = len(self._buf) // 5
            a = np.frombuffer(bytes(self._buf[:n * 5]), np.uint8).reshape(n, 5)
            ok = ((a[:, 0] & 1) != ((a[:, 0] >> 1) & 1)) & ((a[:, 1] & 1) == 1)
            bad = np.flatnonzero(~ok)
            good = n if bad.size == 0 else int(bad[0])
            # Before a detected bad node, the last nodes may be misaligned garbage that happened
            # to pass the 2-bit check (seen in tests), so drop 2 of them.
            keep = good if bad.size == 0 else max(0, good - 2)
            if keep:
                self._nodes(a[:keep])
            if bad.size == 0:
                del self._buf[:n * 5]
                return
            # misaligned: drop the good part plus one byte, then look for 3 valid nodes in a row
            del self._buf[:good * 5 + 1]
            self.bad_nodes += 1
            self.resyncs += 1
            self._realign()

    def _realign(self):
        b = self._buf
        for off in range(0, max(0, len(b) - 15)):
            if all((b[off + 5 * k] & 1) != ((b[off + 5 * k] >> 1) & 1) and b[off + 5 * k + 1] & 1
                   for k in range(3)):
                del b[:off]
                return
        del b[:max(0, len(b) - 14)]  # keep a short tail, wait for more bytes

    def _nodes(self, a):
        start = (a[:, 0] & 1).astype(bool)
        ang = ((a[:, 1] >> 1).astype(np.float32) + a[:, 2].astype(np.float32) * 128) / 64.0
        dist = (a[:, 3].astype(np.float32) + a[:, 4].astype(np.float32) * 256) / 4.0
        q = (a[:, 0] >> 2).astype(np.float32)
        pts = np.stack([ang, dist, q], axis=1)
        # Backup for a lost start node: an angle wrap (drop > 180 deg to < 20 deg) also starts a revolution,
        # otherwise two revolutions would be published as one. Fragments < 50 nodes are dropped.
        prev_ang = np.concatenate([[self._last_angle], ang[:-1]])
        start |= (ang < prev_ang - 180.0) & (ang < 20.0)   # a real wrap lands near 0 deg
        self._last_angle = float(ang[-1])
        idx = np.flatnonzero(start)
        prev = 0
        for i in idx:
            if i > prev:
                self._rev.append(pts[prev:i])
            if self._started and self._rev:
                rev = np.concatenate(self._rev)
                if len(rev) >= 50:
                    self._publish(rev, now_ms())
            self._started = True     # first partial revolution is dropped
            self._rev, prev = [], i
        if prev < len(pts):
            self._rev.append(pts[prev:])
