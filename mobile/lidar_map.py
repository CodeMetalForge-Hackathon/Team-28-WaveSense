"""Bounded phone telemetry from the shared SLAM worker; never opens a device."""
import math
import threading
import time


def pixel_pose(pose, mm_per_px, height):
    x, y, heading = map(float, pose)
    if not all(math.isfinite(v) for v in (x, y, heading, mm_per_px)) or mm_per_px <= 0:
        raise ValueError('Invalid map coordinates')
    return [round(x/mm_per_px, 2), round(height-1-y/mm_per_px, 2), round(heading % 360, 2)]


def bounded_trail(trail, mm_per_px, height, limit=400):
    """Retain both endpoints and a representative path without growing phone payloads."""
    if len(trail) > limit:
        trail = [trail[round(i*(len(trail)-1)/(limit-1))] for i in range(limit)]
    return [pixel_pose(p, mm_per_px, height)[:2] for p in trail]


class MapFeed:
    def __init__(self):
        self.lock = threading.Lock()
        self.png = None
        self.revision = time.time_ns() // 1_000_000
        self.source_map = None
        self.meta = {'status': 'starting', 'online': False}

    def update(self, snapshot, scan_fresh):
        data = snapshot.get('data') or {}
        image = data.get('map')
        if image is not None and image is not self.source_map:
            import cv2
            import numpy as np
            # BreezySLAM: dark = occupied, 127 = unknown, bright = observed free.
            # White obstacles, dim observed floor, black unknown space on the phone.
            shade = np.zeros(256, np.uint8)
            shade[:110] = np.linspace(245, 115, 110).astype(np.uint8)
            shade[146:] = 38
            ok, encoded = cv2.imencode('.png', shade[image], [cv2.IMWRITE_PNG_COMPRESSION, 3])
            if ok:
                with self.lock:
                    self.png = encoded.tobytes()
                    self.revision += 1
                self.source_map = image
        height, width = image.shape if image is not None else (0, 0)
        scale = float(data.get('mm_per_px') or 25)
        age = snapshot.get('age_ms')
        fresh = bool(scan_fresh and snapshot.get('status') == 'ok' and age is not None and age <= 500)
        meta = {'status': snapshot.get('status', 'starting'), 'online': fresh,
                'detail': snapshot.get('detail', ''), 'age_ms': age,
                'fps': snapshot.get('fps', 0), 'updates': data.get('updates', 0),
                'skipped': data.get('skipped', 0), 'update_ms': data.get('update_ms'),
                'mm_per_px': scale, 'width': width, 'height': height,
                'revision': self.revision, 'available': self.png is not None}
        if image is not None:
            meta['pose_px'] = pixel_pose(data['pose'], scale, height)
            meta['trail_px'] = bounded_trail(data.get('trail', []), scale, height)
        with self.lock:
            self.meta = meta
        return dict(meta)

    def image(self):
        with self.lock:
            return self.png
