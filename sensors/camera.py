"""RGB camera: OpenCV CAP_V4L2, 640x480, MJPG if offered else YUYV.

snapshot()["data"] is a BGR uint8 array (480, 640, 3).
"""
import os, subprocess

import cv2

from sensors.base import StreamingSensor, device_path


def offered_fourccs(dev):
    out = subprocess.run(["v4l2-ctl", "-d", dev, "--list-formats"], capture_output=True,
                         text=True, timeout=5).stdout
    return {line.split("'")[1] for line in out.splitlines() if "]: '" in line}


class Camera(StreamingSensor):
    name = "rgb"
    stale_after = 0.5

    def __init__(self, path=None, width=640, height=480):
        super().__init__(path or device_path("rgb"))
        self.width, self.height = width, height
        self.cap = None
        self.fourcc = None

    def _open(self):
        dev = os.path.realpath(self.path)  # by-id -> /dev/videoN, re-resolved on every reconnect
        fourcc = "MJPG" if "MJPG" in offered_fourccs(dev) else "YUYV"
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise IOError(f"OpenCV could not open {dev}")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        # Keep the driver's default 4 buffers: BUFFERSIZE=1 measured 16.5 fps vs 22.7 fps
        # (the camera drops frames while our only buffer is held). The reader thread
        # drains continuously, so no backlog builds up anyway.
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000)  # don't hang on unplug
        self.cap, self.fourcc = cap, fourcc
        self._set_status("starting", f"{dev} {fourcc}")

    def _read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise IOError("frame read failed (unplugged?)")
        self._publish(frame)

    def _close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
