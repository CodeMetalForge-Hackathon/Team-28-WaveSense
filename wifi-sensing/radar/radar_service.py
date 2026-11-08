#!/usr/bin/env python3
"""Live TinyRad radar service: acquisition, RadarEngine, recorder, loopback HTTP.

    ~/tinyrad/venv/bin/python wifi-sensing/radar/radar_service.py            # HTTP 127.0.0.1:3101
    curl -s 127.0.0.1:3101/status.json
    curl -X POST 127.0.0.1:3101/calibrate/start   # adaptive (default): relearn the scene in 15 s
                                                  # --fixed-calibration: start a manual calibration
    curl -X POST 127.0.0.1:3101/calibrate/finish  # --fixed-calibration: save ~/.config/wifi-xray/radar1.json
    curl -X POST '127.0.0.1:3101/record/start?label=NAME' ; curl -X POST 127.0.0.1:3101/record/stop

Recordings (~/missions/radar/*.radrec, repeated [f64 t][u16 n][u16 rx][n*rx int16]) stay on
this Pi and are never committed. Only consenting people go in front of the radar.
The wifi-xray receiver (port 3100) includes this status in its own when it is running.

It also writes the WaveSense phone app's radar contract (schema 1) once a second, replacing the
file atomically (default /srv/team28/runtime/radar/status.json; point the app at it with
`--radar-status-file`). Range only: bearing_deg stays null until angle is measured.
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
CONTRACT_DEFAULT = "/srv/team28/runtime/radar/status.json"


def contract_payload(report: dict, now: float, online: bool = True) -> dict:
    """WaveSense radar contract, schema 1 (wavesense-mobile/README.md, 'Radar teammate contract').
    candidate_present -> 'motion' if moving else 'candidate'; no_candidate -> 'clear'; anything else
    (calibrating, uncalibrated, warming up, no data) -> 'not_judging'. One track per candidate, at the
    strongest range; bearing_deg is None because no angle is measured."""
    st = report.get("state")
    if st == "candidate_present":
        state = "motion" if report.get("kind") == "moving" else "candidate"
        tracks = [{"range_m": float(report["range_m"]), "bearing_deg": None}] if report.get("range_m") is not None else []
    elif st == "no_candidate":
        state, tracks = "clear", []
    else:
        state, tracks = "not_judging", []
    return {"schema": 1, "timestamp_ms": int(now * 1000), "online": bool(online and st != "no_data"),
            "state": state, "tracks": tracks}


def write_atomic(path: str, payload: dict) -> None:
    d = os.path.dirname(path)
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
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
    def __init__(self, device, cal_path: str, record_dir: str, near_range_m: float, contract_file: str | None = None,
                 adaptive: bool = True):
        self.dev, self.cal_path, self.record_dir = device, cal_path, record_dir
        cal = RadarCalibration.load(cal_path) if (not adaptive and os.path.exists(cal_path)) else None
        self.eng = RadarEngine(device.range_axis(RadarEngine.NFFT), cal, near_range_m, adaptive=adaptive)
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.report: dict = dict(state="starting")
        self.rec = None
        self.rec_path, self.rec_count = None, 0
        self.error = None
        self.reconnects = 0
        self.contract_file, self.contract_error = contract_file, None

    def acquire(self) -> None:
        """Read frames forever. On a USB error (unplugged, power dip, re-enumeration) close the device,
        wait, and reopen it; the scene is then relearned, since gain and phase may have changed."""
        while not self.stop.is_set():
            try:
                for t, frame in self.dev.frames(self.stop):
                    with self.lock:
                        self.eng.feed(t, frame)
                        if self.rec:
                            self.rec.write(REC.pack(t, *frame.shape) + frame.astype("<i2").tobytes())
                            self.rec_count += 1
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                self.reconnects += 1
                print(time.strftime("%T"), "radar error:", self.error, "- reconnecting", flush=True)
                self.dev.close()
                # back off: re-starting the radar draws a current surge, and on 2026-11-08 a radar start
                # tripped the Pi's USB over-current protection and dropped the lidar and camera too
                delays = iter([3.0, 10.0, 30.0])
                while not self.stop.wait(next(delays, 60.0)):
                    try:
                        self.dev.start()
                    except Exception as e2:
                        self.error = f"reconnecting: {type(e2).__name__}: {e2}"
                        continue
                    with self.lock:
                        self.eng.buf.clear()
                        self.eng._acc, self.eng._acc_t = [], []
                        if self.eng.adaptive:
                            self.eng.reset_background()
                    self.error = None
                    print(time.strftime("%T"), "radar reconnected", flush=True)
                    break

    def assess_loop(self) -> None:
        while not self.stop.wait(1.0):
            try:
                with self.lock:
                    r = self.eng.assess(time.time())
            except Exception as e:                  # never let the status (and the app file) go stale
                r = dict(state="no_data", valid=False, detail=f"engine error: {type(e).__name__}: {e}", t=time.time())
            if self.error:
                r = dict(state="no_data", valid=False, detail=f"radar stopped: {self.error}", t=time.time())
            self.report = r
            if self.contract_file:
                try:
                    write_atomic(self.contract_file, contract_payload(r, time.time(), online=not self.error))
                    self.contract_error = None
                except OSError as e:
                    self.contract_error = f"{type(e).__name__}: {e}"

    def calibrate_start(self) -> dict:
        with self.lock:
            if self.eng.adaptive:
                self.eng.reset_background()
                return dict(ok=True, mode="adaptive", note=f"relearning the scene for {RadarEngine.LEARN_S} s: "
                                                           "keep the area in front of the radar clear")
            self.eng.cal = None
            self.eng.start_calibration()
        return dict(ok=True, note="nobody in the radar's view (within its range) for at least 60 s")

    def calibrate_finish(self) -> dict:
        if self.eng.adaptive:
            return dict(ok=True, mode="adaptive", note="nothing to finish: the background is relearned automatically")
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
        return dict(t=time.time(), radar=self.report, frames_in=self.eng.frames_in, reconnects=self.reconnects,
                    recording=dict(path=self.rec_path, frames=self.rec_count) if self.rec else None,
                    contract=dict(file=self.contract_file, error=self.contract_error),
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
    ap.add_argument("--fixed-calibration", action="store_true",
                    help="use the saved manual calibration instead of the adaptive background")
    ap.add_argument("--contract-file", default=CONTRACT_DEFAULT,
                    help="WaveSense radar contract JSON, replaced atomically each second; 'none' to disable")
    a = ap.parse_args(argv)
    from tinyrad_dev import TinyRadDevice
    os.makedirs(os.path.dirname(a.cal), mode=0o700, exist_ok=True)
    dev = TinyRadDevice().start()
    contract = None if a.contract_file == "none" else a.contract_file
    if contract:
        os.makedirs(os.path.dirname(contract), mode=0o2775, exist_ok=True)
    svc = RadarService(dev, a.cal, a.record_dir, a.near_range_m, contract, adaptive=not a.fixed_calibration)
    httpd = make_http(svc, a.http_port)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))   # stop cleanly
    for target in (svc.acquire, svc.assess_loop, httpd.serve_forever):
        threading.Thread(target=target, daemon=True).start()
    try:
        version = dev.version()
    except Exception as e:
        version = f"unknown ({type(e).__name__})"
    print(f"radar: TinyRad sw {version}; HTTP 127.0.0.1:{a.http_port}; near zone <= {a.near_range_m} m; "
          f"{'adaptive background' if svc.eng.adaptive else 'fixed calibration'}", flush=True)
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
