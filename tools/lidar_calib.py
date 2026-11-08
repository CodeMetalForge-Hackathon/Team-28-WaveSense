#!/usr/bin/env python3
"""LiDAR calibration helper (Phase 2). Captures go to ~/missions/calib/ (not the repo).

  capture NAME [--revs 30]       collect revolutions -> NAME.npz
  diff BASE NEW [--drop 100]     angle clusters that got >= drop mm closer than BASE
  range NAME --angle A [--window 4]   distance of a flat object around angle A
  rgb NAME                       save one RGB frame -> NAME.jpg

Angles are raw LiDAR angles (degrees, as the C1 reports them).
"""
import argparse, os, sys, time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CAL = os.path.expanduser("~/missions/calib")


def per_degree(pts, step=1.0):
    """Median distance per angle bin over all revolutions, valid points only."""
    v = pts[(pts[:, 1] > 0) & (pts[:, 2] > 0)]
    bins = np.floor(v[:, 0] / step).astype(int) % int(round(360 / step))
    out = np.full(int(round(360 / step)), np.nan)
    for b in np.unique(bins):
        out[b] = np.median(v[bins == b, 1])
    return out


def load(name):
    return np.load(os.path.join(CAL, name + ".npz"))["pts"]


def cmd_capture(a):
    from sensors.lidar import Lidar
    os.makedirs(CAL, exist_ok=True)
    l = Lidar().start()
    t0 = time.monotonic()
    while l.snapshot()["status"] != "ok":
        if time.monotonic() - t0 > 12:
            sys.exit(f"lidar not ok after 12 s: {l.snapshot()['status']} {l.snapshot()['detail']}")
        time.sleep(0.05)
    revs, last = [], l.frames
    while len(revs) < a.revs:
        time.sleep(0.01)
        if l.frames != last:
            last = l.frames
            revs.append(l.snapshot()["data"].copy())
    l.stop()
    pts = np.concatenate(revs)
    np.savez(os.path.join(CAL, a.name + ".npz"), pts=pts)
    valid = (pts[:, 1] > 0) & (pts[:, 2] > 0)
    print(f"saved {a.name}: {len(revs)} revolutions, {len(pts)} points, {100 * valid.mean():.1f}% valid")
    print("(process exit waits ~5 s for the LiDAR port to close)")


def clusters(mask):
    """Contiguous runs of True in a circular 360-bin mask -> list of bin index lists."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs, cur = [], [idx[0]]
    for i in idx[1:]:
        if i - cur[-1] <= 2:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    if len(runs) > 1 and runs[0][0] <= 1 and runs[-1][-1] >= len(mask) - 2:  # wraps past 0
        runs[0] = runs.pop() + runs[0]
    return runs


def cmd_diff(a):
    base, new = per_degree(load(a.base)), per_degree(load(a.new))
    closer = (base - new) >= a.drop
    closer |= np.isnan(base) & ~np.isnan(new) & (new < 3000)   # appeared where there was no return
    print(f"bins >= {a.drop} mm closer than {a.base}:")
    for run in sorted(clusters(closer), key=lambda r: np.nanmin(new[r])):
        un = np.unwrap(np.radians(np.array(run) + 0.5))
        center = np.degrees(np.mean(un)) % 360
        d = new[run]
        print(f"  {run[0]:3d}..{run[-1]:3d} deg  width {len(run):3d}  center {center:6.1f} deg"
              f"  min {np.nanmin(d):6.0f} mm  median {np.nanmedian(d):6.0f} mm"
              f"  (baseline median {np.nanmedian(base[run]):6.0f} mm)")


def cmd_range(a):
    pts = load(a.name)
    v = pts[(pts[:, 1] > 0) & (pts[:, 2] > 0)]
    dang = (v[:, 0] - a.angle + 180) % 360 - 180
    w = v[np.abs(dang) <= a.window]
    if len(w) < 10:
        sys.exit(f"only {len(w)} valid points within +-{a.window} deg of {a.angle}")
    th = np.radians(w[:, 0]); x, y = w[:, 1] * np.cos(th), w[:, 1] * np.sin(th)
    # total-least-squares line fit; perpendicular distance from the spin center to it
    cx, cy = x.mean(), y.mean()
    _, _, vt = np.linalg.svd(np.stack([x - cx, y - cy], 1), full_matrices=False)
    nx, ny = vt[-1]
    s = cx * nx + cy * ny                 # line: p . n = s ; closest point to the spin center = s * n
    perp = abs(s)
    resid = np.abs((x - cx) * nx + (y - cy) * ny)
    foot = np.degrees(np.arctan2(s * ny, s * nx)) % 360
    fine = per_degree(w, step=0.5)
    print(f"{a.name}: {len(w)} points within +-{a.window} deg of {a.angle:.1f}")
    print(f"  median distance        {np.median(w[:, 1]):7.1f} mm")
    print(f"  min of 0.5-deg medians {np.nanmin(fine):7.1f} mm")
    print(f"  line fit: perpendicular {perp:7.1f} mm at {foot:.1f} deg, residual rms {np.sqrt((resid**2).mean()):.1f} mm")


def cmd_rgb(a):
    import cv2
    from sensors.camera import Camera
    os.makedirs(CAL, exist_ok=True)
    c = Camera().start()
    t0 = time.monotonic()
    while c.frames < 45:   # ~2 s so auto-exposure settles
        if time.monotonic() - t0 > 10:
            sys.exit(f"camera not delivering: {c.snapshot()['status']}")
        time.sleep(0.05)
    frame = c.snapshot()["data"]
    c.stop()
    path = os.path.join(CAL, a.name + ".jpg")
    cv2.imwrite(path, frame)
    print(f"saved {path} {frame.shape[1]}x{frame.shape[0]}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("capture"); p.add_argument("name"); p.add_argument("--revs", type=int, default=30)
    p = sp.add_parser("diff"); p.add_argument("base"); p.add_argument("new"); p.add_argument("--drop", type=float, default=100)
    p = sp.add_parser("range"); p.add_argument("name"); p.add_argument("--angle", type=float, required=True)
    p.add_argument("--window", type=float, default=4)
    p = sp.add_parser("rgb"); p.add_argument("name")
    a = ap.parse_args()
    dict(capture=cmd_capture, diff=cmd_diff, range=cmd_range, rgb=cmd_rgb)[a.cmd](a)


if __name__ == "__main__":
    main()
