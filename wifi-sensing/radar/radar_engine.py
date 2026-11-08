"""Person CANDIDATES from a 24 GHz FMCW radar (TinyRad): moving, or still but breathing.

    eng = RadarEngine(range_axis)          # metres per range bin (TinyRadDevice.range_axis(NFFT))
    eng.feed(t, frame)                     # every raw frame: int16 (N samples, n_rx)
    report = eng.assess(t)                 # about once per second

How it works
  - frames are averaged in blocks (250 fps -> 25 Hz slow time), range-FFT'd per RX channel
  - static reflections (walls, furniture, a chair) are removed by subtracting each bin's mean
    over the analysis window, so only things that CHANGE remain
  - per range bin: micro-motion energy in 0.1-2 Hz over 20 s (breathing, fidgeting) and fast
    motion energy over the last 2 s (walking), averaged over the RX channels
  - each bin is compared with ITS OWN empty-room calibration (95th/99th percentile), so the
    thresholds are learned for this room and placement
  - a candidate is reported with its range; within near_range_m it counts for the near zone

Limits: one radar gives range (0.6 m resolution) but no angle here, so two people at the same
distance merge, and the count is not reported. 24 GHz does not pass through concrete or rubble.
Anything that moves or vibrates (a fan, a swinging cable) can be a candidate. A candidate is
for a human to verify; no candidate is NOT evidence that nobody is there.
"""
from __future__ import annotations

import collections
import json
import os
import time
from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class RadarCalibration:
    pres_p99: list            # per range bin: 99th percentile of micro-motion energy, empty room
    mot_p99: list             # per range bin: 99th percentile of fast-motion energy, empty room
    pres_med: list
    windows: int
    rate_hz: float
    near_range_m: float
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    note: str = "radar field of view empty within the watched range"

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=1)

    @classmethod
    def load(cls, path: str) -> "RadarCalibration":
        with open(path) as f:
            return cls(**json.load(f))


def displacement_phase(z: np.ndarray) -> np.ndarray:
    """Unwrapped phase of a complex slow-time series around the centre of its I/Q arc.

    A reflector moving by d shifts the phase by 4*pi*d/lambda; static reflections in the same bin
    add a constant offset, so the samples lie on an arc around that offset. A least-squares circle
    fit (Kasa) estimates the centre; the phase around it is then linear in the displacement."""
    x, y = z.real.astype(float), z.imag.astype(float)
    A = np.column_stack([x, y, np.ones_like(x)])
    sol, *_ = np.linalg.lstsq(A, -(x * x + y * y), rcond=None)
    c = complex(-sol[0] / 2, -sol[1] / 2)
    return np.unwrap(np.angle(z - c))


