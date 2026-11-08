"""Near-zone presence engine for one ESP32 CSI link.

    engine = NearZoneEngine()                   # or NearZoneEngine(calibration=Calibration.load(path))
    engine.feed(frame, t)                       # every parsed CsiFrame, t = receive time in seconds
    report = engine.assess(t)                   # about once per second

Calibration: record 60-120 s with the NEAR ZONE EMPTY (no one within ~1.5 m of
the link). Background people may keep moving; that is the point, since
thresholds are learned from that environment:

    engine.start_calibration(); ... feed ...; cal = engine.finish_calibration(); cal.save(path)

Evidence (all relative to the calibration):
  profile   a body near the link changes the time-averaged multipath "shape",
            even when perfectly still (the case of an unconscious person)
  breathing a consistent 0.12-0.6 Hz periodicity across the strongest subcarriers
  motion    fast fluctuations: anyone moving within radio range, near or far

The output is a CANDIDATE, for a human to verify. One link cannot localise,
cannot see through rubble reliably, and does not count people unless it has been
trained and measured on labelled recordings (see evaluate.py).
"""
from __future__ import annotations

import collections
import json
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from . import dsp

FS = 10.0                 # analysis rate after resampling (Hz)
BREATH_WIN = 30.0         # s, breathing spectrum window
MOTION_WIN = 4.0          # s
PROFILE_WIN = 5.0         # s
MIN_COVERAGE = 0.7        # share of the window that must hold real samples
MIN_RATE_HZ = 5.0         # below this the link is too sparse to judge


@dataclass
class Calibration:
    mask: list                    # active subcarriers (bool per subcarrier)
    profile: list                 # median empty-zone profile (active subcarriers only)
    sigma: list                   # robust spread of the profile per subcarrier
    motion_median: float
    motion_p95: float
    breath_snr_p95: float
    profile_dist_p95: float
    windows: int
    duration_s: float
    rate_hz: float
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    note: str = "near zone empty; background activity allowed"

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=1)

    @classmethod
    def load(cls, path: str) -> "Calibration":
        with open(path) as f:
            return cls(**json.load(f))


