"""wifi-xray unit tests. All CSI here is SYNTHETIC (tests/synth_csi.py): these tests
show the code behaves as designed; they say nothing about real-world accuracy.

  python3 -m unittest discover -s wifi-sensing/tests -p 'test_xray*.py' -v
"""
import os, sys, unittest

try:
    import numpy as np
except ImportError:                     # the shell tooling stays stdlib-only
    raise unittest.SkipTest("numpy not installed: sudo apt install python3-numpy")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # wifi-sensing/ -> import xray
sys.path.insert(0, HERE)
from xray import dsp  # noqa: E402
from xray.csi_packet import build_frame, parse_frame  # noqa: E402
from xray.engine import Calibration, NearZoneEngine  # noqa: E402
from synth_csi import Scene  # noqa: E402


class Packets(unittest.TestCase):
    def test_round_trip(self):
        csi = np.array([[3 + 4j, -128 + 127j, 0j, 10 - 5j]])
        f = parse_frame(build_frame(csi, node_id=7, sequence=0xFFFFFFFF, rssi=-61))
        self.assertEqual((f.node_id, f.n_antennas, f.n_subcarriers, f.sequence, f.rssi), (7, 1, 4, 0xFFFFFFFF, -61))
        np.testing.assert_allclose(f.csi, csi)
        np.testing.assert_allclose(f.amplitude, np.abs(csi[0]), rtol=1e-6)

    def test_positive_rssi_is_negated_like_the_server(self):
        self.assertEqual(parse_frame(build_frame(np.ones((1, 4)), rssi=45)).rssi, -45)

    def test_rejects_garbage(self):
        good = build_frame(np.ones((1, 8)))
        self.assertIsNone(parse_frame(b""))
        self.assertIsNone(parse_frame(good[:-1]))                          # truncated payload
        self.assertIsNone(parse_frame(b"\x02\x00\x11\xc5" + good[4:]))     # vitals magic, not CSI
        self.assertIsNone(parse_frame(good[:6] + b"\x00\x00" + good[8:]))  # zero subcarriers


class Dsp(unittest.TestCase):
    def test_normalise_cancels_gain(self):
        a = np.abs(np.random.default_rng(1).standard_normal((50, 20))) + 1
        np.testing.assert_allclose(dsp.normalise(a), dsp.normalise(3.7 * a))

    def test_hampel_removes_spikes_only(self):
        x = np.sin(np.linspace(0, 6, 200))[:, None] * np.ones((1, 3))
        y = x.copy(); y[100, 1] += 50
        z = dsp.hampel(y)
        self.assertLess(abs(z[100, 1] - x[100, 1]), 0.1)
        np.testing.assert_allclose(z[:, 0], x[:, 0], atol=1e-9)

    def test_resample_coverage_sees_gaps(self):
        t = np.concatenate([np.arange(0, 10, 0.05), np.arange(20, 30, 0.05)])
        _, cov = dsp.resample(t, np.ones((len(t), 2)), 30.0, 30.0, 10.0)
        self.assertAlmostEqual(cov, 2 / 3, delta=0.05)

    def test_breathing_finds_the_rate_and_rejects_noise(self):
        rng = np.random.default_rng(2)
        t = np.arange(300) / 10.0
        sig = 0.02 * np.sin(2 * np.pi * 0.25 * t)[:, None] * rng.uniform(0.5, 1.5, (1, 30))
        b = dsp.breathing(1 + sig + 0.003 * rng.standard_normal((300, 30)), 10.0)
        self.assertAlmostEqual(b["bpm"], 15, delta=2.5)
        self.assertGreater(b["snr"], 20)
        self.assertGreaterEqual(b["consistency"], 0.8)
        n = dsp.breathing(1 + 0.003 * rng.standard_normal((300, 30)), 10.0)
        self.assertLess(n["snr"], 30)          # engine floor; noise measured median 11, max 28 (200 trials)


def run(scene, engine, t0, t1, step=1.0):
    """Feed synthetic frames from t0 to t1, assess once per `step`; return the reports."""
    reports, next_assess = [], t0 + step
    for t in scene.times(t0, t1):
        engine.feed(parse_frame(build_frame(scene.csi(t))), t)
        if t >= next_assess:
            reports.append(engine.assess(t))
            next_assess += step
    return reports


