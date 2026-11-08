"""wifi-xray receiver tests on loopback (synthetic datagrams only)."""
import ipaddress, json, os, socket, sys, tempfile, threading, time, unittest, urllib.request

try:
    import numpy as np
except ImportError:
    raise unittest.SkipTest("numpy not installed")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from xray.csi_packet import build_frame  # noqa: E402
from xray.receiver import FrontDoor, make_http, read_recording, Recorder  # noqa: E402


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
        self.rec.f.close()

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

    def test_refuses_wildcard_bind(self):
        with self.assertRaises(SystemExit):
            FrontDoor("0.0.0.0", 0, ipaddress.IPv4Network("10.0.0.0/8"), None, self.tmp)


if __name__ == "__main__":
    unittest.main()
