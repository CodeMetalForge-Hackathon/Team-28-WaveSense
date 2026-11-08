#!/usr/bin/env python3
"""Live TinyRad radar service: acquisition, RadarEngine, recorder, loopback HTTP.

    ~/tinyrad/venv/bin/python wifi-sensing/radar/radar_service.py            # HTTP 127.0.0.1:3101
    curl -s 127.0.0.1:3101/status.json
    curl -X POST 127.0.0.1:3101/calibrate/start   # nobody in the radar's view, >= 60 s
    curl -X POST 127.0.0.1:3101/calibrate/finish  # saves ~/.config/wifi-xray/radar1.json
    curl -X POST '127.0.0.1:3101/record/start?label=NAME' ; curl -X POST 127.0.0.1:3101/record/stop

Recordings (~/missions/radar/*.radrec, repeated [f64 t][u16 n][u16 rx][n*rx int16]) stay on
this Pi and are never committed. Only consenting people go in front of the radar.
The wifi-xray receiver (port 3100) includes this status in its own when it is running.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from radar_engine import RadarCalibration, RadarEngine  # noqa: E402

REC = struct.Struct("<dHH")
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")


def read_radrec(path: str):
    """Yield (t, frame int16 (n, rx)) from a .radrec file."""
    import numpy as np
    with open(path, "rb") as f:
        while head := f.read(REC.size):
            if len(head) < REC.size:
                return
            t, n, rx = REC.unpack(head)
            raw = f.read(2 * n * rx)
            if len(raw) < 2 * n * rx:
                return
            yield t, np.frombuffer(raw, "<i2").reshape(n, rx)


class RadarService:
    def __init__(self, device, cal_path: str, record_dir: str, near_range_m: float):
        self.dev, self.cal_path, self.record_dir = device, cal_path, record_dir
        cal = RadarCalibration.load(cal_path) if os.path.exists(cal_path) else None
        self.eng = RadarEngine(device.range_axis(RadarEngine.NFFT), cal, near_range_m)
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.report: dict = dict(state="starting")
        self.rec = None
        self.rec_path, self.rec_count = None, 0
        self.error = None

    def acquire(self) -> None:
        try:
            for t, frame in self.dev.frames(self.stop):
                with self.lock:
                    self.eng.feed(t, frame)
                    if self.rec:
                        self.rec.write(REC.pack(t, *frame.shape) + frame.astype("<i2").tobytes())
                        self.rec_count += 1
        except Exception as e:                      # USB unplugged, etc.
            self.error = f"{type(e).__name__}: {e}"

    def assess_loop(self) -> None:
        while not self.stop.wait(1.0):
            with self.lock:
                r = self.eng.assess(time.time())
            if self.error:
                r = dict(state="no_data", valid=False, detail=f"radar stopped: {self.error}", t=time.time())
            self.report = r

    def calibrate_start(self) -> dict:
        with self.lock:
            self.eng.cal = None
            self.eng.start_calibration()
        return dict(ok=True, note="nobody in the radar's view (within its range) for at least 60 s")

    def calibrate_finish(self) -> dict:
        with self.lock:
            try:
                cal = self.eng.finish_calibration()
            except ValueError as e:
                return dict(ok=False, error=str(e))
            cal.save(self.cal_path)
            self.eng.cal = cal
        return dict(ok=True, windows=cal.windows, rate_hz=round(cal.rate_hz, 1), saved=self.cal_path)

    def record_start(self, label: str) -> dict:
        if not LABEL.match(label or ""):
            return dict(ok=False, error="label: letters, digits, '-' and '_' only")
        with self.lock:
            if self.rec:
                return dict(ok=False, error="already recording", path=self.rec_path)
            os.makedirs(self.record_dir, mode=0o700, exist_ok=True)
            self.rec_path = os.path.join(self.record_dir, f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.radrec")
            self.rec, self.rec_count = open(self.rec_path, "ab", buffering=1 << 16), 0
        return dict(ok=True, path=self.rec_path, note="consenting people only; stays on this Pi")

    def record_stop(self) -> dict:
        with self.lock:
            rec, self.rec = self.rec, None
        if rec is None:
            return dict(ok=False, error="not recording")
        rec.close()
        return dict(ok=True, path=self.rec_path, frames=self.rec_count)

    def status(self) -> dict:
        return dict(t=time.time(), radar=self.report, frames_in=self.eng.frames_in,
                    recording=dict(path=self.rec_path, frames=self.rec_count) if self.rec else None,
                    disclaimer="Experimental 24 GHz radar sensing. CANDIDATE indications for a human to verify; "
                               "range only, no count, not validated for life-safety use.")


def make_http(svc: RadarService, port: int) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body):
            data = json.dumps(body, default=float).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/status.json"):
                return self.reply(200, svc.status())
            self.reply(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            routes = {"/calibrate/start": svc.calibrate_start, "/calibrate/finish": svc.calibrate_finish,
                      "/record/stop": svc.record_stop}
            if url.path in routes:
                return self.reply(200, routes[url.path]())
            if url.path == "/record/start":
                return self.reply(200, svc.record_start(parse_qs(url.query).get("label", [""])[0]))
            self.reply(404, {"error": "not found"})

    return ThreadingHTTPServer(("127.0.0.1", port), H)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--http-port", type=int, default=3101)
    ap.add_argument("--cal", default=os.path.expanduser("~/.config/wifi-xray/radar1.json"))
    ap.add_argument("--record-dir", default=os.path.expanduser("~/missions/radar"))
    ap.add_argument("--near-range-m", type=float, default=2.0, help="candidates within this range count as near")
    a = ap.parse_args(argv)
    from tinyrad_dev import TinyRadDevice
    os.makedirs(os.path.dirname(a.cal), mode=0o700, exist_ok=True)
    dev = TinyRadDevice().start()
    svc = RadarService(dev, a.cal, a.record_dir, a.near_range_m)
    httpd = make_http(svc, a.http_port)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))   # stop cleanly
    for target in (svc.acquire, svc.assess_loop, httpd.serve_forever):
        threading.Thread(target=target, daemon=True).start()
    print(f"radar: TinyRad sw {dev.version()}; HTTP 127.0.0.1:{a.http_port}; near zone <= {a.near_range_m} m", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop.set()
        httpd.shutdown()
        time.sleep(0.3)
        dev.close()


if __name__ == "__main__":
    main()
