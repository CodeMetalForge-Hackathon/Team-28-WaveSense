#!/usr/bin/env python3
"""Grab HT-203U thermal frames and save a colormapped PNG.

Decoding follows https://github.com/Krastanov/ht-203u-linux (THERMOMETRY.md,
temperatures.py). The camera's default UVC mode is 256x392 "YUYV", 200,704
bytes per frame, but the buffer holds three things:
  rows   0-191  256x192 little-endian uint16 raw sensor counts
  row    193    starts with 0xAABBCCDD (layout marker)
  rows 196-387  256x192 YUYV preview image
  row    388    camera summary: FFFF EEEE, max code, max x, max y,
                min code, min x, min y, mean code, center code
                (codes -> deg C as code/64 - 50; x, y normalised 0..1000)
The summary only updates ~2.5 Hz, so grab ~25 frames and use the last valid one.
Formats must NOT be set (v4l2-ctl --set-fmt-video / ffmpeg fail on this
firmware); we stream the mode the camera is already in.

Temperatures are the camera's own uncalibrated estimates, not verified.

Usage:
  python3 thermal_grab.py --device /dev/v4l/by-id/usb-HIK_HikCamera_*-video-index0 \
      --out thermal.png [--save-raw hand.raw] [--baseline base.raw]
  python3 thermal_grab.py --input hand.raw --out hand.png      # decode a saved frame

Raw counts are not comparable between grabs (the camera rescales them with its
internal state), so the hand check uses the hottest region within one frame and
the camera's own max-temperature position, not a raw-count difference.
"""
import argparse, glob, re, struct, subprocess, sys, time
import numpy as np
from PIL import Image, ImageDraw

W, H = 256, 192
FRAME = 256 * 392 * 2


def find_device():
    hits = glob.glob("/dev/v4l/by-id/usb-HIK_HikCamera_*-video-index0")
    if len(hits) != 1:
        sys.exit(f"expected one HikCamera video-index0 node, found {hits}")
    return hits[0]


def grab(device, count):
    fmt = subprocess.run(["v4l2-ctl", "-d", device, "--get-fmt-video"],
                         capture_output=True, text=True, check=True).stdout
    if not re.search(r"Width/Height\s*:\s*256/392\s", fmt) or "'YUYV'" not in fmt:
        sys.exit("camera is not in its default 256x392 YUYV mode:\n" + fmt)
    t0 = time.monotonic()
    r = subprocess.run(["v4l2-ctl", "-d", device, "--stream-mmap=4", f"--stream-count={count}",
                        "--stream-to=-"], capture_output=True, timeout=20)
    dt = time.monotonic() - t0
    if r.returncode:
        sys.exit("v4l2-ctl failed: " + r.stderr.decode(errors="replace")[-500:])
    data = r.stdout
    if len(data) % FRAME:
        sys.exit(f"got {len(data)} bytes, not a whole number of {FRAME}-byte frames")
    return [data[i:i + FRAME] for i in range(0, len(data), FRAME)], dt


def decode(frame):
    if struct.unpack_from("<I", frame, 193 * 512)[0] != 0xAABBCCDD:
        raise ValueError("layout marker 0xAABBCCDD missing at row 193")
    tail = struct.unpack_from("<10H", frame, 388 * 512)
    if tail[:2] != (0xFFFF, 0xEEEE):
        raise ValueError("summary signature FFFF EEEE missing at row 388")
    counts = np.frombuffer(frame, dtype="<u2", count=W * H).reshape(H, W)
    summary = None
    if any(tail[2:]):
        c = lambda code: code / 64.0 - 50.0
        summary = dict(max_c=c(tail[2]), max_xy=(round(tail[3] * (W - 1) / 1000), round(tail[4] * (H - 1) / 1000)),
                       min_c=c(tail[5]), min_xy=(round(tail[6] * (W - 1) / 1000), round(tail[7] * (H - 1) / 1000)),
                       mean_c=c(tail[8]), center_c=c(tail[9]))
    return counts, summary


