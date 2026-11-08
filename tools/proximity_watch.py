#!/usr/bin/env python3
"""Live check of robot/proximity.py. No motor commands.

  proximity_watch.py NAME [--seconds 3] [--baseline BASENAME]

Runs the LiDAR for --seconds, assesses every revolution, and saves the median
per-sector distances to ~/missions/calib/prox_NAME.json. With --baseline,
prints the sectors (robot frame) and raw LiDAR angles that got closer, which
tells whether lidar.angle_direction in config/robot.json is right.
"""
import argparse, json, os, sys, time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from robot.proximity import SECTORS, Proximity  # noqa: E402
from sensors.lidar import Lidar  # noqa: E402

OUT = os.path.expanduser("~/missions/calib")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--seconds", type=float, default=3)
    ap.add_argument("--baseline")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    prox = Proximity()
    lid = Lidar().start()
    t0 = time.monotonic()
    while lid.snapshot()["status"] != "ok":
        if time.monotonic() - t0 > 12:
            sys.exit(f"lidar not ok: {lid.snapshot()}")
        time.sleep(0.05)
    per, raws, last, t1 = {s: [] for s in SECTORS}, [], lid.frames, time.monotonic()
    while time.monotonic() - t1 < a.seconds:
        time.sleep(0.01)
        if lid.frames == last:
            continue
        last = lid.frames
        sn = lid.snapshot()
        r = prox.assess(sn["data"], sn["last_seen"])
        for s in SECTORS:
            mm = r["sectors"].get(s, {}).get("nearest_mm")
            if mm is not None:
                per[s].append(mm)
        raws.append(sn["data"].copy())
    lid.stop()
    med = {s: (float(np.median(v)) if v else None) for s, v in per.items()}
    raw = np.concatenate(raws)
    v = raw[(raw[:, 2] > 0) & (raw[:, 1] > 0)]
    bins = np.full(360, np.nan)
    idx = np.floor(v[:, 0]).astype(int) % 360
    for b in np.unique(idx):
        bins[b] = np.median(v[idx == b, 1])
    json.dump(dict(sectors=med, raw_deg=[None if np.isnan(x) else float(x) for x in bins],
                   forward=prox.forward_deg, direction=prox.direction),
              open(os.path.join(OUT, f"prox_{a.name}.json"), "w"))
    print(f"{a.name}: {len(raws)} revolutions; forward {prox.forward_deg} deg, direction {prox.direction}"
          f" (verified={prox.direction_verified})")
    print("  " + "  ".join(f"{s}:{'-' if med[s] is None else f'{med[s]:.0f}'}" for s in SECTORS))
    if a.baseline:
        base = json.load(open(os.path.join(OUT, f"prox_{a.baseline}.json")))
        print(f"sectors >= 80 mm closer than {a.baseline}:")
        for s in SECTORS:
            b, n = base["sectors"][s], med[s]
            if b is not None and n is not None and b - n >= 80:
                print(f"  {s:12s} {n:6.0f} mm (was {b:6.0f})  level {prox.level(n)}")
        rb = np.array([np.nan if x is None else x for x in base["raw_deg"]])
        drop = rb - bins
        hit = np.flatnonzero(drop >= 80)
        if hit.size:
            near = hit[np.argsort(bins[hit])][:5]
            print("  raw LiDAR angles with the biggest approach (deg: now/was mm):",
                  ", ".join(f"{i}: {bins[i]:.0f}/{rb[i]:.0f}" for i in sorted(near)))


if __name__ == "__main__":
    main()
