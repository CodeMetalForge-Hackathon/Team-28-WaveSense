"""Independent capture selections, serialized across phones. Never drives wheels."""
import subprocess
import threading
import time
from pathlib import Path

RADAR_DIR = Path('/home/adritforge/Team-28-wifi-xray/wifi-sensing/radar')
RADAR_PYTHON = '/home/adritforge/tinyrad/venv/bin/python'

class SensorModes:
    def __init__(self, start_lidar, stop_lidar, root, popen=subprocess.Popen, clear_export=None):
        self.start_lidar, self.stop_lidar = start_lidar, stop_lidar
        self.root, self.popen = Path(root), popen
        self.clear_export = clear_export or (lambda: Path('/srv/team28/runtime/radar/status.json').unlink(missing_ok=True))
        self.lock = threading.RLock()
        self.enabled = {'radar':False, 'lidar':False}
        self.switching = False
        self.process = None
        self.error = None
        self.stop_unconfirmed = False
        self.closed = False
        self.restart_due = 0
        self.restart_failures = 0

    @property
    def mode(self):
        selected = dict(self.enabled)
        return 'both' if all(selected.values()) else 'radar' if selected['radar'] else 'lidar' if selected['lidar'] else 'off'

    def status(self):
        process = self.process
        dead = self.enabled['radar'] and process is not None and process.poll() is not None
        return {'mode':self.mode,'enabled':dict(self.enabled),'switching':self.switching,
                'error':'Radar reconnecting' if dead else self.error}

    def stop_radar(self):
        if self.stop_unconfirmed: raise RuntimeError('Radar stop unconfirmed; power-cycle radar before restarting server')
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=5)
                    self.stop_unconfirmed = True
                    raise RuntimeError('Radar stop timed out')
            self.process = None

    def launch_radar(self):
        self.clear_export()
        with (self.root/'radar-worker.log').open('ab') as log:
            self.process=self.popen([RADAR_PYTHON,'-I','radar_service.py','--near-range-m','3.0'],
                                    cwd=RADAR_DIR,stdout=log,stderr=subprocess.STDOUT)

    def recover_radar(self):
        if self.closed or not self.enabled['radar'] or not self.lock.acquire(blocking=False):return
        try:
            if self.closed or not self.enabled['radar']:return
            if self.process is not None and self.process.poll() is None:return
            now=time.monotonic()
            if now<self.restart_due:return
            self.restart_due=now+(3,10,30,60)[min(self.restart_failures,3)]
            self.restart_failures+=1
            try:self.launch_radar();self.error=None
            except Exception as error:self.error=type(error).__name__
        finally:self.lock.release()

    def _set(self, sensor, enabled):
        if self.enabled[sensor] == enabled: return
        if sensor == 'radar':
            if enabled:
                if self.stop_unconfirmed: raise RuntimeError('Radar requires power-cycle')
                self.launch_radar()
            else: self.stop_radar()
        elif enabled: self.start_lidar()
        else: self.stop_lidar()
        self.enabled = dict(self.enabled, **{sensor:enabled})

    def set_enabled(self, sensor, enabled):
        if sensor not in ('radar','lidar') or type(enabled) is not bool: raise ValueError('Invalid sensor selection')
        with self.lock:
            self.switching=True; self.error=None
            try: self._set(sensor, enabled)
            except Exception as e: self.error=str(e); raise
            finally: self.switching=False

    def select(self, mode):
        # Compatibility for earlier clients and explicit all-off/both requests.
        if mode not in ('off','radar','lidar','both'): raise ValueError('Unknown sensor mode')
        target={'radar':mode in ('radar','both'),'lidar':mode in ('lidar','both')}
        with self.lock:
            self.switching=True; self.error=None
            try:
                for sensor in target:
                    if not target[sensor]: self._set(sensor,False)
                for sensor in target:
                    if target[sensor]: self._set(sensor,True)
            except Exception as e: self.error=str(e); raise
            finally: self.switching=False

    def close(self):
        self.closed = True
        # A radar shutdown error must not prevent LiDAR cleanup.
        with self.lock:
            try: self.stop_radar()
            finally:
                self.stop_lidar()
                self.enabled={'radar':False,'lidar':False}
