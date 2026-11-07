"""2D SLAM from the RPLIDAR C1 with BreezySLAM (RMHC_SLAM, scan matching only, no odometry).

    from sensors.lidar import Lidar
    from mapping.slam import Slam
    lidar = Lidar().start()
    slam = Slam(lidar).start()
    snap = slam.snapshot()   # status, fps, last_seen, data={pose, trail, map, map_ts, ...}

Each LiDAR revolution is converted to the robot frame (robot/proximity.py:
forward angle + direction from config/robot.json) and resampled into 360
one-degree bins in BreezySLAM's layout: bin i = robot bearing (i - 180) deg,
counter-clockwise, so bin 180 is straight ahead and the SLAM heading is the
robot heading. Bin values:
  median valid distance (mm)  -> obstacle (BreezySLAM ignores <= hole_width/2)
  1                           -> no information (empty, masked): BreezySLAM
                                 skips it. NOT 0, which BreezySLAM treats as
                                 "free space out to 12 m" and would erase walls.
Map: 800 px x 20 m (25 mm/px). snapshot map is flipped so row 0 is +y (x right,
y up), matching the counter-clockwise robot frame. Pose (x_mm, y_mm, theta_deg)
starts at the map centre (10 m, 10 m), heading 0.
"""
import collections, threading, time

import numpy as np
from breezyslam.algorithms import RMHC_SLAM
from breezyslam.sensors import Laser

from robot.proximity import Proximity, wrap180
from sensors.base import now_ms

NO_INFO = 1


class C1Laser(Laser):
    """RPLIDAR C1 as BreezySLAM sees our resampled scans: 360 bins, ~10 Hz, 360 deg, 12 m."""

    def __init__(self):
        Laser.__init__(self, 360, 10, 360, 12000, 0, 0)