class NearZoneEngine:
    # decision thresholds: multiples of the calibration's own 95th percentile, with floors
    PROFILE_K, PROFILE_FLOOR = 2.0, 4.0
    # BREATH_FLOOR: pure-noise SNR measured median 11, p95 23, max 28 over 200 synthetic trials
    BREATH_K, BREATH_FLOOR, BREATH_CONSISTENCY = 1.5, 30.0, 0.5
    MOTION_HIGH = 4.0             # motion / calibration median above which breathing is not judged
    ON_COUNT, OFF_COUNT = 3, 10   # hysteresis, in assess() calls (~seconds)

    def __init__(self, calibration: Calibration | None = None, max_rate_hz: float = 100.0):
        self.cal = calibration
        self.buf = collections.deque(maxlen=int(BREATH_WIN * max_rate_hz * 1.5))
        self.rssi = collections.deque(maxlen=200)
        self._calib: list | None = None
        self._calib_mask: np.ndarray | None = None
        self._on = self._off = 0
        self.occupied = False

    # ---- input ---------------------------------------------------------------
    def feed(self, frame, t: float) -> None:
        amp = frame.amplitude
        if self.buf and len(self.buf[-1][1]) != len(amp):
            self.buf.clear()                       # subcarrier layout changed (e.g. HT -> HE)
        self.buf.append((t, amp))
        self.rssi.append(frame.rssi)

    # ---- features --------------------------------------------------------------
    def features(self, now: float) -> dict | None:
        recent = [(t, a) for t, a in self.buf if now - BREATH_WIN <= t <= now]
        if len(recent) < 10:
            return None
        times = np.array([t for t, _ in recent])
        amp = np.stack([a for _, a in recent]).astype(np.float64)
        rate = (len(recent) - 1) / max(times[-1] - times[0], 1e-6)
        if self.cal is not None:
            mask = np.array(self.cal.mask, bool)
        elif self._calib is not None:
            if self._calib_mask is None or self._calib_mask.size != amp.shape[1]:
                self._calib_mask = dsp.active_subcarriers(amp)      # frozen for the whole calibration
            mask = self._calib_mask
        else:
            mask = dsp.active_subcarriers(amp)
        if mask.size != amp.shape[1] or mask.sum() < 4:
            return None
        grid, coverage = dsp.resample(times, amp[:, mask], now, BREATH_WIN, FS)
        x = dsp.hampel(dsp.normalise(grid))
        m = int(MOTION_WIN * FS)
        p = int(PROFILE_WIN * FS)
        feat = dict(rate_hz=rate, coverage=coverage, n_subcarriers=int(mask.sum()),
                    rssi_dbm=float(np.mean(self.rssi)) if self.rssi else None,
                    motion=dsp.motion_energy(x[-m:], FS),
                    profile=dsp.profile(x[-p:]))
        feat.update({f"breath_{k}": v for k, v in dsp.breathing(x, FS).items()})
        feat["valid"] = bool(coverage >= MIN_COVERAGE and rate >= MIN_RATE_HZ)
        return feat

    # ---- calibration -------------------------------------------------------------
    def start_calibration(self) -> None:
        self._calib, self._calib_mask = [], None

    def calibrating(self) -> bool:
        return self._calib is not None

    def finish_calibration(self) -> Calibration:
        rows = [r for r in (self._calib or []) if r["valid"]]
        self._calib = None
        if len(rows) < 30:
            raise ValueError(f"only {len(rows)} valid calibration windows; need at least 30 (about 60 s after a 30 s warm-up)")
        prof = np.stack([r["profile"] for r in rows])
        p0 = np.median(prof, axis=0)
        s0 = np.maximum(1.4826 * np.median(np.abs(prof - p0), axis=0), 0.002)
        dist = [dsp.profile_distance(r["profile"], p0, s0) for r in rows]
        motion = np.array([r["motion"] for r in rows])
        snr = np.array([r["breath_snr"] for r in rows])
        mask = self._calib_mask
        return Calibration(mask=mask.tolist(), profile=p0.tolist(), sigma=s0.tolist(),
                           motion_median=float(np.median(motion)), motion_p95=float(np.percentile(motion, 95)),
                           breath_snr_p95=float(np.percentile(snr, 95)),
                           profile_dist_p95=float(np.percentile(dist, 95)),
                           windows=len(rows), duration_s=float(len(rows)),
                           rate_hz=float(np.median([r["rate_hz"] for r in rows])))

    # ---- decision ------------------------------------------------------------------
    def assess(self, now: float) -> dict:
        f = self.features(now)
        if f is None:
            return dict(state="no_data", valid=False, detail="waiting for CSI frames", t=now)
        if self._calib is not None:
            self._calib.append(f)
            return dict(state="calibrating", valid=f["valid"], windows=len(self._calib), t=now,
                        rate_hz=round(f["rate_hz"], 1), coverage=round(f["coverage"], 2))
        out = dict(t=now, valid=f["valid"], rate_hz=round(f["rate_hz"], 1), coverage=round(f["coverage"], 2),
                   rssi_dbm=f["rssi_dbm"], n_subcarriers=f["n_subcarriers"],
                   breathing_bpm=f["breath_bpm"], breathing_snr=round(f["breath_snr"], 2),
                   breathing_consistency=round(f["breath_consistency"], 2), calibrated=self.cal is not None)
        if not f["valid"]:
            out.update(state="poor_link", detail="too few CSI frames to judge")
            return out
        if self.cal is None:
            out.update(state="uncalibrated", detail="calibrate with the near zone empty first")
            return out
        c = self.cal
        dist = dsp.profile_distance(f["profile"], np.array(c.profile), np.array(c.sigma))
        motion_ratio = f["motion"] / max(c.motion_median, 1e-9)
        prof_thr = max(self.PROFILE_FLOOR, self.PROFILE_K * c.profile_dist_p95)
        breath_thr = max(self.BREATH_FLOOR, self.BREATH_K * c.breath_snr_p95)
        profile_hit = dist > prof_thr
        breath_hit = (f["breath_snr"] > breath_thr and f["breath_consistency"] >= self.BREATH_CONSISTENCY
                      and motion_ratio < self.MOTION_HIGH)
        raw = profile_hit or breath_hit
        self._on, self._off = (self._on + 1, 0) if raw else (0, self._off + 1)
        if not self.occupied and self._on >= self.ON_COUNT:
            self.occupied = True
        elif self.occupied and self._off >= self.OFF_COUNT:
            self.occupied = False
        out.update(state="candidate_present" if self.occupied else "no_candidate",
                   evidence=dict(profile=bool(profile_hit), breathing=bool(breath_hit)),
                   profile_distance=round(dist, 2), profile_threshold=round(prof_thr, 2),
                   breathing_threshold=round(breath_thr, 2), motion_ratio=round(motion_ratio, 2),
                   motion=("high" if motion_ratio >= self.MOTION_HIGH else "some" if motion_ratio >= 2 else "low"),
                   label="CANDIDATE near the sensor: verify. Not a confirmed person; no location; no count.")
        return out
