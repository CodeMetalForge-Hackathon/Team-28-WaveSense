"""Proximity warnings from the LiDAR. ADVISORY ONLY: never changes drive commands.

The operator decided (2026-11-07) that the robot is purely human-operated:
commands go straight to robot/chassis.py (with its watchdog, deadman and duty
cap). This module only tells the operator what is close, e.g. for a red edge
and a vibration on the phone.

Robot frame: bearing in degrees, 0 = forward, +90 = left, -90 = right,
+-180 = back. LiDAR angles are converted with config/robot.json:
lidar.forward_angle_deg and lidar.angle_direction ("cw": LiDAR angle grows
clockwise seen from above, Slamtec convention; "ccw" the opposite).

assess() returns per-sector nearest distances (8 x 45 deg), levels
("close" < close_mm, "near" < near_mm, else "clear"), the overall nearest
point, staleness, and, for a given command (vx forward+, vy left+, wz ccw+),
whether that command heads toward something close or near. Distances are from
the LiDAR's spin center, not from the robot's edge.
"""
import json, math, os, time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTORS = ["front", "front-left", "left", "back-left", "back", "back-right", "right", "front-right"]
CENTER = {name: i * 45.0 if i <= 4 else i * 45.0 - 360.0 for i, name in enumerate(SECTORS)}
LEVELS = ("clear", "near", "close")


def load_config():
    try:
        with open(os.path.join(ROOT, "config", "robot.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def wrap180(a):
    return (np.asarray(a, dtype=float) + 180.0) % 360.0 - 180.0


class Proximity:
    def __init__(self, forward_deg=None, direction=None, close_mm=None, near_mm=None,
                 stale_s=None, ignore_below_mm=None, self_mask=None, robust_k=3):
        """Explicit arguments win; otherwise config/robot.json; otherwise built-in defaults."""
        cfg = load_config()
        lid = cfg.get("lidar", {})
        prox = cfg.get("proximity", {})
        self.forward_deg = float(lid.get("forward_angle_deg", 0.0) if forward_deg is None else forward_deg)
        self.direction = (lid.get("angle_direction", "cw") if direction is None else direction).lower()
        if self.direction not in ("cw", "ccw"):
            raise ValueError(f"angle_direction must be cw or ccw, not {self.direction!r}")
        self.direction_verified = bool(lid.get("angle_direction_verified", False))
        pick = lambda arg, key, default: float(arg if arg is not None else prox.get(key, default))
        self.close_mm = pick(close_mm, "close_mm", 300.0)
        self.near_mm = pick(near_mm, "near_mm", 600.0)
        self.stale_s = pick(stale_s, "stale_s", 0.5)
        self.ignore_below_mm = pick(ignore_below_mm, "ignore_below_mm", 60.0)
        # [[bearing_from, bearing_to, max_mm], ...] robot-body returns to ignore
        self.self_mask = prox.get("self_mask", []) if self_mask is None else self_mask
        self.robust_k = int(robust_k)

    # ---- geometry ---------------------------------------------------------------
    def to_robot(self, scan):
        """(N,3) angle_deg, dist_mm, quality -> (M,2) bearing_deg, dist_mm of usable points."""
        scan = np.asarray(scan, dtype=float)
        if scan.size == 0:
            return np.zeros((0, 2))
        ok = (scan[:, 2] > 0) & (scan[:, 1] >= self.ignore_below_mm)
        rel = scan[ok, 0] - self.forward_deg
        bearing = wrap180(-rel if self.direction == "cw" else rel)
        pts = np.stack([bearing, scan[ok, 1]], axis=1)
        for lo, hi, max_mm in self.self_mask:     # counter-clockwise from lo to hi, any width
            inside = (pts[:, 0] - lo) % 360.0 <= (hi - lo) % 360.0
            pts = pts[~(inside & (pts[:, 1] <= max_mm))]
        return pts

    def level(self, mm):
        if mm is None:
            return "clear"
        return "close" if mm < self.close_mm else "near" if mm < self.near_mm else "clear"

    def _nearest(self, pts, center, half_width):
        sel = pts[np.abs(wrap180(pts[:, 0] - center)) <= half_width]
        if len(sel) == 0:
            return None
        d = np.sort(sel[:, 1])
        return float(d[min(self.robust_k, len(d)) - 1])   # k-th nearest: ignores 1-2 noise points

    # ---- main entry ---------------------------------------------------------------
    def assess(self, scan, scan_ts_ms, cmd=None, now_ms=None):
        now_ms = time.time() * 1000 if now_ms is None else now_ms
        age = None if scan_ts_ms is None else now_ms - scan_ts_ms
        stale = scan is None or age is None or age > self.stale_s * 1000
        out = dict(stale=stale, scan_age_ms=None if age is None else int(age),
                   direction_verified=self.direction_verified, distances_from="lidar_center",
                   sectors={}, nearest=None,
                   toward=None, warnings=[])
        if stale:
            out["warnings"].append("LiDAR stale: no scan for "
                                   + ("ever" if age is None else f"{age / 1000:.1f} s") + " - driving blind")
            return out
        pts = self.to_robot(scan)
        for name in SECTORS:
            mm = self._nearest(pts, CENTER[name], 22.5)
            out["sectors"][name] = dict(nearest_mm=mm, level=self.level(mm))
        if len(pts):
            # centre of the closest patch (within 20 mm of the minimum, +-15 deg of it), so a flat
            # surface reports its middle rather than whichever point happens to come first
            i = int(np.argmin(pts[:, 1]))
            near = pts[(pts[:, 1] <= pts[i, 1] + 20) & (np.abs(wrap180(pts[:, 0] - pts[i, 0])) <= 15)]
            b = np.radians(near[:, 0])
            centre = math.degrees(math.atan2(np.sin(b).mean(), np.cos(b).mean()))
            out["nearest"] = dict(bearing_deg=round(centre, 1), mm=float(pts[i, 1]))
        for name, s in out["sectors"].items():
            if s["level"] == "close":
                out["warnings"].append(f"obstacle {s['nearest_mm'] / 10:.0f} cm {name}")
        if cmd is not None:
            out["toward"] = self.toward(pts, *cmd)
        return out

    def toward(self, pts, vx, vy, wz):
        """Does this command head toward something close/near? (Information only.)"""
        speed = math.hypot(vx, vy)
        if speed > 0.05:
            bearing = math.degrees(math.atan2(vy, vx))      # vx forward, vy left -> 0 fwd, +90 left
            mm = self._nearest(pts, bearing, 30.0)          # +-30 deg cone around the direction of travel
            lvl = self.level(mm)
            return dict(kind="translate", bearing_deg=round(bearing, 1), nearest_mm=mm, level=lvl,
                        side=min(SECTORS, key=lambda n: abs(float(wrap180(CENTER[n] - bearing)))))
        if abs(wz) > 0.05:
            mm = self._nearest(pts, 0.0, 180.0)            # rotating: anything all around
            lvl = "close" if mm is not None and mm < self.close_mm else "clear"
            return dict(kind="rotate", nearest_mm=mm, level=lvl, side=None)
        return None
