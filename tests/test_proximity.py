"""Synthetic-scan tests for robot/proximity.py (advisory warnings only).

  .venv/bin/python -m unittest tests.test_proximity -v
"""
import os, sys, unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from robot.proximity import Proximity  # noqa: E402

NOW = 1_000_000.0


def scan(objects=(), ring_mm=2000.0, step=0.7):
    """Room ring at ring_mm plus objects: (lidar_angle_center, half_width_deg, dist_mm)."""
    ang = np.arange(0, 360, step)
    dist = np.full_like(ang, ring_mm)
    for c, hw, d in objects:
        m = np.abs((ang - c + 180) % 360 - 180) <= hw
        dist[m] = np.minimum(dist[m], d)
    return np.stack([ang, dist, np.full_like(ang, 40.0)], axis=1)


class Frames(unittest.TestCase):
    def setUp(self):
        self.p = Proximity(forward_deg=4.2, direction="cw", self_mask=[])

    def test_front_close(self):
        r = self.p.assess(scan([(4.2, 5, 250)]), NOW, now_ms=NOW)
        self.assertFalse(r["stale"])
        self.assertEqual(r["sectors"]["front"]["level"], "close")
        self.assertEqual(r["sectors"]["back"]["level"], "clear")
        self.assertAlmostEqual(r["nearest"]["bearing_deg"], 0.0, delta=1.0)
        self.assertIn("obstacle 25 cm front", r["warnings"])

    def test_left_is_lidar_minus_90_when_cw(self):
        r = self.p.assess(scan([(4.2 - 90 + 360, 5, 450)]), NOW, now_ms=NOW)   # lidar 274.2
        self.assertEqual(r["sectors"]["left"]["level"], "near")
        self.assertEqual(r["sectors"]["right"]["level"], "clear")

    def test_ccw_flips_sides(self):
        p = Proximity(forward_deg=4.2, direction="ccw", self_mask=[])
        r = p.assess(scan([(4.2 - 90 + 360, 5, 450)]), NOW, now_ms=NOW)
        self.assertEqual(r["sectors"]["right"]["level"], "near")


class Robustness(unittest.TestCase):
    def setUp(self):
        self.p = Proximity(forward_deg=0, direction="cw", self_mask=[])

    def test_single_noise_point_ignored(self):
        s = scan()
        s[np.argmin(np.abs(s[:, 0] - 180))] = [180, 120, 40]      # one stray point behind
        r = self.p.assess(s, NOW, now_ms=NOW)
        self.assertEqual(r["sectors"]["back"]["level"], "clear")

    def test_invalid_and_too_close_ignored(self):
        s = scan([(90, 10, 200)])
        s[(s[:, 0] > 80) & (s[:, 0] < 100), 2] = 0                # quality 0
        s[(s[:, 0] > 170) & (s[:, 0] < 190), 1] = 30              # below 60 mm
        r = self.p.assess(s, NOW, now_ms=NOW)
        self.assertEqual(r["sectors"]["right"]["level"], "clear")  # lidar 90 cw -> right
        self.assertEqual(r["sectors"]["back"]["level"], "clear")

    def test_self_mask(self):
        p = Proximity(forward_deg=0, direction="cw", self_mask=[[150, -150, 250]])  # back +-30, <= 250 mm
        r = p.assess(scan([(180, 20, 200)]), NOW, now_ms=NOW)
        self.assertEqual(r["sectors"]["back"]["level"], "clear")
        r = p.assess(scan([(180, 20, 280)]), NOW, now_ms=NOW)     # beyond the mask distance
        self.assertEqual(r["sectors"]["back"]["level"], "close")

    def test_stale(self):
        r = self.p.assess(scan([(0, 5, 100)]), NOW - 600, now_ms=NOW)
        self.assertTrue(r["stale"])
        self.assertEqual(r["sectors"], {})
        self.assertTrue(r["warnings"][0].startswith("LiDAR stale"))
        self.assertTrue(self.p.assess(None, None, now_ms=NOW)["stale"])


class Toward(unittest.TestCase):
    def setUp(self):
        self.p = Proximity(forward_deg=0, direction="cw", self_mask=[])

    def test_forward_into_wall(self):
        r = self.p.assess(scan([(0, 10, 250)]), NOW, cmd=(1, 0, 0), now_ms=NOW)
        self.assertEqual((r["toward"]["level"], r["toward"]["side"]), ("close", "front"))

    def test_strafe_left_sees_left(self):
        r = self.p.assess(scan([(270, 10, 450)]), NOW, cmd=(0, 1, 0), now_ms=NOW)   # cw: lidar 270 = left
        self.assertEqual((r["toward"]["level"], r["toward"]["side"]), ("near", "left"))
        r = self.p.assess(scan([(270, 10, 450)]), NOW, cmd=(0, -1, 0), now_ms=NOW)  # strafing away
        self.assertEqual(r["toward"]["level"], "clear")

    def test_rotate_and_idle(self):
        r = self.p.assess(scan([(135, 5, 200)]), NOW, cmd=(0, 0, 1), now_ms=NOW)
        self.assertEqual((r["toward"]["kind"], r["toward"]["level"]), ("rotate", "close"))
        self.assertIsNone(self.p.assess(scan(), NOW, cmd=(0, 0, 0), now_ms=NOW)["toward"])


if __name__ == "__main__":
    unittest.main()
