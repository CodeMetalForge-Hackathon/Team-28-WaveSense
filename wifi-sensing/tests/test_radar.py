"""Radar engine and radar+Wi-Fi combination on SYNTHETIC FMCW frames: these tests show the code
behaves as designed, not that it is accurate (see radar/README.md for measured results)."""
import os, sys, unittest

try:
    import numpy as np
except ImportError:
    raise unittest.SkipTest("numpy not installed")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "radar"))
sys.path.insert(0, os.path.dirname(HERE))
from radar_engine import RadarEngine  # noqa: E402
from tinyrad_dev import TinyRadDevice  # noqa: E402
from xray.fusion import combine  # noqa: E402

DEV = TinyRadDevice()
SLOPE = DEV.bandwidth / (DEV.n * 1e-6)
N, FS_ADC, RATE = DEV.n, 1e6, 25.0


def frame(t, rng, person_m=None, breath_hz=0.25, amp=40.0, noise=3.0):
    n = np.arange(N)[:, None]
    rx = np.arange(4)[None, :]
    def target(r, a, extra_phase=0.0):
        fb = 2 * r * SLOPE / 299_792_458.0
        return a * np.cos(2 * np.pi * fb * n / FS_ADC + 4 * np.pi * r / DEV.wavelength + extra_phase + 0.7 * rx)
    x = target(3.0, 300.0)                                         # a wall: strong and static
    if person_m is not None:
        d = 0.005 * np.sin(2 * np.pi * breath_hz * t)              # 5 mm chest motion
        x = x + target(person_m + d, amp, 4 * np.pi * d / DEV.wavelength)
    return (x + rng.normal(0, noise, x.shape)).astype(np.int16)


def run(eng, rng, t0, t1, noise_fn=None, **kw):
    reps = []
    for k in range(int((t1 - t0) * RATE)):
        t = t0 + k / RATE
        if noise_fn is not None:
            kw["noise"] = noise_fn(t)
        eng.feed(t, frame(t, rng, **kw))
        if k % int(RATE) == 0:
            reps.append(eng.assess(t))
    return reps


class RadarSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rng = np.random.default_rng(3)
        eng = RadarEngine(DEV.range_axis(RadarEngine.NFFT))
        eng.DECIM = 1                                              # feed slow-time samples directly
        run(eng, cls.rng, 0, 20)
        eng.start_calibration()
        run(eng, cls.rng, 20, 80)
        cls.cal = eng.finish_calibration()
        cls.eng = eng
        eng.cal = cls.cal

    def test_1_empty_room_with_a_wall_gives_no_candidate(self):
        reps = run(self.eng, self.rng, 80, 120)
        self.assertNotIn("candidate_present", [r["state"] for r in reps])

    def test_2_still_breathing_person_is_found_at_its_range(self):
        reps = run(self.eng, self.rng, 120, 170, person_m=1.2)
        cand = [r for r in reps if r["state"] == "candidate_present"]
        self.assertGreater(len(cand), 0.7 * len(reps))
        self.assertAlmostEqual(float(np.median([r["range_m"] for r in cand])), 1.2, delta=0.35)
        late = [r for r in cand if r["t"] > 145]
        self.assertTrue(any(r["kind"] == "still, breathing-like" for r in late), [r["kind"] for r in late])
        bpm = [r["breathing_bpm"] for r in late if r["breathing_bpm"]]
        self.assertAlmostEqual(float(np.median(bpm)), 15, delta=3.5)

    def test_3_person_beyond_the_near_range_is_not_a_near_candidate(self):
        reps = run(self.eng, self.rng, 170, 210, person_m=3.6)
        self.assertEqual(reps[-1]["state"], "no_candidate")
        self.assertTrue(any(reps[-1]["beyond_near_ranges_m"]), reps[-1])

    def test_4_it_clears_after_the_person_leaves(self):
        run(self.eng, self.rng, 210, 240, person_m=1.0)
        reps = run(self.eng, self.rng, 240, 270)
        first_clear = next(r["t"] - 240 for r in reps if r["state"] == "no_candidate")
        self.assertLess(first_clear, 20)
        self.assertEqual(reps[-1]["state"], "no_candidate")