class RadarEngine:
    DECIM = 10                     # raw frames per slow-time sample
    NFFT = 512
    WIN_S = 20.0                   # breathing-rate window (and buffer length)
    PRES_S = 10.0                  # micro-motion presence window: >= 2 breaths, and it clears ~10 s
                                   # after a person leaves (a 20 s window took ~35 s, measured 2026-11-07)
    MOTION_S = 2.0                 # fast-motion window
    PRES_BAND = (0.1, 2.0)         # Hz: breathing, fidgeting
    BREATH_BAND = (0.12, 0.6)      # Hz: 7-36 per minute
    R_MIN, R_MAX = 0.3, 5.0        # metres analysed (the first bins hold TX-RX leakage)
    K_PRES, K_MOT = 3.0, 3.0       # thresholds: multiples of the bin's calibration p99
    # "moving" vs "still": share of the bin's slow-time energy above FAST_HZ. Breathing (a few mm at
    # 0.1-0.6 Hz) stays below ~1.5 Hz even though it swings the 24 GHz phase by several radians;
    # walking puts Doppler far above the 12.5 Hz slow-time Nyquist, so it aliases across the band.
    FAST_HZ, FAST_SHARE = 3.0, 0.3
    BREATH_SHARE = 0.4             # share of 0.05-2 Hz displacement power in the breathing peak
    # Adaptive background (default): no calibration step. Per range bin, the typical empty-scene level
    # of micro-motion and motion energy is learned in the first LEARN_S seconds, then tracked in the
    # log domain: it falls with TAU_DOWN, rises with TAU_UP while the bin is below threshold, and only
    # leaks up with TAU_LEAK while the bin is above it, so a still person stays detected for many
    # minutes (a constant source such as a fan is absorbed after ~15-30 min). Measured 2026-11-07:
    # empty-room flicker drifted 2-5x over minutes, its p99 sat ~2x (micro-motion) and ~3x (motion)
    # above its median, and a seated person read 110-365x at 0.6-1.5 m.
    LEARN_S = 15
    # Learning-free "wobble" rule, active from the first second: a bin whose micro-motion power is
    # both > WOBBLE_ABS x this radar's own noise floor (device_floor.json, measured once) and
    # > WOBBLE_REL x its own static echo power wobbles like a body, not like a wall, a door or noise.
    # Measured 2026-11-07 (two scenes): 0% of empty seconds, 100% seated in the open, 60% behind a door.
    # Bins it flags are kept out of the adaptive learning, so people present at power-on are not learned.
    WOBBLE_ABS, WOBBLE_REL = 30.0, 3.0
    FLOOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_floor.json")
    TAU_DOWN, TAU_UP, TAU_LEAK = 20.0, 60.0, 900.0
    K_ADAPT_PRES, K_ADAPT_MOT = 6.0, 10.0      # ~3x the empty p99, as multiples of the tracked level
    ON_COUNT, OFF_COUNT = 3, 5     # hysteresis in assess() calls (~seconds)

    def __init__(self, range_axis: np.ndarray, calibration: RadarCalibration | None = None,
                 near_range_m: float = 2.0, adaptive: bool | None = None):
        self.r_axis = np.asarray(range_axis, float)
        self.bins = np.where((self.r_axis >= self.R_MIN) & (self.r_axis <= self.R_MAX))[0]
        self.cal, self.near = calibration, near_range_m
        self.adaptive = (calibration is None) if adaptive is None else adaptive
        self.bg_p = self.bg_m = None
        self._learn: list = []
        self._bg_t = None
        self.floor = self._load_floor()
        self._acc, self._acc_t = [], []
        self.buf = collections.deque(maxlen=int(self.WIN_S * 60))   # (t, complex bins x rx)
        self._calib: list | None = None
        self._on = self._off = 0
        self.occupied = False
        self.frames_in = 0

    # ---- input -----------------------------------------------------------------
    def feed(self, t: float, frame: np.ndarray) -> None:
        self.frames_in += 1
        self._acc.append(np.asarray(frame, np.float32))
        self._acc_t.append(t)
        if len(self._acc) >= self.DECIM:
            x = np.mean(self._acc, axis=0)                      # (N, rx)
            tt = float(np.mean(self._acc_t))
            self._acc, self._acc_t = [], []
            x = x - x.mean(axis=0, keepdims=True)
            w = np.hanning(x.shape[0])[:, None]
            spec = np.fft.rfft(x * w, n=self.NFFT, axis=0)      # (NFFT/2+1, rx)
            self.buf.append((tt, spec[self.bins].astype(np.complex64)))

    # ---- features --------------------------------------------------------------
    def features(self, now: float) -> dict | None:
        rec = [(t, s) for t, s in self.buf if now - self.WIN_S <= t <= now]
        if len(rec) < 20:
            return None
        T = np.array([t for t, _ in rec])
        X = np.stack([s for _, s in rec])                       # (time, bins, rx)
        rate = (len(T) - 1) / max(T[-1] - T[0], 1e-6)
        coverage = (T[-1] - T[0]) / self.WIN_S
        dyn = X - X.mean(axis=0, keepdims=True)                 # static clutter removed
        win = np.hanning(len(T))[:, None, None]
        P = np.abs(np.fft.fft(dyn * win, axis=0)) ** 2          # (freq, bins, rx), two-sided
        f = np.abs(np.fft.fftfreq(len(T), 1 / rate))
        band = (f >= self.PRES_BAND[0]) & (f <= self.PRES_BAND[1])
        m = T >= now - self.PRES_S                              # presence: last PRES_S seconds only
        dp = X[m] - X[m].mean(axis=0, keepdims=True)
        Pp = np.abs(np.fft.fft(dp * np.hanning(m.sum())[:, None, None], axis=0)) ** 2
        fp = np.abs(np.fft.fftfreq(int(m.sum()), 1 / rate))
        bandp = (fp >= self.PRES_BAND[0]) & (fp <= self.PRES_BAND[1])
        pres = Pp[bandp].sum(axis=0).mean(axis=-1) / m.sum()    # (bins,)
        stat = (np.abs(X[m].mean(axis=0)) ** 2).mean(axis=-1)   # static echo power per bin
        Pm = Pp.mean(axis=-1)
        fast_share = Pm[fp >= self.FAST_HZ].sum(axis=0) / np.maximum(Pm[fp >= self.PRES_BAND[0]].sum(axis=0), 1e-12)
        k = max(3, int(self.MOTION_S * rate))
        recent = X[-k:] - X[-k:].mean(axis=0, keepdims=True)
        mot = (np.abs(recent) ** 2).mean(axis=0).mean(axis=-1)  # (bins,)
        bb = (f >= self.BREATH_BAND[0]) & (f <= self.BREATH_BAND[1])
        Pb = P.mean(axis=-1)                                    # (freq, bins)
        fb = f[bb]
        breath_hz = fb[np.argmax(Pb[bb], axis=0)] if bb.any() else np.full(len(self.bins), np.nan)
        # periodicity: power in the peak +-1 bin over all power in 0.1-2 Hz
        peak_share = np.zeros(len(self.bins))
        if bb.any():
            pk = np.argmax(Pb[bb], axis=0)
            idx = np.where(bb)[0]
            for j in range(len(self.bins)):
                c = idx[pk[j]]
                peak_share[j] = Pb[max(c - 1, 0):c + 2, j].sum() / max(Pb[band, j].sum(), 1e-12)
        return dict(rate_hz=rate, coverage=coverage, pres=pres, mot=mot, breath_hz=breath_hz,
                    peak_share=peak_share, fast_share=fast_share, stat=stat, valid=bool(coverage >= 0.7 and rate >= 10),
                    _X=X, _T=T)

    def breathing(self, X: np.ndarray, T: np.ndarray, j: int) -> tuple[float | None, float]:
        """(breaths per minute or None, periodicity share) from the displacement phase of bin j."""
        rate = (len(T) - 1) / max(T[-1] - T[0], 1e-6)
        ph = []
        for ch in range(X.shape[2]):
            p = displacement_phase(X[:, j, ch])
            ph.append(p - np.polyval(np.polyfit(T - T[0], p, 1), T - T[0]))   # remove drift
        sig = np.mean(ph, axis=0)
        P = np.abs(np.fft.rfft(sig * np.hanning(len(sig)))) ** 2
        f = np.fft.rfftfreq(len(sig), 1 / rate)
        bb = (f >= self.BREATH_BAND[0]) & (f <= self.BREATH_BAND[1])
        wide = (f >= 0.05) & (f <= 2.0)
        if not bb.any():
            return None, 0.0
        c = np.where(bb)[0][np.argmax(P[bb])]
        share = float(P[max(c - 1, 0):c + 2].sum() / max(P[wide].sum(), 1e-12))
        return float(f[c] * 60), share

    def _load_floor(self):
        """Per-bin device noise floor of the micro-motion power, interpolated onto this range axis."""
        try:
            with open(self.FLOOR_FILE) as f:
                d = json.load(f)
            return np.interp(self.r_axis[self.bins], d["range_m"], d["floor"])
        except (OSError, KeyError, ValueError):
            return None

    # ---- adaptive background --------------------------------------------------------
    def reset_background(self) -> None:
        """Relearn the background from the next LEARN_S seconds (the manual 'calibrate' in adaptive mode)."""
        self.bg_p = self.bg_m = None
        self._learn, self._bg_t = [], None
        self.occupied, self._on, self._off = False, 0, 0

    def _adapt(self, bg: np.ndarray, x: np.ndarray, hit: np.ndarray, dt: float) -> np.ndarray:
        x = np.maximum(x, 1e-12)
        lb, lx = np.log(bg), np.log(x)
        down = 1 - np.exp(-dt / self.TAU_DOWN)
        up = 1 - np.exp(-dt / self.TAU_UP)
        new = np.where(lx < lb, lb + down * (lx - lb),
                       np.where(hit, np.minimum(lb + dt / self.TAU_LEAK, lx), lb + up * (lx - lb)))
        return np.exp(new)

    # ---- calibration -------------------------------------------------------------
    def start_calibration(self) -> None:
        self._calib = []

    def finish_calibration(self) -> RadarCalibration:
        rows = [r for r in (self._calib or []) if r["valid"]]
        self._calib = None
        if len(rows) < 30:
            raise ValueError(f"only {len(rows)} valid calibration windows; need 30 (about 50 s after a 15 s warm-up)")
        pres = np.stack([r["pres"] for r in rows]); mot = np.stack([r["mot"] for r in rows])
        return RadarCalibration(pres_p99=np.percentile(pres, 99, axis=0).tolist(),
                                mot_p99=np.percentile(mot, 99, axis=0).tolist(),
                                pres_med=np.median(pres, axis=0).tolist(), windows=len(rows),
                                rate_hz=float(np.median([r["rate_hz"] for r in rows])), near_range_m=self.near)

    # ---- decision ------------------------------------------------------------------
    def assess(self, now: float) -> dict:
        f = self.features(now)
        if f is None:
            return dict(state="no_data", valid=False, detail="waiting for radar frames", t=now)
        if self._calib is not None:
            self._calib.append(f)
            return dict(state="calibrating", valid=f["valid"], windows=len(self._calib), t=now,
                        rate_hz=round(f["rate_hz"], 1))
        out = dict(t=now, valid=f["valid"], rate_hz=round(f["rate_hz"], 1), coverage=round(f["coverage"], 2),
                   calibrated=self.cal is not None, near_range_m=self.near)
        if not f["valid"]:
            out.update(state="poor_link", detail="radar window not full yet")
            return out
        rng = self.r_axis[self.bins]
        near = rng <= self.near
        wob = np.zeros(len(self.bins), bool)
        if self.floor is not None:
            wob = (f["pres"] > self.WOBBLE_ABS * self.floor) & (f["pres"] > self.WOBBLE_REL * f["stat"])
        if self.adaptive:
            out["mode"] = "adaptive"
            if self.bg_p is None:
                self._learn.append((f["pres"], f["mot"], wob))
                if len(self._learn) < self.LEARN_S:
                    # the wobble rule judges while the background is learned
                    raw = bool((wob & near).any())
                    self._on, self._off = (self._on + 1, 0) if raw else (0, self._off + 1)
                    if not self.occupied and self._on >= self.ON_COUNT:
                        self.occupied = True
                    elif self.occupied and self._off >= self.OFF_COUNT:
                        self.occupied = False
                    jj = int(np.argmax(np.where(near & wob, f["pres"], 0)))
                    out.update(state="candidate_present" if self.occupied else "learning",
                               kind="person-like wobble" if self.occupied else None,
                               range_m=round(float(rng[jj]), 2) if self.occupied else None,
                               evidence=dict(wobble=raw), learning=f"{len(self._learn)}/{self.LEARN_S} s",
                               detail="learning this scene; body-like wobble is judged meanwhile",
                               label=("CANDIDATE near the radar: verify. Not a confirmed person; range only, no count."
                                      if self.occupied else "Learning the scene (15 s)."))
                    return out
                P = np.array([p for p, _, _ in self._learn]); M = np.array([m for _, m, _ in self._learn])
                W = np.array([w for _, _, w in self._learn])
                Pm = np.where(W, np.nan, P); Mm = np.where(W, np.nan, M)
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)      # all-NaN bins are handled below
                    bp, bm = np.nanmedian(Pm, axis=0), np.nanmedian(Mm, axis=0)
                # a bin flagged in every learning second starts at its own level / threshold, so it is
                # not instantly a candidate of the adaptive rule (the wobble rule still judges it)
                self.bg_p = np.where(np.isnan(bp), np.median(P, axis=0) / self.K_ADAPT_PRES, bp)
                self.bg_m = np.where(np.isnan(bm), np.median(M, axis=0) / self.K_ADAPT_MOT, bm)
                self._learn, self._bg_t = [], now
            pr = f["pres"] / np.maximum(self.bg_p, 1e-12)
            mr = f["mot"] / np.maximum(self.bg_m, 1e-12)
            kp, km = self.K_ADAPT_PRES, self.K_ADAPT_MOT
        else:
            out["mode"] = "calibrated"
            if self.cal is None:
                out.update(state="uncalibrated", detail="calibrate with nobody in the radar's view first")
                return out
            pr = f["pres"] / np.maximum(np.array(self.cal.pres_p99), 1e-12)
            mr = f["mot"] / np.maximum(np.array(self.cal.mot_p99), 1e-12)
            kp, km = self.K_PRES, self.K_MOT
        pres_hit = near & ((pr > kp) | wob)
        mot_hit = near & (mr > km)
        if self.adaptive:
            dt = max(now - (self._bg_t or now), 0.0) or 1.0
            self._bg_t = now
            self.bg_p = self._adapt(self.bg_p, f["pres"], (pr > kp) | wob, dt)
            self.bg_m = self._adapt(self.bg_m, f["mot"], (mr > km) | wob, dt)
        raw = bool(pres_hit.any() or mot_hit.any())
        self._on, self._off = (self._on + 1, 0) if raw else (0, self._off + 1)
        if not self.occupied and self._on >= self.ON_COUNT:
            self.occupied = True
        elif self.occupied and self._off >= self.OFF_COUNT:
            self.occupied = False
        score = np.where(near, np.maximum(pr, mr), 0)
        j = int(np.argmax(score))
        moving = bool((mot_hit | pres_hit).any() and f["fast_share"][j] > self.FAST_SHARE)
        bpm, share = self.breathing(f["_X"], f["_T"], j) if (raw and not moving) else (None, 0.0)
        breathing_like = bool(not moving and share >= self.BREATH_SHARE)
        kind = "moving" if moving else ("still, breathing-like" if breathing_like else "still, micro-motion")
        far = (~near) & ((pr > kp) | (mr > km))
        out.update(state="candidate_present" if self.occupied else "no_candidate",
                   evidence=dict(micro_motion=bool(pres_hit.any()), motion_energy=bool(mot_hit.any()), moving=moving,
                                 wobble=bool((wob & near).any())),
                   fast_share=round(float(f["fast_share"][j]), 2),
                   range_m=round(float(rng[j]), 2), kind=kind if self.occupied else None,
                   breathing_bpm=round(bpm, 1) if breathing_like and bpm else None,
                   breathing_periodicity=round(share, 2),
                   presence_ratio=round(float(pr[j]), 2), motion_ratio=round(float(mr[j]), 2),
                   threshold=kp,
                   candidate_ranges_m=[round(float(r), 1) for r in rng[pres_hit | mot_hit]],
                   beyond_near_ranges_m=[round(float(r), 1) for r in rng[far]][:8],
                   label=("CANDIDATE near the radar: verify. Not a confirmed person; range only, no count."
                          if self.occupied else "No candidate. This is NOT evidence that nobody is there."))
        return out
