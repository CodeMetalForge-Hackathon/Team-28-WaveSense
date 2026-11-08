"""SYNTHETIC CSI for unit tests only. Never present output of this file as measurements.

A simple multipath model of one 2.4 GHz HT20 link (64 subcarriers, 56 used):
  H_k(t) = gain(t) * [ sum_static a_p e^{-j2pi f_k tau_p}  + people ] + noise
People are extra reflection paths whose length changes over time:
  near_still   strong path, length oscillating +-5 mm at the breathing rate
  near_walker  strong path moving 0.5 m/s
  far_walker   weak path moving ~1 m/s (a person several metres away)
"""
from __future__ import annotations

import numpy as np

C = 299_792_458.0
FC = 2.437e9
K = 64
F = FC + (np.arange(K) - 32) * 312.5e3
USED = np.zeros(K, bool)
USED[6:32] = True
USED[33:59] = True                 # HT20: DC (32) and guard bins carry no CSI


class Scene:
    def __init__(self, seed: int = 0, scale: float = 22.0):
        self.rng = np.random.default_rng(seed)
        r = self.rng
        self.scale = scale
        self.static = [(1.0, 10e-9, 0.0)] + [(r.uniform(0.2, 0.6), r.uniform(15e-9, 80e-9), r.uniform(0, 2 * np.pi))
                                              for _ in range(4)]
        self.people: list[dict] = []
        self.gain = 1.0

    def add(self, kind: str, start: float = 0.0, stop: float = 1e9, breath_hz: float = 0.25, amp: float | None = None):
        r = self.rng
        a = amp if amp is not None else dict(near_still=0.45, near_walker=0.35, far_walker=r.uniform(0.02, 0.05))[kind]
        d0 = dict(near_still=r.uniform(1.0, 1.6), near_walker=r.uniform(1.0, 2.0), far_walker=r.uniform(4.0, 8.0))[kind]
        speed = dict(near_still=0.0, near_walker=0.5, far_walker=r.uniform(0.7, 1.3))[kind]
        self.people.append(dict(kind=kind, a=a, d0=d0, speed=speed, start=start, stop=stop,
                                fb=breath_hz, phase=r.uniform(0, 2 * np.pi), turn=r.uniform(3, 8)))

    def path_len(self, p: dict, t: float) -> float:
        if p["kind"] == "near_still":
            return p["d0"] + 0.005 * np.sin(2 * np.pi * p["fb"] * t + p["phase"])
        # walk back and forth (triangle wave) so the path length stays bounded
        x = (p["speed"] * t) % (2 * p["turn"])
        return p["d0"] + (x if x < p["turn"] else 2 * p["turn"] - x)

    def csi(self, t: float) -> np.ndarray:
        h = np.zeros(K, complex)
        for a, tau, ph in self.static:
            h += a * np.exp(-2j * np.pi * F * tau + 1j * ph)
        for p in self.people:
            if p["start"] <= t < p["stop"]:
                tau = self.path_len(p, t) / C
                h += p["a"] * np.exp(-2j * np.pi * F * tau + 1j * p["phase"])
        if self.rng.random() < 0.002:                      # occasional AGC step
            self.gain *= self.rng.choice([0.8, 1.25])
        g = self.gain * (1 + 0.02 * self.rng.standard_normal())
        h = g * h + 0.01 * (self.rng.standard_normal(K) + 1j * self.rng.standard_normal(K))
        h[~USED] = 0
        return (self.scale * h)[None, :]

    def times(self, t0: float, t1: float, rate: float = 20.0, drop: float = 0.05):
        """Irregular arrival times: jittered rate with random drops."""
        t = t0
        while t < t1:
            t += (1 / rate) * self.rng.uniform(0.7, 1.3)
            if self.rng.random() >= drop:
                yield t
