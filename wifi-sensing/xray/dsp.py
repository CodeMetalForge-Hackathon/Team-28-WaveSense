"""Signal processing for near-zone presence from a single CSI link (numpy only).

All functions take amplitude matrices shaped (time, subcarrier).
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def resample(times: np.ndarray, values: np.ndarray, t_end: float, duration: float, fs: float,
             max_gap: float = 0.5) -> tuple[np.ndarray, float]:
    """Linear-interpolate irregular samples onto a uniform grid ending at t_end.

    Returns (grid_values (n, K), coverage), where coverage is the fraction of grid
    points that have a real sample within max_gap seconds. ESP32 CSI arrives at an
    irregular rate, and spectral estimates need a uniform one.
    """
    n = int(round(duration * fs))
    grid = t_end - duration + (np.arange(n) + 1) / fs
    if len(times) < 2:
        return np.zeros((n, values.shape[1] if values.ndim == 2 else 0)), 0.0
    out = np.empty((n, values.shape[1]), dtype=np.float64)
    for k in range(values.shape[1]):
        out[:, k] = np.interp(grid, times, values[:, k])
    idx = np.clip(np.searchsorted(times, grid), 1, len(times) - 1)
    nearest = np.minimum(np.abs(grid - times[idx - 1]), np.abs(times[idx] - grid))
    return out, float(np.mean(nearest <= max_gap))


def active_subcarriers(amp: np.ndarray, rel: float = 0.1) -> np.ndarray:
    """Mask of subcarriers carrying signal (drops nulls, guards and dead bins)."""
    med = np.median(amp, axis=0)
    return med > rel * np.median(med[med > 0]) if np.any(med > 0) else np.zeros(amp.shape[1], bool)


def normalise(amp: np.ndarray) -> np.ndarray:
    """Divide each frame by its mean amplitude: cancels the ESP32's automatic gain steps."""
    return amp / np.maximum(amp.mean(axis=1, keepdims=True), 1e-9)


def hampel(x: np.ndarray, half: int = 3, n_sigma: float = 3.0) -> np.ndarray:
    """Replace outliers (> n_sigma robust deviations from the local median) per column."""
    if x.shape[0] < 2 * half + 1:
        return x.copy()
    pad = np.pad(x, ((half, half), (0, 0)), mode="edge")
    win = sliding_window_view(pad, 2 * half + 1, axis=0)          # (T, K, w)
    med = np.median(win, axis=-1)
    mad = 1.4826 * np.median(np.abs(win - med[..., None]), axis=-1)
    bad = np.abs(x - med) > n_sigma * np.maximum(mad, 1e-9)
    out = x.copy()
    out[bad] = med[bad]
    return out


def motion_energy(x: np.ndarray, fs: float, smooth_s: float = 1.5) -> float:
    """Mean over subcarriers of the std of the high-passed signal (fast fluctuations)."""
    w = max(3, int(round(smooth_s * fs)) | 1)
    if x.shape[0] <= w:
        return float("nan")
    kernel = np.ones(w) / w
    trend = np.stack([np.convolve(x[:, k], kernel, mode="same") for k in range(x.shape[1])], axis=1)
    hp = (x - trend)[w // 2: -(w // 2)]
    return float(np.mean(np.std(hp, axis=0)))


def _detrend(x: np.ndarray) -> np.ndarray:
    t = np.arange(x.shape[0], dtype=np.float64)
    a = np.vstack([t, np.ones_like(t)]).T
    coef, *_ = np.linalg.lstsq(a, x, rcond=None)
    return x - a @ coef


def breathing(x: np.ndarray, fs: float, f_lo: float = 0.12, f_hi: float = 0.6,
              top_k: int = 10) -> dict:
    """Look for a breathing-like periodicity (default 7-36 breaths/min).

    Picks the top_k subcarriers by in-band power, takes their first principal
    component, and reports the spectral peak with:
      snr          peak power / median power over 0.05-2 Hz, excluding the peak
      consistency  share of the chosen subcarriers whose own peak is within one bin
    A strong, consistent peak is breathing-LIKE. It is not proof of a breathing
    person: any slow periodic disturbance (a fan, a swaying object) also qualifies.
    """
    n = x.shape[0]
    if n < 2 * fs / f_lo or x.shape[1] == 0:            # need two periods of the slowest rate
        return dict(freq_hz=None, bpm=None, snr=0.0, consistency=0.0)
    xd = _detrend(x) * np.hanning(n)[:, None]
    spec = np.fft.rfft(xd, axis=0)
    f = np.fft.rfftfreq(n, 1 / fs)
    band = (f >= f_lo) & (f <= f_hi)
    if band.sum() < 2:
        return dict(freq_hz=None, bpm=None, snr=0.0, consistency=0.0)
    power = np.abs(spec) ** 2
    k = min(top_k, x.shape[1])
    chosen = np.argsort(power[band].sum(axis=0))[-k:]
    filt = spec[:, chosen] * band[:, None]
    bp = np.fft.irfft(filt, n=n, axis=0)
    _, _, vt = np.linalg.svd(bp - bp.mean(axis=0), full_matrices=False)
    # Learn the breathing direction from the band-passed data, but measure the
    # spectrum of the UNFILTERED signal along it: a band-passed series has no
    # out-of-band power, which made the noise floor zero and the SNR meaningless.
    full = xd[:, chosen] - xd[:, chosen].mean(axis=0)
    pspec = np.abs(np.fft.rfft(full @ vt[0])) ** 2
    ib = np.flatnonzero(band)
    peak = ib[np.argmax(pspec[ib])]
    ref = (f >= 0.05) & (f <= 2.0)
    ref[max(0, peak - 2): peak + 3] = False
    noise = np.median(pspec[ref]) if ref.any() else np.nan
    snr = float(pspec[max(0, peak - 1): peak + 2].max() / noise) if noise and noise > 0 else 0.0
    own = ib[np.argmax(power[ib][:, chosen], axis=0)]
    consistency = float(np.mean(np.abs(own - peak) <= 1))
    return dict(freq_hz=float(f[peak]), bpm=float(60 * f[peak]), snr=snr, consistency=consistency)


def profile(x: np.ndarray) -> np.ndarray:
    """Time-averaged normalised amplitude per subcarrier: the link's multipath 'shape'."""
    return x.mean(axis=0)


def profile_distance(p: np.ndarray, p0: np.ndarray, s0: np.ndarray) -> float:
    """Mean |p - p0| in units of the empty-zone variability s0 (robust z-score)."""
    return float(np.mean(np.abs(p - p0) / np.maximum(s0, 1e-6)))
