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
    POST /record/start?label=X  start a labelled recording in --record-dir (consenting people only)
    POST /record/stop           close it; RuView forwarding never stops
- --record writes <label>-<time>.csirec: repeated [f64 t][u16 n][n bytes], raw
  datagrams with receive times, for offline evaluation (evaluate.py). Recordings
  stay on this Pi: they are git-ignored and must never be published.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xray.csi_packet import parse_frame  # noqa: E402
from xray.engine import Calibration, NearZoneEngine  # noqa: E402
from xray.fusion import combine, fuse  # noqa: E402

REC = struct.Struct("<dH")
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")


def iface_network(iface: str) -> ipaddress.IPv4Interface:
    """IPv4 address + prefix of an interface, via iproute2 (as lib.sh does)."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", iface], capture_output=True, text=True).stdout
    for tok in out.split():
        if "/" in tok and tok[0].isdigit():
            return ipaddress.IPv4Interface(tok)
    raise SystemExit(f"{iface} has no IPv4 address: is the hotspot up?")


def count_stations(iface: str) -> int | None:
    """Devices associated with the hotspot (count only: MACs are never kept). None if unknown."""
    try:
        out = subprocess.run(["iw", "dev", iface, "station", "dump"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return sum(1 for line in out.stdout.splitlines() if line.startswith("Station "))


STALE_S = 5.0


def link_hint(stats: dict, stations: int | None, frame_age_s: float | None, iface: str, port: int) -> str | None:
    """Why the link is not delivering CSI, in plain words. None when frames are flowing."""
    if stats["csi_frames"]:
        if frame_age_s is not None and frame_age_s > STALE_S:
            return (f"CSI frames stopped {frame_age_s:.0f} s ago: did the node reboot, lose Wi-Fi or lose power? "
                    "Read its console: python3 scripts/esp32_log.py")
        return None
    if not stats["received"]:
        if stations == 0:
            return (f"No device has joined the hotspot on {iface}, so the ESP32 is not connected. "
                    "Read its console: python3 scripts/esp32_log.py (reason=201 = it cannot see the SSID)")
        if stations:
            return (f"{stations} device(s) joined the hotspot but nothing arrived on UDP port {port}: "
                    "check the node's target address and port")
        return "No datagrams received yet."
    if stats["dropped_source"] == stats["received"]:
        return "Datagrams arrive only from outside the hotspot subnet, and are dropped."
    return "Datagrams arrive but none is an ESP32 CSI frame (magic 0xC5110001): firmware mismatch?"


class Recorder:
    def __init__(self, folder: str, label: str):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        self.path = os.path.join(folder, f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.csirec")
        self.f = open(self.path, "ab", buffering=0)
        self.count = 0

    def write(self, t: float, data: bytes) -> None:
        self.f.write(REC.pack(t, len(data)) + data)
        self.count += 1

    def close(self) -> None:
        self.f.close()


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
                 cal_dir: str, recorder: Recorder | None = None, iface: str = "", station_probe=None,
                 record_dir: str | None = None):
        if bind == "0.0.0.0":
            raise SystemExit("refusing to bind 0.0.0.0: give the hotspot interface")
        self.allow, self.forward, self.cal_dir, self.recorder = allow, forward, cal_dir, recorder
        self.record_dir = record_dir
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, port))
        self.fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if forward else None
        self.engines: dict[int, NearZoneEngine] = {}
        self.reports: dict[int, dict] = {}
        self.stats = dict(received=0, csi_frames=0, other=0, dropped_source=0, forwarded=0, forward_errors=0)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.iface, self.port = iface, self.sock.getsockname()[1]
        self.station_probe = station_probe
        self.stations: int | None = None
        self.last_frame_t: float | None = None
        self.radar_url: str | None = None          # radar_service status (loopback), optional
        self.radar: dict | None = None
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
        with self.lock:
            if self.recorder:
                self.recorder.write(t, data)
        frame = parse_frame(data)
        if frame is None:
            self.stats["other"] += 1           # vitals/sync packets etc.: forwarded, not analysed
            return
        self.stats["csi_frames"] += 1
        self.last_frame_t = t
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

    def probe_link(self) -> None:
        if self.station_probe is not None:
            self.stations = self.station_probe()

    def poll_radar(self) -> None:
        if not self.radar_url:
            return
        try:
            import urllib.request
            with urllib.request.urlopen(self.radar_url, timeout=0.5) as r:
                self.radar = json.loads(r.read(200_000)).get("radar")
        except Exception:
            self.radar = dict(state="no_data", detail="radar service not reachable")

    def assess_loop(self) -> None:
        ticks = 0
        while not self.stop.wait(1.0):
            if ticks % 5 == 0:
                self.probe_link()
            ticks += 1
            self.poll_radar()
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

    # ---- recording control ---------------------------------------------------------
    def record_start(self, label: str) -> dict:
        if not self.record_dir:
            return dict(ok=False, error="recording disabled: start the receiver with --record-dir")
        if not LABEL.match(label or ""):
            return dict(ok=False, error="label: letters, digits, '-' and '_' only, up to 48 characters")
        with self.lock:
            if self.recorder:
                return dict(ok=False, error="already recording", path=self.recorder.path)
            self.recorder = Recorder(self.record_dir, label)
            return dict(ok=True, path=self.recorder.path,
                        note="only consenting people in the near zone; the file stays on this Pi")

    def record_stop(self) -> dict:
        with self.lock:
            rec, self.recorder = self.recorder, None
        if rec is None:
            return dict(ok=False, error="not recording")
        rec.close()
        return dict(ok=True, path=rec.path, datagrams=rec.count)

    def status(self) -> dict:
        now = time.time()
        age = None if self.last_frame_t is None else round(now - self.last_frame_t, 1)
        with self.lock:
            link = dict(self.stats, stations=self.stations, last_frame_age_s=age,
                        hint=link_hint(self.stats, self.stations, age, self.iface or "the hotspot", self.port))
            wifi_zone = fuse(self.reports)
            return dict(t=now, link=link, zone=combine(wifi_zone, self.radar), wifi_zone=wifi_zone, radar=self.radar,
                        nodes={str(k): v for k, v in self.reports.items()},
                        recording=dict(path=self.recorder.path, datagrams=self.recorder.count) if self.recorder else None,
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
const z=s.zone,hint=s.link.hint?`<p class=warn>${s.link.hint}</p>`:'',rd=s.radar;
h=(rd?`<p>Radar: <span class=c>${rd.state}</span><br><small>${rd.kind??''} ${rd.range_m!=null&&rd.state==='candidate_present'?'at ~'+rd.range_m+' m':''} presence ${rd.presence_ratio??'-'}x, motion ${rd.motion_ratio??'-'}x ${rd.detail??''}</small></p>`:'')+h;
o.innerHTML=hint+(h?`<p>Zone: <span class=c>${z.state}</span> <small>(${z.basis})</small><br>${z.label}</p>`+h:'<p>No CSI node seen yet.</p>')+
(s.recording?`<p class=warn>RECORDING ${s.recording.path} (${s.recording.datagrams} datagrams)</p>`:'')+
`<small>${s.disclaimer}<br>link: ${JSON.stringify(s.link)}</small>`}
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
            url = urlparse(self.path)
            if url.path == "/calibrate/start":
                return self.reply(200, door.calibrate_start())
            if url.path == "/calibrate/finish":
                return self.reply(200, door.calibrate_finish())
            if url.path == "/record/start":
                return self.reply(200, door.record_start(parse_qs(url.query).get("label", [""])[0]))
            if url.path == "/record/stop":
                return self.reply(200, door.record_stop())
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
    ap.add_argument("--record", help="record from startup into this folder (stays on this Pi)")
    ap.add_argument("--label", default="session")
    ap.add_argument("--radar", default="http://127.0.0.1:3101/status.json",
                    help="radar_service status URL on loopback; 'none' to disable")
    ap.add_argument("--record-dir", default=os.path.expanduser("~/missions/csi"),
                    help="folder for recordings started over HTTP (POST /record/start?label=...)")
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
    probe = None if a.bind else (lambda: count_stations(a.iface))
    door = FrontDoor(bind, a.port, allow, forward, os.path.expanduser(a.cal_dir), rec,
                     iface="" if a.bind else a.iface, station_probe=probe,
                     record_dir=os.path.expanduser(a.record) if a.record else os.path.expanduser(a.record_dir))
    if a.radar != "none":
        if not urlparse(a.radar).hostname in ("127.0.0.1", "localhost"):
            raise SystemExit("--radar must be a loopback URL")
        door.radar_url = a.radar
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
