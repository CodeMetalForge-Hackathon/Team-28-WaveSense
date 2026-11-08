#!/usr/bin/env python3
"""wifi-xray front door: receive ESP32 CSI, forward it to RuView, judge the near zone.

    python3 -m xray.receiver --iface wlan1 --forward 127.0.0.1:5006
    python3 -m xray.receiver --iface wlan1 --forward 127.0.0.1:5006 --record ~/missions/csi --label empty

- UDP: binds the IPv4 address of --iface (the hotspot), port 5005 by default.
  Never 0.0.0.0. Datagrams from outside that interface's subnet are dropped.
- Every admitted datagram is forwarded UNCHANGED to --forward (RuView, published
  on loopback), so the RuView website keeps working.
- CSI frames feed one NearZoneEngine per node. assess() runs once per second.
- HTTP on 127.0.0.1:3100 (loopback only):
    GET  /status.json           latest report per node, plus link counters
    GET  /                      small auto-refreshing page
    POST /calibrate/start       near zone EMPTY; background activity is fine
    POST /calibrate/finish      saves the calibration to --cal-dir
- --record writes <label>-<time>.csirec: repeated [f64 t][u16 n][n bytes], raw
  datagrams with receive times, for offline evaluation (evaluate.py). Recordings
  stay on this Pi: they are git-ignored and must never be published.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xray.csi_packet import parse_frame  # noqa: E402
from xray.engine import Calibration, NearZoneEngine  # noqa: E402

REC = struct.Struct("<dH")


def iface_network(iface: str) -> ipaddress.IPv4Interface:
    """IPv4 address + prefix of an interface, via iproute2 (as lib.sh does)."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", iface], capture_output=True, text=True).stdout
    for tok in out.split():
        if "/" in tok and tok[0].isdigit():
            return ipaddress.IPv4Interface(tok)
    raise SystemExit(f"{iface} has no IPv4 address: is the hotspot up?")