class EngineSynthetic(unittest.TestCase):
    """Crowd = 4 far walkers, always present. Near person = still, breathing at 15/min."""

    @classmethod
    def setUpClass(cls):
        cls.scene = Scene(seed=42)
        for _ in range(4):
            cls.scene.add("far_walker")
        cls.engine = NearZoneEngine()
        run(cls.scene, cls.engine, 0, 30)                  # fill the 30 s window
        cls.engine.start_calibration()
        run(cls.scene, cls.engine, 30, 150)                # 120 s empty near zone, crowd moving
        cls.cal = cls.engine.finish_calibration()
        cls.engine.cal = cls.cal

    def test_1_calibration_is_sane(self):
        self.assertGreaterEqual(self.cal.windows, 100)
        self.assertGreater(self.cal.rate_hz, 15)
        self.assertEqual(sum(self.cal.mask), 52)            # 52 bins carry signal in this synthetic layout

    def test_2_crowd_alone_gives_no_candidate(self):
        reps = run(self.scene, self.engine, 150, 210)
        states = [r["state"] for r in reps]
        self.assertNotIn("candidate_present", states, states)

    def test_3_still_breathing_person_nearby_is_a_candidate(self):
        self.scene.add("near_still", start=210, stop=300)
        reps = run(self.scene, self.engine, 210, 300)
        first = next((r["t"] for r in reps if r["state"] == "candidate_present"), None)
        self.assertIsNotNone(first, [r.get("profile_distance") for r in reps])
        self.assertLess(first - 210, 20)                   # within 20 s of sitting down
        late = [r for r in reps if r["t"] > 260]
        self.assertTrue(all(r["state"] == "candidate_present" for r in late))
        self.assertTrue(any(r["evidence"]["breathing"] for r in late))
        bpm = [r["breathing_bpm"] for r in late if r["evidence"]["breathing"]]
        self.assertAlmostEqual(float(np.median(bpm)), 15, delta=2.5)

    def test_4_person_leaves_and_it_clears(self):
        reps = run(self.scene, self.engine, 300, 380)
        self.assertEqual(reps[-1]["state"], "no_candidate", [r["state"] for r in reps][-20:])

    def test_5_calibration_round_trips_through_json(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "cal.json")
        self.cal.save(path)
        self.assertEqual(Calibration.load(path), self.cal)


class EngineEdges(unittest.TestCase):
    def test_uncalibrated_and_sparse(self):
        s = Scene(seed=3)
        e = NearZoneEngine()
        reps = run(s, e, 0, 40)
        self.assertEqual(reps[-1]["state"], "uncalibrated")
        e2 = NearZoneEngine()
        sparse = []
        for t in s.times(0, 40, rate=2.0):
            e2.feed(parse_frame(build_frame(s.csi(t))), t)
        sparse.append(e2.assess(40.0))
        self.assertIn(sparse[-1]["state"], ("poor_link", "no_data"))

    def test_rssi_gate_drops_a_distant_transmitter_but_follows_a_real_drop(self):
        e = NearZoneEngine()
        strong = build_frame(np.full((1, 16), 30 + 0j), rssi=-68)
        weak = build_frame(np.full((1, 16), 5 + 0j), rssi=-92)             # another AP far away
        t = 0.0
        for i in range(300):
            t += 0.03
            e.feed(parse_frame(weak if i % 50 == 49 else strong), t)
        self.assertEqual(e.gated, 6)                                         # every weak frame, no strong one
        self.assertEqual(len(e.buf), 294)
        dropped = build_frame(np.full((1, 16), 12 + 0j), rssi=-86)         # body blocking the link: 18 dB lasting drop
        before = e.gated
        for _ in range(300):
            t += 0.03
            e.feed(parse_frame(dropped), t)
        self.assertLess(e.gated - before, 120)                               # the median follows; frames flow again
        self.assertEqual(e.buf[-1][1][0], 12.0)

    def test_too_short_calibration_is_refused(self):
        s = Scene(seed=4)
        e = NearZoneEngine()
        run(s, e, 0, 30)
        e.start_calibration()
        run(s, e, 30, 40)
        with self.assertRaises(ValueError):
            e.finish_calibration()


if __name__ == "__main__":
    unittest.main()
