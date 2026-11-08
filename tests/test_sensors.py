"""Synthetic tests for the Phase 1 sensor modules (no hardware needed).

  .venv/bin/python -m unittest tests.test_sensors -v
"""
import os, struct, sys, time, unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sensors.base import StreamingSensor  # noqa: E402
from sensors.lidar import Lidar  # noqa: E402
from sensors.thermal import Thermal  # noqa: E402
from sensors.thermal_grab import FRAME  # noqa: E402


def lidar_node(start, q, angle_deg, dist_mm):
    a = int(round(angle_deg * 64)); d = int(round(dist_mm * 4))
    b0 = (q << 2) | (2 if not start else 0) | (1 if start else 0)
    return bytes([b0, ((a & 0x7F) << 1) | 1, a >> 7, d & 0xFF, d >> 8])


def lidar_stream(revs, per_rev=500):
    out = bytearray()
    for r in range(revs):
        for i in range(per_rev):
            out += lidar_node(i == 0, 40, i * 360.0 / per_rev, 1000 + r)
    return bytes(out)


def thermal_frame(value):
    f = bytearray(FRAME)
    f[0:W_H2] = np.full(256 * 192, value, "<u2").tobytes()
    struct.pack_into("<I", f, 193 * 512, 0xAABBCCDD)
    struct.pack_into("<10H", f, 388 * 512, 0xFFFF, 0xEEEE, 64 * 80, 500, 500, 64 * 70, 0, 0, 64 * 75, 64 * 76)
    return bytes(f)


W_H2 = 256 * 192 * 2


class Base(unittest.TestCase):
    def test_fps_zero_when_not_ok(self):
        s = StreamingSensor("/nonexistent")
        for _ in range(10):
            s._publish("x"); time.sleep(0.01)
        self.assertGreater(s.snapshot()["fps"], 10)
        s._set_status("offline", "device not present")
        self.assertEqual(s.snapshot()["fps"], 0.0)
        s._publish("y"); s.stale_after = 0.05; time.sleep(0.1)
        snap = s.snapshot()
        self.assertEqual((snap["status"], snap["fps"]), ("stale", 0.0))


class LidarParse(unittest.TestCase):
    def test_clean_stream(self):
        l = Lidar("/nonexistent")
        l._buf += lidar_stream(4); l._parse()
        d = l.snapshot()["data"]
        self.assertEqual(l.frames, 3)          # first partial-revolution logic: 4 starts -> 3 complete
        self.assertEqual(d.shape, (500, 3))
        self.assertAlmostEqual(float(d[250, 0]), 180.0, places=1)
        self.assertEqual(float(d[0, 1]), 1002.0)
        self.assertEqual(l.resyncs, 0)

    def test_dropped_byte_resyncs(self):
        l = Lidar("/nonexistent")
        s = bytearray(lidar_stream(6))
        del s[1700 * 5 + 2]                    # lose one byte in the middle of revolution 3
        for i in range(0, len(s), 777):        # arrive in odd-sized chunks
            l._buf += s[i:i + 777]; l._parse()
        self.assertGreaterEqual(l.resyncs, 1)
        self.assertEqual(l.frames, 5)          # revs 0-4 published (rev 5 still open)
        d = l.snapshot()["data"]
        self.assertEqual(d.shape, (500, 3))
        self.assertTrue(np.all(d[:, 1] == 1004.0), "revolution after the glitch must be intact")

    def test_lost_start_node_splits_on_wrap(self):
        s = bytearray(lidar_stream(6))
        del s[1500 * 5:1500 * 5 + 5]           # the start node of revolution 3 vanishes entirely
        sizes = []
        l2 = Lidar("/nonexistent"); l2._publish = lambda d, ts=None: sizes.append(len(d))
        l2._buf += s; l2._parse()
        self.assertEqual(max(sizes), 500, f"no merged revolution expected, got sizes {sizes}")
        self.assertEqual(len(sizes), 5)


class ThermalSplit(unittest.TestCase):
    def test_frames_and_resync(self):
        t = Thermal("/nonexistent")
        stream = thermal_frame(5000) + b"\x01\x02\x03" + thermal_frame(5100) + thermal_frame(5200)
        for i in range(0, len(stream), 65536):  # pipe-sized chunks
            t._feed(stream[i:i + 65536])
        self.assertEqual(t.frames, 3)
        self.assertGreaterEqual(t.resyncs, 1)
        d = t.snapshot()["data"]
        self.assertEqual(d["color"].shape, (192, 256, 3))
        self.assertEqual(d["intensity"].shape, (192, 256))
        self.assertAlmostEqual(d["temp"]["max_c"], 30.0)   # 64*80/64 - 50
        self.assertFalse(d["temp"]["calibrated"])


if __name__ == "__main__":
    unittest.main()