class RadarAdaptive(unittest.TestCase):
    """No calibration step: the background is learned and tracked (SYNTHETIC frames)."""

    def engine(self):
        e = RadarEngine(DEV.range_axis(RadarEngine.NFFT))          # no calibration -> adaptive
        e.DECIM = 1
        return e

    def test_learns_then_judges_and_tracks_slow_drift(self):
        rng, e = np.random.default_rng(5), self.engine()
        self.assertTrue(e.adaptive)
        first = run(e, rng, 0, 40)
        self.assertIn("learning", [r["state"] for r in first])
        self.assertEqual(first[-1]["state"], "no_candidate")
        drift = run(e, rng, 40, 220, noise_fn=lambda t: 3.0 * (1 + 2 * (t - 40) / 180))   # noise x3 over 3 min
        self.assertNotIn("candidate_present", [r["state"] for r in drift])

    def test_still_person_found_kept_for_10_min_then_cleared(self):
        rng, e = np.random.default_rng(6), self.engine()
        run(e, rng, 0, 40)
        seated = run(e, rng, 40, 640, person_m=1.2)
        first = next(r["t"] - 40 for r in seated if r["state"] == "candidate_present")
        self.assertLess(first, 15)
        self.assertTrue(all(r["state"] == "candidate_present" for r in seated if r["t"] > 40 + first + 5))
        after = run(e, rng, 640, 700)
        self.assertEqual(after[-1]["state"], "no_candidate")
        self.assertLess(next(r["t"] - 640 for r in after if r["state"] == "no_candidate"), 30)

    def test_person_present_at_power_on_is_not_learned_away(self):
        rng, e = np.random.default_rng(8), self.engine()
        self.assertIsNotNone(e.floor, "device_floor.json missing")
        # the synthetic radar's noise floor differs from the real unit's, so scale the floor to it
        cal = RadarEngine(DEV.range_axis(RadarEngine.NFFT)); cal.DECIM = 1
        run(cal, rng, 0, 30)
        e.floor = np.median([cal.features(29.9)["pres"]], axis=0)
        reps = run(e, rng, 0, 120, person_m=1.0, amp=60.0)
        first = next((r["t"] for r in reps if r["state"] == "candidate_present"), None)
        self.assertIsNotNone(first, [r["state"] for r in reps])
        self.assertLess(first, 35)
        self.assertTrue(all(r["state"] == "candidate_present" for r in reps if r["t"] > first + 5), [r["state"] for r in reps])

    def test_relearn(self):
        rng, e = np.random.default_rng(7), self.engine()
        run(e, rng, 0, 40)
        e.reset_background()
        self.assertEqual(run(e, rng, 40, 42)[-1]["state"], "learning")


class Contract(unittest.TestCase):
    """The WaveSense phone app's radar file (schema 1)."""

    def payload(self, **report):
        from radar_service import contract_payload
        return contract_payload(report, 1794120000.5)

    def test_state_mapping(self):
        p = self.payload(state="candidate_present", kind="still, micro-motion", range_m=1.5)
        self.assertEqual((p["schema"], p["timestamp_ms"], p["online"], p["state"]), (1, 1794120000500, True, "candidate"))
        self.assertEqual(p["tracks"], [{"range_m": 1.5, "bearing_deg": None}])         # no angle: null
        self.assertEqual(self.payload(state="candidate_present", kind="moving", range_m=0.9)["state"], "motion")
        self.assertEqual(self.payload(state="no_candidate", range_m=1.8), {"schema": 1, "timestamp_ms": 1794120000500,
                                                                          "online": True, "state": "clear", "tracks": []})
        for st in ("calibrating", "uncalibrated", "poor_link"):
            self.assertEqual(self.payload(state=st)["state"], "not_judging")
        self.assertFalse(self.payload(state="no_data")["online"])

    def test_atomic_write_is_read_by_the_wavesense_app(self):
        import tempfile, time as _t
        from radar_service import contract_payload, write_atomic
        d = tempfile.mkdtemp(); path = os.path.join(d, "status.json")
        write_atomic(path, contract_payload(dict(state="candidate_present", kind="still", range_m=1.2), _t.time()))
        self.assertEqual(os.listdir(d), ["status.json"])                                 # no temp file left behind
        app = "/srv/team28/wavesense-mobile"
        if not os.path.exists(os.path.join(app, "radar_status.py")):
            self.skipTest("WaveSense app not on this machine")
        sys.path.insert(0, app)
        from radar_status import read_radar                                              # the app's own reader
        r = read_radar(path)
        self.assertEqual((r["online"], r["state"], r["tracks"]), (True, "candidate", [{"range_m": 1.2, "bearing_deg": None, "candidate": True}]))


class Combine(unittest.TestCase):
    W = lambda self, s: dict(state=s, basis="", label="")

    def test_combination(self):
        r_c = dict(state="candidate_present", kind="still, breathing-like", range_m=1.1)
        self.assertEqual(combine(self.W("no_candidate"), r_c)["state"], "candidate_present")
        self.assertIn("1.1 m", combine(self.W("no_candidate"), r_c)["basis"])
        self.assertEqual(combine(self.W("candidate_present"), dict(state="no_candidate"))["state"], "weak_candidate")
        z = combine(self.W("no_candidate"), dict(state="no_candidate"))
        self.assertEqual(z["state"], "no_candidate")
        self.assertIn("NOT evidence", z["label"])
        self.assertEqual(combine(self.W("changed"), None)["state"], "changed")      # no radar: Wi-Fi alone
        self.assertEqual(combine(self.W("no_candidate"), dict(state="uncalibrated"))["sources"]["radar"], "uncalibrated")


if __name__ == "__main__":
    unittest.main()
