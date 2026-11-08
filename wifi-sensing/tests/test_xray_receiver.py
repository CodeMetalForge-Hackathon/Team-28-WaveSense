"""wifi-xray receiver tests on loopback (synthetic datagrams only)."""
import ipaddress, json, os, socket, sys, tempfile, threading, time, unittest, urllib.request

try:
    import numpy as np
except ImportError:
    raise unittest.SkipTest("numpy not installed")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from xray.csi_packet import build_frame  # noqa: E402
from xray.receiver import FrontDoor, link_hint, make_http, read_recording, Recorder  # noqa: E402


def udp_sink():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(1.0)
    return s


class Receiver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sink = udp_sink()
        self.rec = Recorder(os.path.join(self.tmp, "rec"), "unit")
        self.door = FrontDoor("127.0.0.1", 0, ipaddress.IPv4Network("127.0.0.0/8"),
                              self.sink.getsockname(), os.path.join(self.tmp, "cal"), self.rec)
        self.addr = self.door.sock.getsockname()
        threading.Thread(target=self.door.udp_loop, daemon=True).start()
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def tearDown(self):
        self.door.stop.set()
        time.sleep(0.6)                      # let udp_loop see the stop flag
        for sk in (self.door.sock, self.door.fwd, self.sink, self.tx):
            sk and sk.close()
        self.rec.f.closed or self.rec.close()

    def send(self, data):
        self.tx.sendto(data, self.addr)

    def test_forwards_unchanged_records_and_feeds_engine(self):
        frames = [build_frame(np.full((1, 64), 10 + i, complex), node_id=3, sequence=i) for i in range(5)]
        vitals = b"\x02\x00\x11\xc5" + bytes(28)               # a non-CSI packet: forwarded, not analysed
        for d in frames + [vitals]:
            self.send(d)
        got = [self.sink.recvfrom(4096)[0] for _ in range(6)]
        self.assertEqual(got, frames + [vitals])
        time.sleep(0.1)
        s = self.door.stats
        self.assertEqual((s["received"], s["csi_frames"], s["other"], s["forwarded"]), (6, 5, 1, 6))
        self.assertIn(3, self.door.engines)
        self.assertEqual(len(self.door.engines[3].buf), 5)
        self.assertEqual([d for _, d in read_recording(self.rec.path)], frames + [vitals])

    def test_drops_sources_outside_the_hotspot_subnet(self):
        self.door.allow = ipaddress.IPv4Network("10.42.0.0/24")
        self.send(build_frame(np.ones((1, 8))))
        time.sleep(0.2)
        self.assertEqual(self.door.stats["dropped_source"], 1)
        self.assertEqual(self.door.stats["forwarded"], 0)
        with self.assertRaises(socket.timeout):
            self.sink.recvfrom(4096)

    def test_http_status_and_calibration_guard(self):
        httpd = make_http(self.door, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        s = json.load(urllib.request.urlopen(base + "/status.json"))
        self.assertIn("CANDIDATE", s["disclaimer"])
        r = urllib.request.Request(base + "/calibrate/start", method="POST")
        self.assertFalse(json.load(urllib.request.urlopen(r))["ok"])     # no node seen yet
        self.assertIn(b"wifi-xray", urllib.request.urlopen(base + "/").read())
        httpd.shutdown()

    def test_status_explains_a_silent_link(self):
        self.door.station_probe = lambda: 0
        self.door.probe_link()
        st = self.door.status()
        self.assertEqual(st["link"]["stations"], 0)
        self.assertIn("No device has joined the hotspot", st["link"]["hint"])
        self.assertEqual(st["zone"]["state"], "not_judging")
        self.send(build_frame(np.ones((1, 8)), node_id=1))
        time.sleep(0.2)
        st = self.door.status()
        self.assertIsNone(st["link"]["hint"])                     # frames are flowing
        self.assertLess(st["link"]["last_frame_age_s"], 2)

    def test_http_recording_start_stop(self):
        self.door.record_stop()                                   # the setUp recorder
        self.door.record_dir = os.path.join(self.tmp, "http-rec")
        httpd = make_http(self.door, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        post = lambda path: json.load(urllib.request.urlopen(urllib.request.Request(base + path, method="POST")))
        self.assertFalse(post("/record/start?label=../../etc")["ok"])         # label is validated
        self.assertFalse(post("/record/stop")["ok"])                          # not recording
        self.send(build_frame(np.ones((1, 8)), sequence=1))                   # before start: not recorded
        time.sleep(0.1)
        r = post("/record/start?label=two-seated-A")
        self.assertTrue(r["ok"])
        self.assertTrue(os.path.basename(r["path"]).startswith("two-seated-A-"))
        self.assertFalse(post("/record/start?label=other")["ok"])             # one at a time
        during = [build_frame(np.ones((1, 8)), sequence=i) for i in (2, 3)]
        for d in during:
            self.send(d)
        time.sleep(0.2)
        self.assertEqual(self.door.status()["recording"]["datagrams"], 2)
        stop = post("/record/stop")
        self.assertEqual((stop["ok"], stop["datagrams"]), (True, 2))
        self.send(build_frame(np.ones((1, 8)), sequence=4))                   # after stop: not recorded
        time.sleep(0.1)
        self.assertEqual([d for _, d in read_recording(r["path"])], during)
        self.assertIsNone(self.door.status()["recording"])
        self.assertEqual(self.door.stats["forwarded"], 4)                     # forwarding never paused
        httpd.shutdown()

    def test_refuses_wildcard_bind(self):
        with self.assertRaises(SystemExit):
            FrontDoor("0.0.0.0", 0, ipaddress.IPv4Network("10.0.0.0/8"), None, self.tmp)


class Hints(unittest.TestCase):
    Z = dict(received=0, csi_frames=0, other=0, dropped_source=0, forwarded=0, forward_errors=0)

    def hint(self, stations=None, age=None, **stats):
        return link_hint(dict(self.Z, **stats), stations, age, "wlan1", 5005)

    def test_cases(self):
        self.assertIn("No device has joined the hotspot on wlan1", self.hint(stations=0))
        self.assertIn("nothing arrived on UDP port 5005", self.hint(stations=1))
        self.assertEqual(self.hint(), "No datagrams received yet.")
        self.assertIn("outside the hotspot subnet", self.hint(received=3, dropped_source=3))
        self.assertIn("none is an ESP32 CSI frame", self.hint(received=3, other=3))
        self.assertIsNone(self.hint(received=9, csi_frames=9, age=0.3))
        self.assertIn("stopped 12 s ago", self.hint(received=9, csi_frames=9, age=12.0))


if __name__ == "__main__":
    unittest.main()
