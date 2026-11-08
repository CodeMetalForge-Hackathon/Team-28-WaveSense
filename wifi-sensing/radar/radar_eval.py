#!/usr/bin/env python3
"""Replay a .radrec recording through RadarEngine and MEASURE it, by clock-time segments.

    python3 radar_eval.py REC --cal-span 23:22:00-23:23:00 \\
        --seg empty=23:22:00-23:23:00 --seg one=23:23:00-23:24:00 --seg empty=23:24:00-23:25:00

Calibrates on --cal-span (nobody in view), then scores each segment second by second: share of
seconds with a candidate, first candidate time, ranges. The first SKIP_S seconds of each segment
are reported separately (people moving in or out, and the engine's hysteresis).
Results are measured on that recording only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from radar_engine import RadarEngine  # noqa: E402
from radar_service import read_radrec  # noqa: E402
from tinyrad_dev import TinyRadDevice  # noqa: E402

SKIP_S = 10.0


def span(day_t: float, text: str) -> tuple[float, float]:
    base = datetime.datetime.fromtimestamp(day_t)
    def one(h):
        H, M, S = map(int, h.split(":"))
        return base.replace(hour=H, minute=M, second=S, microsecond=0).timestamp()
    a, b = text.split("-")
    return one(a), one(b)


def replay(frames, eng: RadarEngine, a: float, b: float, calibrate: bool = False) -> list[dict]:
    """Feed frames from WIN_S before a (pre-roll, if the recording has it) to b; assess once a second
    from a. With calibrate=True, calibration windows start only at a + WIN_S, so every calibration
    window lies entirely inside [a, b)."""
    reps, last, calibrating = [], None, False
    for t, fr in frames:
        if t < a - RadarEngine.WIN_S:
            continue
        if t >= b:
            break
        eng.feed(t, fr)
        if calibrate and not calibrating and t >= a + RadarEngine.WIN_S:
            eng.start_calibration()
            calibrating = True
        if t >= a and (last is None or t - last >= 1.0) and (calibrating or not calibrate):
            reps.append(eng.assess(t))
            last = t
    return reps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rec")
    ap.add_argument("--cal-span", required=True)
    ap.add_argument("--seg", action="append", default=[], help="LABEL=HH:MM:SS-HH:MM:SS")
    ap.add_argument("--near-range-m", type=float, default=2.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    frames = list(read_radrec(a.rec))
    t0 = frames[0][0]
    r_axis = TinyRadDevice().range_axis(RadarEngine.NFFT)
    ca, cb = span(t0, a.cal_span)
    eng = RadarEngine(r_axis, near_range_m=a.near_range_m)
    replay(frames, eng, ca, cb, calibrate=True)
    cal = eng.finish_calibration()
    out = dict(calibration=dict(span=a.cal_span, windows=cal.windows, rate_hz=round(cal.rate_hz, 1)), segments=[])
    for s in a.seg:
        label, text = s.split("=", 1)
        sa, sb = span(t0, text)
        e = RadarEngine(r_axis, cal, a.near_range_m)
        reps = [r for r in replay(frames, e, sa, sb) if r.get("state") in ("candidate_present", "no_candidate")]
        core = [r for r in reps if r["t"] >= sa + SKIP_S]
        cand = [r for r in reps if r["state"] == "candidate_present"]
        first = next((r["t"] - sa for r in cand), None)
        ranges = [r["range_m"] for r in cand]
        out["segments"].append(dict(
            label=label, span=text, judged=len(reps),
            candidate_s=len(cand), candidate_pct=round(100 * len(cand) / len(reps), 1) if reps else None,
            after_skip_pct=round(100 * sum(r["state"] == "candidate_present" for r in core) / len(core), 1) if core else None,
            first_candidate_s=None if first is None else round(first, 1),
            median_range_m=round(float(np.median(ranges)), 2) if ranges else None,
            kinds=sorted({r.get("kind") for r in cand if r.get("kind")}),
            median_bpm=(round(float(np.median([r["breathing_bpm"] for r in cand if r.get("breathing_bpm")])), 1)
                        if any(r.get("breathing_bpm") for r in cand) else None),
            median_presence_ratio=round(float(np.median([r["presence_ratio"] for r in reps])), 2) if reps else None))
    if a.json:
        print(json.dumps(out, indent=1))
        return
    print(f"calibration {a.cal_span}: {cal.windows} windows at {cal.rate_hz:.1f} Hz (measured, this recording)")
    for g in out["segments"]:
        print(f"{g['span']} {g['label']:6s} candidate {g['candidate_s']}/{g['judged']} s ({g['candidate_pct']}%), "
              f"after first {SKIP_S:.0f} s {g['after_skip_pct']}% | first +{g['first_candidate_s']} s | "
              f"range {g['median_range_m']} m | {', '.join(g['kinds']) or '-'} | bpm {g['median_bpm']} | "
              f"presence ratio median {g['median_presence_ratio']}")


if __name__ == "__main__":
    main()
