"""Unit tests for scripts/ruview_json.py (standard library only).

Run: python3 -m unittest discover -s wifi-sensing/tests -p 'test_*.py'
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import ruview_json as rj  # noqa: E402


def node(seq, csi_status="active", **extra):
    n = {
        "node_id": 1,
        "status": "active",
        "csi_status": csi_status,
        "csi_last_seen_ms": 90,
        "csi_sequence": seq,
        "rssi_dbm": -81.0,
        "motion_level": "present_moving",
        "person_count": 1,
        "person_count_valid": True,
        "position_configured": False,
    }
    n.update(extra)
    return n


def doc(*nodes):
    return {"nodes": list(nodes), "total": len(nodes)}


class FormatTests(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(rj.fmt(None), "null")
        self.assertEqual(rj.fmt(True), "true")
        self.assertEqual(rj.fmt(-81.0), "-81.0")
        self.assertEqual(rj.fmt("present_moving"), "present_moving")

    def test_unexpected_strings_are_masked(self):
        self.assertEqual(rj.fmt("has space"), "<unexpected>")
        self.assertEqual(rj.fmt("x" * 80), "<unexpected>")
        self.assertEqual(rj.fmt({"nested": 1}), "<unexpected>")


class HealthTests(unittest.TestCase):
    def test_source_states(self):
        # "esp32" only says what the server is configured for; it is not liveness.
        self.assertEqual(rj.classify_source("esp32"), "configured")
        self.assertEqual(rj.classify_source("esp32:offline"), "offline")
        self.assertEqual(rj.classify_source("simulated"), "simulated")
        self.assertEqual(rj.classify_source("auto"), "other")
        self.assertEqual(rj.classify_source(None), "unknown")

    @staticmethod
    def health(datagrams, age):
        return {"status": "ok", "source": "esp32", "tick": 5,
                "processing": {"state": "ok", "udp": {"datagrams": datagrams, "dropped_by_allowlist": 0,
                                                      "last_datagram_age_ms": age}}}

    def test_live_needs_a_recent_datagram(self):
        self.assertEqual(rj.source_state(self.health(81234, 12)), "live")
        self.assertEqual(rj.source_state(self.health(81234, 5000)), "live")
        self.assertEqual(rj.source_state(self.health(81234, 5001)), "waiting")

    def test_regression_esp32_with_zero_datagrams_is_not_live(self):
        # Observed on the real Pi, 2026-11-07: source=esp32, 0 datagrams, no nodes.
        doc = {"clients": 0, "processing": {"last_update_age_ms": None, "state": "idle",
               "udp": {"datagrams": 0, "dropped_by_allowlist": 0, "last_datagram_age_ms": None}},
               "source": "esp32", "status": "ok", "tick": 0}
        self.assertEqual(rj.source_state(doc), "waiting")
        lines = rj.summarise_health(doc)
        self.assertIn("source_state=waiting", lines)
        self.assertIn("udp_datagrams=0", lines)
        self.assertIn("udp_last_datagram_age_ms=null", lines)

    def test_no_udp_counters_is_only_configured(self):
        self.assertEqual(rj.source_state({"status": "ok", "source": "esp32"}), "configured")
        self.assertEqual(rj.source_state({"source": "esp32", "processing": {"state": "ok"}}), "configured")

    def test_odd_age_values_are_not_live(self):
        for age in (True, "12", -1, None, [12]):
            self.assertEqual(rj.source_state(self.health(5, age)), "waiting", age)

    def test_summary_uses_allowlist(self):
        doc = self.health(7, 30)
        doc["secret"] = "abc"
        doc["processing"]["udp"]["peer"] = "aa:bb:cc:dd:ee:ff"
        lines = rj.summarise_health(doc)
        self.assertIn("source_state=live", lines)
        self.assertNotIn("abc", "\n".join(lines))
        self.assertNotIn("aa:bb", "\n".join(lines))

    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            rj.summarise_health([1, 2])


class NodesTests(unittest.TestCase):
    def test_unknown_fields_never_printed(self):
        lines = rj.summarise_nodes(doc(node(5, mac="aa:bb:cc:dd:ee:ff", device_id="a9be7e5bb1644d3a")))
        text = "\n".join(lines)
        self.assertIn("total=1", text)
        self.assertIn("csi_sequence=5", text)
        self.assertNotIn("aa:bb", text)
        self.assertNotIn("a9be7e", text)

    def test_missing_fields_are_null(self):
        lines = rj.summarise_nodes({"nodes": [{"node_id": 3}]})
        self.assertIn("csi_sequence=null", lines[1])

    def test_rejects_missing_nodes_list(self):
        with self.assertRaises(ValueError):
            rj.summarise_nodes({"total": 0})


class CompareTests(unittest.TestCase):
    def test_advancing_is_verified(self):
        lines, ok = rj.compare(doc(node(58300)), doc(node(58462)), 5)
        self.assertTrue(ok)
        self.assertIn("sequence=advancing seq_delta=162 seq_rate_hz=32.4", lines[0])

    def test_stalled_is_not_verified(self):
        lines, ok = rj.compare(doc(node(10)), doc(node(10)), 5)
        self.assertFalse(ok)
        self.assertIn("sequence=stalled", lines[0])

    def test_reset_is_not_verified(self):
        lines, ok = rj.compare(doc(node(900)), doc(node(3)), 5)
        self.assertFalse(ok)
        self.assertIn("sequence=reset", lines[0])

    def test_advancing_but_csi_stale_is_not_verified(self):
        _, ok = rj.compare(doc(node(1, "stale")), doc(node(50, "stale")), 5)
        self.assertFalse(ok)

    def test_new_node_in_second_sample(self):
        lines, ok = rj.compare(doc(), doc(node(7)), 5)
        self.assertFalse(ok)
        self.assertIn("sequence=unknown", lines[0])

    def test_no_nodes(self):
        lines, ok = rj.compare(doc(), doc(), 5)
        self.assertFalse(ok)
        self.assertEqual(lines, ["no nodes reported"])

    def test_boolean_sequence_is_not_a_number(self):
        _, ok = rj.compare(doc(node(True)), doc(node(5)), 5)
        self.assertFalse(ok)


class CliTests(unittest.TestCase):
    def run_main(self, argv, stdin_text=""):
        out, errs = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
        try:
            with redirect_stdout(out), redirect_stderr(errs):
                rc = rj.main(argv)
        finally:
            sys.stdin = old_stdin
        return rc, out.getvalue(), errs.getvalue()

    def test_invalid_json(self):
        rc, _, errs = self.run_main(["x", "nodes"], "<html>401</html>")
        self.assertEqual(rc, 1)
        self.assertIn("not valid JSON", errs)

    def test_usage(self):
        rc, _, _ = self.run_main(["x"])
        self.assertEqual(rc, 2)
        rc, _, _ = self.run_main(["x", "bogus"])
        self.assertEqual(rc, 2)

    def test_compare_files(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
            with open(a, "w", encoding="utf-8") as f:
                json.dump(doc(node(1)), f)
            with open(b, "w", encoding="utf-8") as f:
                json.dump(doc(node(31)), f)
            rc, out, _ = self.run_main(["x", "compare", a, b, "1"])
        self.assertEqual(rc, 0)
        self.assertIn("verified=yes", out)


if __name__ == "__main__":
    unittest.main()
