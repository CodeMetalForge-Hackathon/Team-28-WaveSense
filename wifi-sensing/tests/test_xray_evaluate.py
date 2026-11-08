"""evaluate.py on SYNTHETIC recordings (code behaviour only, not real-world accuracy)."""
import os, sys, tempfile, unittest

try:
    import numpy as np  # noqa: F401
except ImportError:
    raise unittest.SkipTest("numpy not installed")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from xray import evaluate  # noqa: E402
from xray.csi_packet import build_frame  # noqa: E402
from xray.receiver import Recorder  # noqa: E402
from synth_csi import Scene  # noqa: E402


def record(folder, label, scene, t0, t1):
    r = Recorder(folder, label)
    for t in scene.times(t0, t1):
        r.write(t, build_frame(scene.csi(t)))
    r.f.close()
    return r.path


class Evaluate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        s = Scene(seed=7)
        for _ in range(4):
            s.add("far_walker")
        cls.cal_file = record(cls.dir, "empty-crowd-cal", s, 0, 150)
        cls.empty = record(cls.dir, "empty-crowd-test", s, 150, 240)
        s.add("near_still", start=240, stop=340)
        cls.one = record(cls.dir, "one-seated", s, 240, 340)

    def test_calibrate_then_score_held_out(self):
        cal = evaluate.calibrate(self.cal_file)
        self.assertGreaterEqual(cal.windows, 100)
        rows = [evaluate.score(f, cal) for f in (self.empty, self.one)]
        self.assertEqual([r["label"] for r in rows], ["empty", "one"])
        self.assertEqual(rows[0]["candidate_pct"], 0.0)
        self.assertGreater(rows[1]["candidate_pct"], 80)
        s = evaluate.summarise(rows)
        self.assertEqual(s["false_candidate_pct_empty"], 0.0)
        self.assertGreater(s["detection_pct_one"], 80)

    def test_refuses_to_score_its_own_calibration_file(self):
        with self.assertRaises(SystemExit):
            evaluate.main(["--calibrate", self.cal_file, self.cal_file])

    def test_short_recording_cannot_calibrate(self):
        s = Scene(seed=8)
        short = record(self.dir, "empty-short", s, 0, 20)
        with self.assertRaises(SystemExit):
            evaluate.calibrate(short)


if __name__ == "__main__":
    unittest.main()
