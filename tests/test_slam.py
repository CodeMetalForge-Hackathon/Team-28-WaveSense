"""SLAM pipeline tests with synthetic ray-cast scans of an asymmetric room (no hardware).

  .venv/bin/python -m unittest tests.test_slam -v
"""
import math, os, sys, unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mapping.slam import NO_INFO, Slam  # noqa: E402
from robot.proximity import Proximity  # noqa: E402

FWD = 4.2   # same forward angle as config; LiDAR angles clockwise
# Room 5 m x 4 m (world mm, origin bottom-left), plus a box in the upper-left corner so the
# room is not mirror-symmetric. Segments: ((x1, y1), (x2, y2)).
W, H = 5000.0, 4000.0
SEGS = [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0)),
        ((800, 3000), (1600, 3000)), ((1600, 3000), (1600, H))]


def ray(px, py, ang):
    dx, dy, best = math.cos(ang), math.sin(ang), 1e9
    for (x1, y1), (x2, y2) in SEGS:
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = ((x1 - px) * ey - (y1 - py) * ex) / den
        u = ((x1 - px) * dy - (y1 - py) * dx) / den
        if t > 0 and 0 <= u <= 1:
            best = min(best, t)
    return best


def c1_scan(x, y, heading_deg, n=508):
    """What the C1 would report from world pose (x, y, heading ccw): raw clockwise angles."""
    raw = np.arange(n) * 360.0 / n
    bearing = -(raw - FWD)                               # cw LiDAR -> ccw robot bearing
    world = np.radians(heading_deg + bearing)
    d = np.array([ray(x, y, a) for a in world])
    return np.stack([raw, d, np.full(n, 40.0)], axis=1)


def new_slam():
    return Slam(lidar=None, prox=Proximity(forward_deg=FWD, direction="cw", self_mask=[]), seed=1234,
                map_every_s=0.0)


class Resample(unittest.TestCase):
    def test_bins_layout(self):
        s = new_slam()
        scan = np.array([[FWD, 1500, 40], [(FWD - 90) % 360, 800, 40], [100, 0, 0]], float)
        b = s.resample(scan)
        self.assertEqual(b[180], 1500)           # straight ahead -> bin 180
        self.assertEqual(b[270], 800)            # robot left (+90) -> bin 270
        self.assertEqual(int((b > NO_INFO).sum()), 2)   # invalid point -> no info, not 0
        self.assertNotIn(0, b.tolist())


class Tracking(unittest.TestCase):
    def run_path(self, path):
        s = new_slam()
        x0, y0, _ = s.pose
        sx, sy = 2500.0, 1500.0                  # true start in the room
        for dx, dy, h in path:
            s.process(c1_scan(sx + dx, sy + dy, h))
        x, y, th = s.pose
        return s, x - x0, y - y0, th

    def test_forward_increases_x(self):
        s, dx, dy, th = self.run_path([(k * 20.0, 0, 0) for k in range(41)])      # 800 mm forward
        self.assertAlmostEqual(dx, 800, delta=40); self.assertAlmostEqual(dy, 0, delta=40)

    def test_left_increases_y(self):
        s, dx, dy, th = self.run_path([(0, k * 20.0, 0) for k in range(41)])      # 800 mm to the left
        self.assertAlmostEqual(dy, 800, delta=40); self.assertAlmostEqual(dx, 0, delta=40)

    def test_ccw_rotation_positive(self):
        s, dx, dy, th = self.run_path([(0, 0, k * 3.0) for k in range(21)])       # turn 60 deg left
        self.assertAlmostEqual(th, 60, delta=8)

    def test_map_not_mirrored(self):
        s, *_ = self.run_path([(k * 10.0, 0, 0) for k in range(10)])
        s.refresh_map()
        m = s.map
        # The corner box is at world x 800-1600, y 3000-4000 (upper-left of the room). Relative to
        # the start (2500, 1500) that is up and to the left in an x-right / y-up image.
        cx, cy = s.to_pixel(s.trail[0][0] - 2500 + 1200, s.trail[0][1] - 1500 + 3000)   # box bottom edge
        mx, my = s.to_pixel(s.trail[0][0] - 2500 + 3800, s.trail[0][1] - 1500 + 3000)   # mirror spot (x)
        dark = lambda c, r: m[r - 3:r + 4, c - 3:c + 4].min()
        self.assertLess(dark(cx, cy), 100, "box wall missing where expected")
        self.assertGreater(dark(mx, my), 100, "box appears mirrored")


if __name__ == "__main__":
    unittest.main()
