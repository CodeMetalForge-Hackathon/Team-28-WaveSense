"""Serialized, fail-closed physical capture interlock. No drive commands."""
import subprocess
import threading
from pathlib import Path

RADAR_DIR = Path('/home/adritforge/Team-28-wifi-xray/wifi-sensing/radar')
RADAR_PYTHON = '/home/adritforge/tinyrad/venv/bin/python'

class SensorModes:
    def __init__(self, start_lidar, stop_lidar, root, popen=subprocess.Popen, clear_export=None):
        self.start_lidar, self.stop_lidar = start_lidar, stop_lidar
        self.root, self.popen = Path(root), popen
        self.clear_export = clear_export or (lambda: Path("/srv/team28/runtime/radar/status.json").unlink(missing_ok=True))
        self.lock = threading.RLock()
        self.mode = 'off'
        self.process = None
        self.error = None
        self.stop_unconfirmed = False

    def status(self):
        # Status must not block camera/control polling during a sensor transition.
        mode, process = self.mode, self.process
        dead = mode == 'radar' and process is not None and process.poll() is not None
        return {'mode':mode, 'error':'Radar stopped; toggle off and retry' if dead else self.error}

    def stop_radar(self):
        if self.stop_unconfirmed: raise RuntimeError("Radar stop unconfirmed; power-cycle radar before restarting server")
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=5)
                    self.stop_unconfirmed = True
                    raise RuntimeError("Radar stop timed out; other sensor stays off")
            self.process = None

    def select(self, mode):
        if mode not in ('off','radar','lidar'): raise ValueError('Unknown sensor mode')
        with self.lock:
            if self.mode == mode and not self.error: return
            self.mode = 'switching'; self.error = None
            try:
                # Always stop BOTH old sources before starting either new source.
                self.stop_radar()
                self.stop_lidar()
                if mode == 'radar':
                    self.clear_export()
                    with (self.root/'radar-worker.log').open('ab') as log:
                        self.process = self.popen([RADAR_PYTHON,'-I','radar_service.py'],cwd=RADAR_DIR,
                                                   stdout=log,stderr=subprocess.STDOUT)
                elif mode == 'lidar': self.start_lidar()
                self.mode = mode
            except Exception as e:
                self.mode = 'off'; self.error = str(e)
                raise

    def close(self):
        with self.lock:
            self.stop_radar()
            self.stop_lidar()
            self.mode = 'off'
