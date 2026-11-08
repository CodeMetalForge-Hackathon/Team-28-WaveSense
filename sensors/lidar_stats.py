#!/usr/bin/env python3
"""Summarise RPLIDAR ultra_simple output.

Live run (starts ultra_simple, saves its raw output, times each revolution,
then stops it with SIGINT so it can stop the scan and motor itself):
  python3 lidar_stats.py --run PORT --ultra-simple PATH --out lidar.txt [--seconds 8]
Offline (no rev/s, file only):
  python3 lidar_stats.py lidar.txt
Compare a hand scan against a baseline scan:
  python3 lidar_stats.py hand.txt --baseline lidar.txt

ultra_simple prints one line per point: "[S ] theta: A Dist: D Q: q",
where "S" marks the first point of a new revolution.
"""
import argparse, os, re, signal, statistics, subprocess, sys, threading, time

LINE = re.compile(r"^(S )?\s*theta:\s*([\d.]+)\s+Dist:\s*([\d.]+)\s+Q:\s*(\d+)")


def parse(lines, stamps=None):
    revs, cur, header = [], None, []
    for i, ln in enumerate(lines):
        m = LINE.match(ln)
        if not m:
            if ln.strip() and cur is None:
                header.append(ln.rstrip())
            continue
        if m.group(1):  # sync: a new revolution starts
            if cur is not None:
                revs.append(cur)
            cur = []
        if cur is not None:
            cur.append((float(m.group(2)), float(m.group(3)), int(m.group(4))))
    return header, revs  # trailing partial revolution is dropped


def per_degree(revs):
    """Median distance (mm) per 1-degree bin, valid points only."""
    bins = {}
    for rev in revs:
        for a, d, q in rev:
            if q > 0 and d > 0:
                bins.setdefault(int(a) % 360, []).append(d)
    return {k: statistics.median(v) for k, v in bins.items()}


def run(port, exe, seconds, out_path):
    """Run ultra_simple, timestamp sync lines, stop it with SIGINT only."""
    lines, sync_times = [], []
    proc = subprocess.Popen(["stdbuf", "-oL", exe, "--channel", "--serial", port, "460800"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def reader():
        for ln in proc.stdout:
            lines.append(ln)
            if ln.startswith("S "):
                sync_times.append(time.monotonic())

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(seconds)
    proc.send_signal(signal.SIGINT)  # ultra_simple's handler: stop scan, motor off, exit
    t_int = time.monotonic()
    # Closing the CP2102N port takes ~5 s (one USB control request times out in
    # the kernel's cp210x close path), so allow 12 s before giving up.
    try:
        rc = proc.wait(timeout=12)
        how = f"exited {time.monotonic() - t_int:.1f} s after SIGINT"
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = proc.wait()
        how = "DID NOT EXIT within 12 s of SIGINT, killed"
    t.join(2)
    with open(out_path, "w") as f:
        f.writelines(lines)
    print(f"ultra_simple {how} (rc={rc}); {len(lines)} lines saved to {out_path}")
    return lines, sync_times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="saved ultra_simple output")
    ap.add_argument("--run", metavar="PORT", help="serial port of the LiDAR (use /dev/serial/by-id/...)")
    ap.add_argument("--ultra-simple", default=os.path.expanduser(
        "~/vendor/rplidar_sdk/output/Linux/Release/ultra_simple"))
    ap.add_argument("--seconds", type=float, default=8)
    ap.add_argument("--out", default="lidar.txt")
    ap.add_argument("--baseline", help="earlier scan to compare against (hand test)")
    a = ap.parse_args()

    sync_times = []
    if a.run:
        lines, sync_times = run(a.run, a.ultra_simple, a.seconds, a.out)
    else:
        lines = open(a.file).read().splitlines()

    header, revs = parse(lines)
    for h in header:
        print("|", h)
    if not revs:
        sys.exit("no complete revolutions found")
    pts = [p for r in revs for p in r]
    good = sum(1 for _, d, q in pts if q > 0)
    print(f"complete revolutions : {len(revs)}")
    print(f"points/revolution    : median {statistics.median(len(r) for r in revs):.0f}"
          f" (min {min(len(r) for r in revs)}, max {max(len(r) for r in revs)})")
    print(f"points with Q > 0    : {good}/{len(pts)} = {100 * good / len(pts):.1f} %")
    if len(sync_times) >= 3:
        gaps = [b - t for t, b in zip(sync_times, sync_times[1:])]
        span = sync_times[-1] - sync_times[0]
        print(f"revolutions/second   : {(len(sync_times) - 1) / span:.2f} over {span:.2f} s"
              f" (median period {1000 * statistics.median(gaps):.1f} ms)")
    valid = [(a_, d) for a_, d, q in pts if q > 0 and d > 0]
    if valid:
        a_, d = min(valid, key=lambda x: x[1])
        print(f"closest valid point  : {d:.0f} mm at {a_:.1f} deg")

    if a.baseline:
        _, base_revs = parse(open(a.baseline).read().splitlines())
        base, now = per_degree(base_revs), per_degree(revs)
        # bins that got at least 100 mm closer than the baseline
        closer = sorted((now[k], k, base[k]) for k in now if k in base and base[k] - now[k] > 100)
        print(f"bins >=100 mm closer than baseline: {len(closer)}")
        for dist, deg, was in closer[:8]:
            print(f"  {deg:3d} deg: {dist:6.0f} mm now, {was:6.0f} mm baseline")


if __name__ == "__main__":
    main()
