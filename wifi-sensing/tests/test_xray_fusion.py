"""Zone fusion across nodes (pure logic, no CSI)."""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from xray.fusion import fuse  # noqa: E402

C, N = {"state": "candidate_present"}, {"state": "no_candidate"}


class Fusion(unittest.TestCase):
    def test_nothing_judging(self):
        z = fuse({1: {"state": "uncalibrated"}, 2: {"state": "no_data"}})
        self.assertEqual(z["state"], "not_judging")
        self.assertEqual(z["links_not_judging"], {1: "uncalibrated", 2: "no_data"})
        self.assertEqual(fuse({})["state"], "not_judging")

    def test_single_link_passes_through_and_says_so(self):
        self.assertEqual(fuse({1: C})["state"], "candidate_present")
        z = fuse({1: N, 2: {"state": "poor_link"}})
        self.assertEqual((z["state"], z["links_judging"]), ("no_candidate", [1]))
        self.assertIn("single link", z["basis"])

    def test_two_links_must_agree(self):
        self.assertEqual(fuse({1: C, 2: C})["state"], "candidate_present")
        z = fuse({1: C, 2: N})
        self.assertEqual((z["state"], z["candidate_links"]), ("weak_candidate", [1]))   # shown, not hidden
        self.assertEqual(fuse({1: N, 2: N})["state"], "no_candidate")
        self.assertEqual(fuse({1: N, 2: C, 3: C})["state"], "candidate_present")

    def test_no_candidate_never_claims_absence(self):
        for r in ({1: N}, {1: N, 2: N}):
            self.assertIn("NOT evidence that nobody is there", fuse(r)["label"])
        self.assertIn("verify", fuse({1: C, 2: C})["label"])


if __name__ == "__main__":
    unittest.main()