class Slam:
    name = "slam"
    stale_after = 1.0

    # RMHC parameters chosen by a sweep on synthetic ray-cast scans (tests/test_slam.py room):
    # BreezySLAM defaults (quality 50, sigma_xy 100, 1000 iter) under-estimated straight-line
    # motion by 12-24 % (619-707 mm for 800 mm); quality 25, sigma_xy 50, 3000 iter gave
    # 799-809 mm and exact mixed motion, at ~18 ms per update on the Pi 5. sigma_xy 50 assumes
    # under ~0.5 m/s (~50 mm per revolution).
    def __init__(self, lidar=None, map_px=800, map_m=20.0, mask=None, min_valid_bins=90,
                 map_every_s=1.0, trail_step_mm=20.0, prox=None, seed=None,
                 map_quality=25, hole_width_mm=600, sigma_xy_mm=50, sigma_theta_deg=20, max_search_iter=3000):
        self.lidar = lidar
        self.prox = prox or Proximity()
        self.map_px, self.map_m = map_px, map_m
        self.mm_per_px = map_m * 1000.0 / map_px
        self.mask = mask or []                 # [[bearing_from, bearing_to, max_mm], ...] ccw, robot frame
        self.min_valid_bins = min_valid_bins
        self.map_every_s = map_every_s
        self.trail_step_mm = trail_step_mm
        self.params = dict(map_quality=map_quality, hole_width_mm=hole_width_mm, sigma_xy_mm=sigma_xy_mm,
                           sigma_theta_degrees=sigma_theta_deg, max_search_iter=max_search_iter)
        self.slam = RMHC_SLAM(C1Laser(), map_px, map_m, random_seed=seed, **self.params)
        self._mapbytes = bytearray(map_px * map_px)
        self._lock = threading.Lock()
        self.pose = (map_m * 500.0, map_m * 500.0, 0.0)
        self.trail = [self.pose]
        self.map = None
        self.map_ts = None
        self.updates = self.skipped = 0
        self.update_ms = collections.deque(maxlen=50)
        self._times = collections.deque(maxlen=64)
        self._last_seen = None
        self.status, self.detail = "starting", ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="slam", daemon=True)

    # ---- scan -> bins ---------------------------------------------------------------
    def resample(self, scan):
        pts = self.prox.to_robot(scan)               # (bearing ccw deg, mm), quality/self-mask filtered
        pts = pts[pts[:, 1] < 12000]
        for lo, hi, max_mm in self.mask:
            inside = (pts[:, 0] - lo) % 360.0 <= (hi - lo) % 360.0
            pts = pts[~(inside & (pts[:, 1] <= max_mm))]
        bins = np.full(360, NO_INFO, dtype=np.int64)
        if len(pts):
            idx = np.round(wrap180(pts[:, 0]) + 180.0).astype(int) % 360
            order = np.argsort(idx, kind="stable")
            idx, d = idx[order], pts[order, 1]
            starts = np.flatnonzero(np.r_[True, idx[1:] != idx[:-1]])
            for s, e in zip(starts, np.r_[starts[1:], len(idx)]):
                bins[idx[s]] = int(np.median(d[s:e]))
        return bins

    def process(self, scan, ts_ms=None):
        """Feed one revolution. Returns the new pose, or None if the scan was too sparse."""
        bins = self.resample(scan)
        if int((bins > NO_INFO).sum()) < self.min_valid_bins:
            self.skipped += 1
            return None
        t = time.perf_counter()
        self.slam.update(bins.tolist(), (0, 0, 0))   # no odometry: pure scan matching
        x, y, th = self.slam.getpos()
        self.update_ms.append(1000 * (time.perf_counter() - t))
        with self._lock:
            self.pose = (x, y, th)
            lx, ly, _ = self.trail[-1]
            if np.hypot(x - lx, y - ly) >= self.trail_step_mm:
                self.trail.append(self.pose)
            self._last_seen = ts_ms if ts_ms is not None else now_ms()
            self._times.append(time.monotonic())
            self.status, self.detail = "ok", ""
        self.updates += 1
        if self.map_ts is None or time.monotonic() - self.map_ts >= self.map_every_s:
            self.refresh_map()
        return self.pose

    def refresh_map(self):
        self.slam.getmap(self._mapbytes)
        m = np.frombuffer(bytes(self._mapbytes), np.uint8).reshape(self.map_px, self.map_px)
        with self._lock:
            self.map = np.flipud(m).copy()           # row 0 = +y: x right, y up
            self.map_ts = time.monotonic()

    def to_pixel(self, x_mm, y_mm):
        """World mm -> (col, row) in the flipped map image."""
        return int(round(x_mm / self.mm_per_px)), int(round(self.map_px - 1 - y_mm / self.mm_per_px))

    # ---- thread ---------------------------------------------------------------------
    def start(self):
        self._thread.start()
        return self

    def stop(self, timeout=2.0):
        self._stop.set()
        self._thread.join(timeout)

    def _run(self):
        last = self.lidar.frames
        while not self._stop.is_set():
            if self.lidar.frames == last:
                time.sleep(0.005)
                ls = self.lidar.snapshot()
                if ls["status"] != "ok":
                    with self._lock:
                        self.status, self.detail = "stale", f"lidar {ls['status']}"
                continue
            last = self.lidar.frames                   # always the newest revolution; never queue
            sn = self.lidar.snapshot()
            try:
                if self.process(sn["data"], sn["last_seen"]) is None:
                    with self._lock:
                        self.detail = "scan too sparse, skipped"
            except Exception as e:
                with self._lock:
                    self.status, self.detail = "error", f"{type(e).__name__}: {e}"
                time.sleep(0.1)
        with self._lock:
            self.status = "stopped"

    def snapshot(self):
        with self._lock:
            t = time.monotonic()
            ts = [x for x in self._times if t - x <= 2.0]
            fps = (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) >= 2 and ts[-1] > ts[0] else 0.0
            age = None if self._last_seen is None else now_ms() - self._last_seen
            status = self.status
            if status == "ok" and (age is None or age > self.stale_after * 1000):
                status = "stale"
            return dict(name=self.name, status=status, detail=self.detail,
                        fps=round(fps, 1) if status == "ok" else 0.0, last_seen=self._last_seen, age_ms=age,
                        data=dict(pose=self.pose, trail=list(self.trail), map=self.map, mm_per_px=self.mm_per_px,
                                  updates=self.updates, skipped=self.skipped,
                                  update_ms=float(np.median(self.update_ms)) if self.update_ms else None))
