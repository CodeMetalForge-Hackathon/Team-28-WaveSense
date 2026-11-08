#!/usr/bin/env python3
"""Measure the near-zone engine on labelled recordings (.csirec from receiver.py --record).

    python3 -m xray.evaluate --calibrate empty-crowd-A.csirec --save cal.json
    python3 -m xray.evaluate --cal cal.json empty-crowd-B.csirec one-seated-*.csirec two-seated-*.csirec

The label is the file-name prefix: empty* means nobody within ~1.5 m of the
link (others may move further away); one* / two* mean 1 / 2 people seated next
to it. NEVER calibrate on a file you also score: calibrate on one empty
recording, score the others.

Reported per file (after the 30 s warm-up):
  windows      one assessment per second of recording
  candidate %  share of windows the engine called candidate_present
  first        seconds until the first candidate_present
  evidence     median profile distance, breathing SNR and motion ratio
Summary: false-candidate rate on empty files, detection rate on occupied files.
These are MEASURED numbers only for the recordings given: different rooms,
crowds and placements need their own recordings.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xray.csi_packet import parse_frame  # noqa: E402
from xray.engine import BREATH_WIN, Calibration, NearZoneEngine  # noqa: E402
from xray.receiver import read_recording  # noqa: E402


def label_of(path: str) -> str:
    name = os.path.basename(path).lower()
    for lab in ("empty", "one", "two"):
        if name.startswith(lab):
            return lab
    return "unlabelled"


def frames(path: str):
    for t, data in read_recording(path):
        f = parse_frame(data)
        if f is not None:
            yield t, f


def calibrate(path: str, node: int | None = None) -> Calibration:
    eng, started, t_first, last = NearZoneEngine(), False, None, None
    for t, f in frames(path):
        if node is not None and f.node_id != node:
            continue
        t_first = t if t_first is None else t_first
        eng.feed(f, t)
        if not started and t - t_first >= BREATH_WIN:
            eng.start_calibration()
            started, last = True, t
        if started and t - last >= 1.0:
            eng.assess(t)
            last = t
    if not started:
        raise SystemExit(f"{path}: shorter than the {BREATH_WIN:.0f} s warm-up; record at least 2 minutes")
    return eng.finish_calibration()


def score(path: str, cal: Calibration, node: int | None = None) -> dict:
    eng, t_first, last, reps = NearZoneEngine(calibration=cal), None, None, []
    for t, f in frames(path):
        if node is not None and f.node_id != node:
            continue
        t_first = t if t_first is None else t_first
        eng.feed(f, t)
        if t - t_first >= BREATH_WIN and (last is None or t - last >= 1.0):
            reps.append(eng.assess(t))
            last = t
    judged = [r for r in reps if r["state"] in ("candidate_present", "no_candidate")]
    cand = [r for r in judged if r["state"] == "candidate_present"]
    first = next((r["t"] - t_first for r in judged if r["state"] == "candidate_present"), None)
    med = lambda k: float(np.median([r[k] for r in judged])) if judged else None
    return dict(file=os.path.basename(path), label=label_of(path), windows=len(reps), judged=len(judged),
                poor_link=sum(r["state"] == "poor_link" for r in reps),
                candidate_pct=round(100 * len(cand) / len(judged), 1) if judged else None,
                first_candidate_s=None if first is None else round(first, 1),
                median_profile_distance=med("profile_distance"), median_breathing_snr=med("breathing_snr"),
                median_motion_ratio=med("motion_ratio"),
                breathing_evidence_pct=round(100 * np.mean([r["evidence"]["breathing"] for r in judged]), 1) if judged else None)


def summarise(rows: list[dict]) -> dict:
    def pooled(label):
        sel = [r for r in rows if r["label"] == label and r["judged"]]
        n = sum(r["judged"] for r in sel)
        return None if n == 0 else round(sum(r["candidate_pct"] * r["judged"] for r in sel) / n, 1)
    return dict(false_candidate_pct_empty=pooled("empty"), detection_pct_one=pooled("one"),
                detection_pct_two=pooled("two"), files=len(rows),
                note="measured on these recordings only; one link; candidate = for a human to verify")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Measure the near-zone engine on labelled .csirec recordings")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--calibrate", help="empty-zone recording to calibrate from")
    ap.add_argument("--save", help="write the calibration JSON here")
    ap.add_argument("--cal", help="calibration JSON to score with")
    ap.add_argument("--node", type=int, help="only this node id")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.calibrate:
        cal = calibrate(a.calibrate, a.node)
        if a.save:
            cal.save(a.save)
        print(f"calibrated on {os.path.basename(a.calibrate)}: {cal.windows} windows, {cal.rate_hz:.1f} fps, "
              f"profile p95 {cal.profile_dist_p95:.2f}, breathing SNR p95 {cal.breath_snr_p95:.1f}")
        if a.calibrate in a.files:
            raise SystemExit("refusing to score the calibration file itself")
    elif a.cal:
        cal = Calibration.load(a.cal)
    else:
        ap.error("give --calibrate EMPTY_RECORDING or --cal CALIBRATION.json")
    rows = [score(f, cal, a.node) for f in a.files]
    summary = summarise(rows) if rows else {}
    if a.json:
        print(json.dumps(dict(rows=rows, summary=summary), indent=1))
        return 0
    for r in rows:
        print(f"{r['file']:<38} {r['label']:<6} judged {r['judged']:>4}  candidate {r['candidate_pct']}%  "
              f"first {r['first_candidate_s']} s  profile {r['median_profile_distance']}  "
              f"breath SNR {r['median_breathing_snr']}  motion {r['median_motion_ratio']}  poor {r['poor_link']}")
    if summary:
        print("summary:", json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
