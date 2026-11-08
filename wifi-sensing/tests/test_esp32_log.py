"""esp32_log.py: parsing and masking on canned console text (no hardware)."""
import io, os, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import esp32_log  # noqa: E402

# Shaped like the real node-1 log of 2026-11-07, with fake addresses and values.
NOT_CONNECTED = """\
W (1202741) wifi:Haven't to connect to a suitable AP now!
W (1202781) stream_sender: sendto failed: errno 118
W (1281421) main: WiFi disconnected, reason=201 rssi=-128
I (1281421) main: Reconnecting in 30000 ms (attempt 45)
I (1289721) csi_collector: CSI cb #4500: len=106 rssi=-93 ch=1
I (1302211) csi_collector: CSI cb #4600: len=106 rssi=-94 ch=1
W (1313841) main: WiFi disconnected, reason=201 rssi=-128
I (1313841) main: Reconnecting in 30000 ms (attempt 46)
I (1316121) csi_collector: CSI cb #4700: len=106 rssi=-92 ch=1
I (1316200) main: peer 12:34:56:78:9a:bc password=hunter2-not-real key: abc123
"""

CONNECTED = """\
\x1b[0;33mW (5000) main: WiFi disconnected, reason=15 rssi=-60\x1b[0m
I (9000) main: Got IP: 10.0.0.23
I (9100) csi_collector: Auto-detected AP channel: 6
I (9500) csi_collector: CSI cb #100: len=106 rssi=-41 ch=6
"""


class Esp32Log(unittest.TestCase):
    def test_not_connected_summary(self):
        s = esp32_log.summarise(NOT_CONNECTED)
        self.assertEqual((s["disconnects"], s["last_reason"], s["last_attempt"], s["got_ip"]), (2, 201, 46, False))
        self.assertEqual((s["csi_channels"], min(s["csi_rssi"]), max(s["csi_rssi"])), ({1}, -94, -92))
        self.assertEqual(s["uptime_ms"], 1316200)
        text = "\n".join(esp32_log.report(s))
        self.assertIn("NOT CONNECTED", text)
        self.assertIn("NO_AP_FOUND", text)
        self.assertIn("attempt 46", text)
        self.assertIn("antenna", text)                       # all frames <= -85 dBm
        self.assertIn("says nothing about the hotspot's channel", text)

    def test_connected_supersedes_earlier_failure(self):
        s = esp32_log.summarise(CONNECTED)
        self.assertTrue(s["got_ip"])
        self.assertIsNone(s["last_reason"])
        text = "\n".join(esp32_log.report(s))
        self.assertIn("CONNECTED", text)
        self.assertNotIn("NOT CONNECTED", text)
        self.assertNotIn("antenna", text)

    def test_masks_macs_and_secrets(self):
        m = esp32_log.mask(NOT_CONNECTED)
        self.assertNotIn("12:34:56:78:9a:bc", m)
        self.assertNotIn("hunter2", m)
        self.assertNotIn("abc123", m)
        self.assertIn("XX:XX:XX:XX:XX:XX", m)
        self.assertIn("password=***", m)

    def test_file_mode_output_is_masked(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(NOT_CONNECTED)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(esp32_log.main(["--file", f.name, "--lines", "50"]), 0)
        os.unlink(f.name)
        self.assertIn("NO_AP_FOUND", out.getvalue())
        self.assertNotIn("hunter2", out.getvalue())
        self.assertNotIn("12:34:56:78:9a:bc", out.getvalue())
        self.assertNotIn("sendto failed: errno", out.getvalue())     # noise lines are filtered

    def test_refuses_non_acm_ports_by_default(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(esp32_log.main(["--dev", "/dev/null"]), 2)
        self.assertIn("refusing", err.getvalue())


if __name__ == "__main__":
    unittest.main()
