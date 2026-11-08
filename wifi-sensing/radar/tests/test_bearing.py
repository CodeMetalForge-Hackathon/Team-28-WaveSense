"""Synthetic geometry checks for radar_bearing. These prove the maths, not real-world accuracy."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
try:
    import numpy as np
    from radar_bearing import RX_SPACING_M, estimate_bearing
except ImportError:
    np = None

WAVELENGTH = 0.012426


def steering(theta):
    return np.exp(2j * np.pi * np.arange(4) * RX_SPACING_M * np.sin(np.deg2rad(theta)) / WAVELENGTH)


def scene(rng, movers, clutter=(), noise=1.0, errors=np.zeros(4), samples=250, bins=3):
    """Slow-time samples (time, bins, rx): movers are (angle, amplitude), clutter is static."""
    x = noise * (rng.normal(size=(samples, bins, 4)) + 1j * rng.normal(size=(samples, bins, 4)))
    for angle, amplitude in movers:
        wobble = np.exp(1j * rng.uniform(0, 2 * np.pi, samples))[:, None, None]
        x = x + amplitude * wobble * steering(angle)[None, None, :]
    for angle, amplitude in clutter:
        x = x + amplitude * steering(angle)[None, None, :]
    return x * np.exp(1j * errors)[None, None, :]


@unittest.skipIf(np is None, "Needs numpy / Pi venv")
class BearingTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(7)

    def test_recovers_known_angles(self):
        for angle in (-40, -15, 0, 22, 45):
            result = estimate_bearing(scene(self.rng, [(angle, 50)]), np.ones(4), WAVELENGTH)
            self.assertAlmostEqual(result["bearing_deg"], angle, delta=2)

    def test_static_clutter_much_stronger_than_the_mover_is_ignored(self):
        x = scene(self.rng, [(20, 50)], clutter=[(-35, 5000)])
        self.assertAlmostEqual(estimate_bearing(x, np.ones(4), WAVELENGTH)["bearing_deg"], 20, delta=2)

    def test_factory_correction_removes_channel_phase_errors(self):
        errors = np.array([.7, -.8, .4, 1.3])
        x = scene(self.rng, [(-25, 50)], errors=errors)
        self.assertAlmostEqual(estimate_bearing(x, np.exp(-1j * errors), WAVELENGTH)["bearing_deg"], -25, delta=2)

    def test_no_correction_means_no_direction(self):
        self.assertIsNone(estimate_bearing(scene(self.rng, [(10, 50)]), None, WAVELENGTH))

    def test_noise_alone_gives_no_direction(self):
        self.assertIsNone(estimate_bearing(scene(self.rng, []), np.ones(4), WAVELENGTH))

    def test_two_equal_movers_in_one_range_bin_give_no_direction(self):
        self.assertIsNone(estimate_bearing(scene(self.rng, [(-40, 50), (35, 50)]), np.ones(4), WAVELENGTH))

    def test_malformed_input(self):
        for bad in (np.ones((10, 3, 2)), np.ones((2, 3, 4)), np.full((10, 3, 4), np.nan)):
            self.assertIsNone(estimate_bearing(bad, np.ones(4), WAVELENGTH))

    def test_silence_gives_no_direction(self):
        self.assertIsNone(estimate_bearing(np.zeros((50, 3, 4)), np.ones(4), WAVELENGTH))


@unittest.skipIf(np is None, "Needs numpy / Pi venv")
class ServiceBearingTests(unittest.TestCase):
    """The service's choice of range; the engine is a stand-in with known per-range signals."""

    def service(self, signals, strongest=None, fail=False):
        from radar_service import RadarService
        service = RadarService.__new__(RadarService)
        service.dev = types.SimpleNamespace(rx_calibration=np.ones(4), wavelength=WAVELENGTH)
        service.bearing_error = None

        def slow_time(now, seconds, range_m):
            if fail:
                raise RuntimeError("buffer")
            return signals.get(range_m)
        service.eng = types.SimpleNamespace(slow_time=slow_time, strongest_range=lambda *a: strongest)
        return service

    def setUp(self):
        rng = np.random.default_rng(3)
        self.person = scene(rng, [(-7, 50)])
        self.echo = scene(rng, [])

    @staticmethod
    def report(range_m=2.4):
        return dict(state="candidate_present", kind="still", t=100.0, range_m=range_m, candidate_ranges_m=[1.2, 2.4])

    def test_strongest_bin_with_a_direction_is_kept(self):
        report = self.report(1.2)
        self.service({1.2: self.person}, strongest=2.4).add_bearing(report)
        self.assertAlmostEqual(report["bearing_deg"], -7, delta=2)
        self.assertNotIn("track_range_m", report)

    def test_echo_without_direction_falls_back_to_the_stronger_mover(self):
        report = self.report()
        self.service({2.4: self.echo, 1.2: self.person}, strongest=1.2).add_bearing(report)
        self.assertAlmostEqual(report["bearing_deg"], -7, delta=2)
        self.assertEqual(report["track_range_m"], 1.2)
        self.assertEqual(report["range_m"], 2.4)
        from radar_service import contract_payload
        track = contract_payload(report, 100.0)["tracks"][0]
        self.assertEqual(track["range_m"], 1.2)
        self.assertAlmostEqual(track["bearing_deg"], -7, delta=2)

    def test_no_direction_anywhere_keeps_range_only(self):
        report = self.report()
        self.service({2.4: self.echo, 1.2: self.echo}, strongest=1.2).add_bearing(report)
        self.assertNotIn("bearing_deg", report)
        self.assertNotIn("track_range_m", report)

    def test_failure_never_costs_the_detection(self):
        report = self.report()
        service = self.service({}, fail=True)
        service.add_bearing(report)
        self.assertEqual(service.bearing_error, "RuntimeError")
        self.assertEqual(report["range_m"], 2.4)
        self.assertNotIn("bearing_deg", report)


@unittest.skipIf(np is None, "Needs numpy / Pi venv")
class PlatformMovedTests(unittest.TestCase):
    def service(self):
        import threading
        from radar_service import RadarService
        service = RadarService.__new__(RadarService)
        service.lock = threading.Lock()
        service.settle_until = 0.0
        service.resets = 0
        def reset():
            service.resets += 1
        service.eng = types.SimpleNamespace(buf=[1, 2], _acc=[1], _acc_t=[1], reset_background=reset)
        return service

    def test_motion_is_forgotten_and_the_scene_is_relearned(self):
        service = self.service()
        self.assertTrue(service.platform_moved()["ok"])
        self.assertEqual((service.eng.buf, service.eng._acc, service.eng._acc_t, service.resets), ([], [], [], 1))

    def test_no_candidate_is_reported_while_settling(self):
        from radar_service import contract_payload
        service = self.service()
        service.settle_until = 110.0
        report = service.settled(dict(state="candidate_present", range_m=1.2, kind="moving", t=105.0))
        payload = contract_payload(report, 105.0)
        self.assertEqual((payload["state"], payload["tracks"], payload["online"]), ("not_judging", [], True))

    def test_judging_resumes_after_settling(self):
        service = self.service()
        service.settle_until = 110.0
        report = dict(state="candidate_present", range_m=1.2, kind="moving", t=111.0)
        self.assertIs(service.settled(report), report)


if __name__ == "__main__":
    unittest.main()