def colormap(img01):
    """Small inferno-like colormap without matplotlib: black-purple-red-orange-yellow-white."""
    stops = np.array([[0, 0, 4], [87, 16, 110], [188, 55, 84], [249, 142, 9], [252, 255, 164]], float)
    x = np.clip(img01, 0, 1) * (len(stops) - 1)
    i = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - i)[..., None]
    return (stops[i] * (1 - f) + stops[i + 1] * f).astype(np.uint8)


def region(xy):
    x, y = xy
    col = "left" if x < W / 3 else "right" if x > 2 * W / 3 else "middle"
    row = "top" if y < H / 3 else "bottom" if y > 2 * H / 3 else "middle"
    return f"{row}-{col}" if (row, col) != ("middle", "middle") else "center"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None)
    ap.add_argument("--frames", type=int, default=25)
    ap.add_argument("--out", default="thermal.png")
    ap.add_argument("--save-raw", help="save the decoded frame's raw 200,704 bytes here")
    ap.add_argument("--baseline", help="raw frame from an earlier grab, to compare hottest positions")
    ap.add_argument("--input", help="decode this saved raw frame instead of grabbing")
    a = ap.parse_args()
    if a.input:
        frames = [open(a.input, "rb").read()]
        print(f"decoding saved frame {a.input} ({len(frames[0])} bytes)")
    else:
        dev = a.device or find_device()
        frames, dt = grab(dev, a.frames)
        print(f"{len(frames)} frames of {FRAME} bytes in {dt:.2f} s from {dev}"
              f" (~{len(frames) / dt:.1f} fps incl. stream start)")
    chosen = None
    for fr in frames:  # keep the last frame that has a summary
        counts, summary = decode(fr)
        if summary:
            chosen = (fr, counts, summary)
    if not chosen:
        fr = frames[-1]
        chosen = (fr, *decode(fr))
    fr, counts, summary = chosen
    if a.save_raw:
        open(a.save_raw, "wb").write(fr)

    lo, hi = np.percentile(counts, [1, 99.5])
    rgb = colormap((counts.astype(float) - lo) / max(hi - lo, 1))
    hot_y, hot_x = np.unravel_index(np.argmax(counts), counts.shape)
    im = Image.fromarray(rgb).resize((W * 3, H * 3), Image.NEAREST)
    d = ImageDraw.Draw(im)
    d.ellipse([hot_x * 3 - 9, hot_y * 3 - 9, hot_x * 3 + 9, hot_y * 3 + 9], outline=(0, 255, 0), width=2)
    im.save(a.out)
    print(f"saved {a.out} (768x576 colormap of the 256x192 sensor counts; green ring = hottest pixel)")
    print(f"sensor counts: min {counts.min()}, max {counts.max()}, hottest pixel at x={hot_x}, y={hot_y}"
          f" ({region((hot_x, hot_y))} of the image)")
    if summary:
        print(f"camera summary (uncalibrated): max {summary['max_c']:.1f} C at {summary['max_xy']}"
              f" ({region(summary['max_xy'])}), min {summary['min_c']:.1f} C at {summary['min_xy']},"
              f" center {summary['center_c']:.1f} C, mean {summary['mean_c']:.1f} C")
    else:
        print("camera summary: not populated in these frames")

    # hottest region: the warmest 2 % of pixels in this frame
    m = counts >= np.percentile(counts, 98)
    ys, xs = np.nonzero(m)
    cx, cy = int(xs.mean()), int(ys.mean())
    print(f"hottest 2 % of pixels: bbox x {xs.min()}-{xs.max()}, y {ys.min()}-{ys.max()},"
          f" centroid ({cx}, {cy}) = {region((cx, cy))} of the image")
    if a.baseline:
        base, bsum = decode(open(a.baseline, "rb").read())
        by, bx = np.unravel_index(np.argmax(base), base.shape)
        msg = f"baseline hottest pixel ({bx}, {by}) = {region((bx, by))}"
        if bsum:
            msg += f"; baseline camera max {bsum['max_c']:.1f} C at {bsum['max_xy']}"
        print(msg)

if __name__ == "__main__":
    main()