class Recorder:
    def __init__(self, folder: str, label: str):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        self.path = os.path.join(folder, f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.csirec")
        self.f = open(self.path, "ab", buffering=0)
        self.count = 0

    def write(self, t: float, data: bytes) -> None:
        self.f.write(REC.pack(t, len(data)) + data)
        self.count += 1


def read_recording(path: str):
    """Yield (t, datagram) from a .csirec file."""
    with open(path, "rb") as f:
        while head := f.read(REC.size):
            if len(head) < REC.size:
                return
            t, n = REC.unpack(head)
            data = f.read(n)
            if len(data) < n:
                return
            yield t, data


class FrontDoor:
    def __init__(self, bind: str, port: int, allow: ipaddress.IPv4Network, forward: tuple | None,
                 cal_dir: str, recorder: Recorder | None = None):
        if bind == "0.0.0.0":
            raise SystemExit("refusing to bind 0.0.0.0: give the hotspot interface")
        self.allow, self.forward, self.cal_dir, self.recorder = allow, forward, cal_dir, recorder
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, port))
        self.fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if forward else None
        self.engines: dict[int, NearZoneEngine] = {}
        self.reports: dict[int, dict] = {}
        self.stats = dict(received=0, csi_frames=0, other=0, dropped_source=0, forwarded=0, forward_errors=0)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        os.makedirs(cal_dir, mode=0o700, exist_ok=True)

    def cal_path(self, node: int) -> str:
        return os.path.join(self.cal_dir, f"node{node}.json")

    def engine(self, node: int) -> NearZoneEngine:
        if node not in self.engines:
            cal = Calibration.load(self.cal_path(node)) if os.path.exists(self.cal_path(node)) else None
            self.engines[node] = NearZoneEngine(calibration=cal)
        return self.engines[node]

    def handle(self, data: bytes, addr: tuple, t: float) -> None:
        self.stats["received"] += 1
        if ipaddress.IPv4Address(addr[0]) not in self.allow:
            self.stats["dropped_source"] += 1
            return
        if self.fwd is not None:
            try:
                self.fwd.sendto(data, self.forward)
                self.stats["forwarded"] += 1
            except OSError:
                self.stats["forward_errors"] += 1
        if self.recorder:
            self.recorder.write(t, data)
        frame = parse_frame(data)
        if frame is None:
            self.stats["other"] += 1           # vitals/sync packets etc.: forwarded, not analysed
            return
        self.stats["csi_frames"] += 1
        with self.lock:
            self.engine(frame.node_id).feed(frame, t)

    def udp_loop(self) -> None:
        self.sock.settimeout(0.5)
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            self.handle(data, addr, time.time())

    def assess_loop(self) -> None:
        while not self.stop.wait(1.0):
            now = time.time()
            with self.lock:
                for node, eng in self.engines.items():
                    self.reports[node] = eng.assess(now)

    # ---- calibration control -----------------------------------------------------
    def calibrate_start(self) -> dict:
        with self.lock:
            if not self.engines:
                return dict(ok=False, error="no CSI node seen yet")
            for e in self.engines.values():
                e.cal = None
                e.start_calibration()
        return dict(ok=True, nodes=sorted(self.engines), note="keep the near zone EMPTY for at least 60 s")

    def calibrate_finish(self) -> dict:
        out = {}
        with self.lock:
            for node, e in self.engines.items():
                try:
                    cal = e.finish_calibration()
                    cal.save(self.cal_path(node))
                    e.cal = cal
                    out[node] = dict(ok=True, windows=cal.windows, rate_hz=round(cal.rate_hz, 1))
                except ValueError as err:
                    out[node] = dict(ok=False, error=str(err))
        return dict(ok=all(v["ok"] for v in out.values()) if out else False, nodes=out)

    def status(self) -> dict:
        with self.lock:
            return dict(t=time.time(), link=dict(self.stats), nodes={str(k): v for k, v in self.reports.items()},
                        recording=self.recorder.path if self.recorder else None,
                        disclaimer="Experimental Wi-Fi sensing. CANDIDATE indications for a human to verify; "
                                   "no location, no count, not validated for life-safety use.")


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>wifi-xray</title><style>body{font:16px system-ui;background:#111;color:#eee;margin:1em}
.c{font-size:1.6em;font-weight:600}.warn{color:#fb0}small{color:#aaa}pre{white-space:pre-wrap}</style>
<h2>wifi-xray: near-zone check</h2><p class=warn>Experimental. CANDIDATE only: verify by other means.</p>
<div id=o>loading...</div><script>
async function tick(){try{const s=await (await fetch('status.json',{cache:'no-store'})).json();
let h='';for(const [n,r] of Object.entries(s.nodes)){h+=`<p>Node ${n}: <span class=c>${r.state}</span><br>`+
`<small>link ${r.rate_hz??'-'} fps, coverage ${r.coverage??'-'}, RSSI ${r.rssi_dbm?.toFixed?.(0)??'-'} dBm; `+
`motion ${r.motion??'-'}; breathing-like ${r.evidence?.breathing?'yes ~'+Math.round(r.breathing_bpm)+'/min':'no'}; `+
`profile ${r.profile_distance??'-'} / ${r.profile_threshold??'-'}</small></p>`}
o.innerHTML=(h||'<p>No CSI node seen yet.</p>')+`<small>${s.disclaimer}<br>link: ${JSON.stringify(s.link)}</small>`}
catch(e){o.textContent='receiver not reachable'}}tick();setInterval(tick,1000)</script>"""


def make_http(door: FrontDoor, port: int) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=float).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/status.json"):
                return self.reply(200, door.status())
            if self.path in ("/", "/index.html"):
                return self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
            self.reply(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/calibrate/start":
                return self.reply(200, door.calibrate_start())
            if self.path == "/calibrate/finish":
                return self.reply(200, door.calibrate_finish())
            self.reply(404, {"error": "not found"})

    return ThreadingHTTPServer(("127.0.0.1", port), H)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--iface", default=os.environ.get("CSI_IFACE", "wlan1"))
    ap.add_argument("--bind", help="override the bind address (tests); never 0.0.0.0")
    ap.add_argument("--allow", help="override the allowed source network (tests)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CSI_UDP_PORT", "5005")))
    ap.add_argument("--forward", default="127.0.0.1:5006", help="RuView UDP on loopback; 'none' to disable")
    ap.add_argument("--http-port", type=int, default=3100)
    ap.add_argument("--cal-dir", default=os.path.expanduser("~/.config/wifi-xray"))
    ap.add_argument("--record", help="folder for .csirec recordings (stays on this Pi)")
    ap.add_argument("--label", default="session")
    a = ap.parse_args(argv)
    if a.bind:
        bind, allow = a.bind, ipaddress.IPv4Network(a.allow or f"{a.bind}/32", strict=False)
    else:
        net = iface_network(a.iface)
        bind, allow = str(net.ip), net.network
    if a.forward == "none":
        forward = None
    else:
        host, port = a.forward.rsplit(":", 1)
        if not ipaddress.IPv4Address(host).is_loopback:
            raise SystemExit("--forward must be a loopback address")
        forward = (host, int(port))
    rec = Recorder(os.path.expanduser(a.record), a.label) if a.record else None
    door = FrontDoor(bind, a.port, allow, forward, os.path.expanduser(a.cal_dir), rec)
    httpd = make_http(door, a.http_port)
    for target in (door.udp_loop, door.assess_loop, httpd.serve_forever):
        threading.Thread(target=target, daemon=True).start()
    print(f"wifi-xray: UDP {bind}:{a.port} (allow {allow}) -> forward {forward}; HTTP 127.0.0.1:{a.http_port}"
          + (f"; recording {rec.path}" if rec else ""), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        door.stop.set()
        httpd.shutdown()


if __name__ == "__main__":
    main()
