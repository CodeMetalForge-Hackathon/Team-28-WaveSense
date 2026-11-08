"""assess() must report a distance that actually fired. Synthetic features; no real-world claim."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
try:
    import numpy as np
    from radar_engine import RadarEngine
except ImportError:
    np = None


@unittest.skipIf(np is None, "Needs numpy / Pi venv")
class ReportedRangeTests(unittest.TestCase):
    def setUp(self):
        self.e = RadarEngine(np.arange(257) * 0.3, None, 5.0, adaptive=True)
        n = len(self.e.bins)
        self.rng = self.e.r_axis[self.e.bins]
        self.far = int(np.argmin(np.abs(self.rng - 3.9)))
        self.near = int(np.argmin(np.abs(self.rng - 1.2)))
        self.e.floor = np.ones(n)
        self.e.bg_p, self.e.bg_m, self.e._bg_t = np.ones(n), np.ones(n), 0.0
        self.e.bg_p[self.far] = 1000.0                   # wobble fires here at a low ratio
        self.e.breathing = lambda *a: (None, 0.0)
        self.n = n

    def features(self, wobble=True):
        pres, stat, mot = np.full(self.n, .1), np.full(self.n, 100.0), np.full(self.n, .1)
        pres[self.near] = 5.0                            # best ratio, but below every threshold
        if wobble:
            pres[self.far], stat[self.far] = 40.0, 1.0
        return dict(valid=True, rate_hz=25.0, coverage=1.0, pres=pres, stat=stat, mot=mot,
                    fast_share=np.zeros(self.n), _X=None, _T=None)

    def assess(self, t, wobble=True):
        self.e.features = lambda now: self.features(wobble)
        return self.e.assess(t)

    def test_range_is_the_bin_that_fired(self):
        self.assertAlmostEqual(self.assess(1.0)["range_m"], self.rng[self.far])

    def test_hold_over_seconds_keep_the_last_fired_bin(self):
        self.assess(1.0)
        self.assertAlmostEqual(self.assess(2.0, wobble=False)["range_m"], self.rng[self.far])


if __name__ == "__main__":
    unittest.main()
