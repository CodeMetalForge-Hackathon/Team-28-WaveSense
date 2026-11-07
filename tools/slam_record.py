#!/usr/bin/env python3
"""Record a SLAM map from the live LiDAR. No motors.

  slam_record.py --seconds 90 --out ~/missions/test [--carry]

--carry ignores returns within 700 mm in the robot's back 120 deg (the person
carrying it). Prints pose/rate every 2 s; saves map.png (trail red, start green,
end pose blue) and trail.json to --out.
"""
import argparse, json, math, os, signal, sys, threading, time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mapping.slam import Slam  # noqa: E402
from sensors.lidar import Lidar  # noqa: E402

CARRY_MASK = [[120.0, -120.0, 700.0]]   # ccw from +120 to -120 deg = the back, up to 700 mm


def cpu_times():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:8]]
    return sum(v), v[3] + v[4]


def render(snap, slam, path):
    m = snap["data"]["map"]
    img = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    trail = snap["data"]["trail"]
    pts = np.array([slam.to_pixel(x, y) for x, y, _ in trail], np.int32)
    if len(pts) > 1:
        cv2.polylines(img, [pts.reshape(-1, 1, 2)], False, (0, 0, 255), 2)
    cv2.circle(img, tuple(int(v) for v in pts[0]), 6, (0, 200, 0), -1)
    x, y, th = snap["data"]["pose"]
    cx, cy = slam.to_pixel(x, y)
    tip = (int(cx + 18 * math.cos(math.radians(th))), int(cy - 18 * math.sin(math.radians(th))))
    cv2.arrowedLine(img, (cx, cy), tip, (255, 0, 0), 2, tipLength=0.4)
    # crop to the explored area (+1.5 m margin) so the room is visible, keep a 1 m scale bar
    ys, xs = np.nonzero(np.abs(m.astype(int) - 127) > 10)
    if len(xs):
        pad = int(1500 / slam.mm_per_px)
        x0, x1 = max(0, xs.min() - pad), min(m.shape[1], xs.max() + pad)
        y0, y1 = max(0, ys.min() - pad), min(m.shape[0], ys.max() + pad)
        img = img[y0:y1, x0:x1]
    px = int(1000 / slam.mm_per_px)
    cv2.line(img, (10, img.shape[0] - 12), (10 + px, img.shape[0] - 12), (0, 0, 0), 3)
    cv2.putText(img, "1 m", (12, img.shape[0] - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(path, img)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--out", default="~/missions/test")
    ap.add_argument("--carry", action="store_true")
    a = ap.parse_args()
    out = os.path.expanduser(a.out)
    os.makedirs(out, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())

    lidar = Lidar().start()
    slam = Slam(lidar, mask=CARRY_MASK if a.carry else None).start()
    t0 = time.monotonic()
    while lidar.snapshot()["status"] != "ok" and time.monotonic() - t0 < 12 and not stop.is_set():
        time.sleep(0.05)
    print(f"recording {a.seconds:.0f} s, carry mask {'ON' if a.carry else 'off'}, params {slam.params}", flush=True)
    T0, I0 = cpu_times()
    p0, w0 = time.process_time(), time.monotonic()
    while not stop.is_set() and time.monotonic() - w0 < a.seconds:
        stop.wait(2.0)
        sn = slam.snapshot()
        x, y, th = sn["data"]["pose"]
        d = sn["data"]
        print(f"{time.monotonic() - w0:5.0f}s {sn['status']:6s} {sn['fps']:4.1f} upd/s "
              f"pose x={x - 10000:+6.0f} y={y - 10000:+6.0f} mm th={th:+6.1f} deg "
              f"| updates {d['updates']} skipped {d['skipped']} | {d['update_ms'] or 0:.1f} ms/update", flush=True)
    wall = time.monotonic() - w0
    T1, I1 = cpu_times()
    slam.refresh_map()
    sn = slam.snapshot()
    render(sn, slam, os.path.join(out, "map.png"))
    trail = [dict(x_mm=x - 10000, y_mm=y - 10000, theta_deg=th) for x, y, th in sn["data"]["trail"]]
    json.dump(dict(start_at_origin=True, params=slam.params, carry_mask=a.carry, trail=trail),
              open(os.path.join(out, "trail.json"), "w"), indent=1)
    length = sum(math.hypot(b["x_mm"] - a_["x_mm"], b["y_mm"] - a_["y_mm"]) for a_, b in zip(trail, trail[1:]))
    print(f"saved {out}/map.png and trail.json: {len(trail)} trail points, path length {length / 1000:.2f} m")
    print(f"updates {sn['data']['updates']} in {wall:.0f} s = {sn['data']['updates'] / wall:.1f}/s, "
          f"skipped {sn['data']['skipped']}, median {sn['data']['update_ms']:.1f} ms/update")
    print(f"CPU: this process {100 * (time.process_time() - p0) / wall:.0f}% of one core; "
          f"Pi total {100 * ((T1 - T0) - (I1 - I0)) / (T1 - T0):.1f}% of 4 cores")
    slam.stop()
    lidar.stop()


if __name__ == "__main__":
    main()
