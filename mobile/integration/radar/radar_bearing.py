"""Direction of a moving or breathing reflector, from the clutter-removed slow-time signal.

Raw beamforming at a range bin points at whatever reflects most there, which is usually a wall or
furniture. Subtracting each channel's mean over the window removes everything static, so the
spatial covariance that remains belongs to what is changing: the candidate the engine reported.

Single TX, four RX in a line: one azimuth, no elevation, about 26 degrees of beamwidth. The peak of
one dominant source is much finer than the beamwidth, but two sources in one range bin merge, so
the estimate is withheld unless one source clearly dominates the covariance.

Sign and zero follow radar_scene.py. They are sensor-local until checked against a known position.
A bearing is an estimate for a human to verify, not a confirmed person.
"""
from __future__ import annotations

import numpy as np

ANGLES_DEG = np.arange(-60.0, 61.0, 1.0)
RX_SPACING_M = 0.006217         # ADI TinyRadTool RfGet('RxPosn')
MIN_DOMINANCE = 0.6             # share of dynamic power in the strongest eigenvalue
EDGE_DEG = 58.0                 # peaks pinned to the scan edge are not a measured direction


def estimate_bearing(x: np.ndarray, calibration, wavelength: float) -> dict | None:
    """Bearing from complex slow-time samples x, shaped (time, range bins, 4 RX), or None.

    calibration is the factory RX correction. Without it the channels' phase errors would be read
    as direction, so None is returned rather than a guess."""
    if calibration is None or not np.isfinite(wavelength) or wavelength <= 0:
        return None
    cal = np.asarray(calibration, complex)
    data = np.asarray(x, complex)
    if cal.shape != (4,) or data.ndim != 3 or data.shape[2] != 4 or data.shape[0] < 5 or not np.isfinite(data).all():
        return None
    dynamic = (data - data.mean(axis=0, keepdims=True)) * cal
    snapshots = dynamic.reshape(-1, 4)
    covariance = snapshots.T @ snapshots.conj() / len(snapshots)
    eigenvalues = np.linalg.eigvalsh(covariance)
    total = float(eigenvalues.sum())
    if total <= 1e-12:
        return None
    dominance = float(eigenvalues[-1]) / total
    if dominance < MIN_DOMINANCE:
        return None
    positions = np.arange(4) * RX_SPACING_M
    weights = np.exp(-2j * np.pi * np.sin(np.deg2rad(ANGLES_DEG))[:, None] * positions[None, :] / wavelength)
    power = np.real(np.einsum("ai,ij,aj->a", weights, covariance, weights.conj()))
    bearing = float(ANGLES_DEG[int(np.argmax(power))])
    if abs(bearing) >= EDGE_DEG:
        return None
    return {"bearing_deg": bearing, "dominance": round(dominance, 2)}
