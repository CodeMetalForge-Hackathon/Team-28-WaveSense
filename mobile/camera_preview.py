#!/usr/bin/env python3
"""WaveSense operator gateway. Static UI, camera, CSI proxy, leased motor control.

Motor framing and wheel signs match Team-28 PR #1, commit 85f6b10.
No hardware is opened on import or startup. Motors open only on explicit Arm.
"""
import argparse
from collections import deque
import sys
import atexit
import fcntl
import http.cookies
import json
import math
import mimetypes
import os
from pathlib import Path
import secrets
import signal
import struct
import threading
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
WATCHDOG = 0.30
MAX_DUTY = 35.0
SIGNS = {1: -1, 2: 1, 3: -1, 4: 1}


class StaleCommand(ValueError):
    pass


class Motors:
    """The tested chassis owns the UART and its separate crash-stop process."""
    def __init__(self, port):
        self.port = port
        self.chassis = None

    def open(self):
        from robot.chassis import Chassis
        self.chassis = Chassis(port=self.port, duty_cap=40, watchdog_s=.3,
                               deadman=True, handle_signals=False)
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            state = self.chassis.status()
            if state['error']:
                break
            if state['deadman_alive'] and state['battery_age_s'] is not None and state['battery_age_s'] < .5:
                return
            time.sleep(.02)
        self.close()
        raise RuntimeError('Motor board not responding')

    def send(self, linear, angular, sideways=0):
        if self.chassis:
            self.chassis.set_velocity(vx=linear/.35, vy=sideways/.35, wz=angular/.35)
            if self.chassis.error:
                raise RuntimeError(self.chassis.error)

    def close(self):
        if self.chassis:
            self.chassis.close()
            self.chassis = None

    def details(self):
        return self.chassis.status() if self.chassis else {'moving': False, 'deadman_alive': False}


class Camera:
    """Single reader from the team's reconnecting sensor module, cached JPEG fan-out."""
    def __init__(self, device=None, thermal=False):
        self.device = device
        self.thermal = thermal
        self.frame = None
        self.updated = 0
        self.state = 'waiting'
        self.lock = threading.Lock()
        self.sensor = None
        self.seq = 0
        self.source = {}
        self.frame_times = deque(maxlen=60)
        self.resolution = None
        self.condition = threading.Condition(self.lock)

    def snapshot(self):
        with self.lock:
            return self.frame, self.updated, self.state

    def details(self):
        with self.lock:
            times = self.frame_times
            fps = (len(times)-1)/(times[-1]-times[0]) if len(times)>1 and time.monotonic()-times[-1]<1 else 0
            return dict(self.source, sequence=self.seq, resolution=self.resolution, stream_fps=round(fps,1))

    def close(self):
        if self.sensor:
            self.sensor.stop()

    def run(self):
        import cv2
        if self.thermal:
            from sensors.thermal import Thermal
            self.sensor = Thermal(path=self.device).start()
        else:
            from sensors.camera import Camera as RgbCamera
            self.sensor = RgbCamera(path=self.device).start()
        previous = None
        while True:
            sample = self.sensor.snapshot()
            with self.lock:
                self.source = {k:sample.get(k) for k in ('status','fps','age_ms','last_seen','frames','reconnects')}
                self.state = 'live' if sample['status'] == 'ok' else 'reconnecting'
            if sample['status'] == 'ok' and sample['last_seen'] != previous:
                data = sample['data']
                image = data['color'] if self.thermal else data
                ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 72])
                if ok:
                    with self.condition:
                        self.frame = encoded.tobytes()
                        self.resolution = [int(image.shape[1]),int(image.shape[0])]
                        self.frame_times.append(time.monotonic())
                        # Preserve source age: an encoder must not make stale data fresh.
                        self.updated = time.monotonic() - max(0, sample['age_ms']) / 1000
                        self.state = 'live'
                        self.seq += 1
                        if self.thermal and data.get('temp'):
                            self.source['max_c_uncalibrated'] = float(data['temp']['max_c'])
                        self.condition.notify_all()
                    previous = sample['last_seen']
            time.sleep(.005)  # Drain fresh frames promptly; no fixed 12.5 fps phone cap.


class Gateway:
    def __init__(self, args):
        self.args = args
        self.camera = Camera(args.camera)
        self.thermal = Camera(args.thermal, thermal=True)
        self.motors = Motors(args.motor_port)
        self.lock = threading.RLock()
        self.owner = None
        self.lease = None
        self.last_command = 0
        self.sequence = -1
        self.reason = 'Not armed'
        self.sessions = {}
        self.wifi = {'online': False, 'reason': 'RuView unavailable'}
        self.lidar = {'mode':'2d','online': False, 'reason': 'LiDAR off'}
        self.lidar_sensor = None
        self.slam = None
        self.map_feed = None
        self.cloud = None
        self.radar = {"online":False,"reason":"Radar driver feed unavailable"}
        from person_vision import PersonVision
        self.vision = PersonVision(self.camera, ROOT)
        self.last_motion_at = 0
        from sensor_modes import SensorModes
        self.lidar_enabled = threading.Event()
        self.lidar_running = threading.Event()
        self.lidar_stopped = threading.Event(); self.lidar_stopped.set()
        self.sensor_modes = SensorModes(self.start_lidar, self.stop_lidar, ROOT)

    def radar_poll(self):
        from radar_status import read_radar
        while True:
            self.radar = read_radar(self.args.radar_status_file) if self.sensor_modes.enabled["radar"] else {"online":False,"tracks":[],"reason":"Radar off"}
            time.sleep(.3)

    def start_lidar(self):
        self.lidar_stopped.clear()
        self.lidar_enabled.set()
        if not self.lidar_running.wait(3): raise RuntimeError("LiDAR worker did not start")

    def stop_lidar(self):
        self.lidar_enabled.clear()
        if not self.lidar_stopped.wait(15):
            raise RuntimeError('LiDAR is still stopping; radar remains off')

    def lidar_poll(self):
        while True:
            self.lidar_enabled.wait()
            self.lidar_running.set()
            try:
                self.run_lidar()
            except Exception as e:
                self.lidar = {'mode':'2d','online':False,'reason':type(e).__name__,'points':[]}
            finally:
                if self.cloud: self.cloud.stop(); self.cloud = None
                if self.slam: self.slam.stop(); self.slam = None
                if self.lidar_sensor:
                    sensor = self.lidar_sensor
                    sensor.stop()
                    if sensor.alive():
                        # Never release the interlock while capture is still alive.
                        while sensor.alive(): time.sleep(.1)
                    if sensor._closer: sensor._closer.join()
                    self.lidar_sensor = None
                self.lidar_enabled.clear()
                self.lidar = {'mode':'2d','online':False,'reason':'LiDAR off','points':[]}
                self.map_feed = None
                self.lidar_running.clear()
                self.lidar_stopped.set()

    def run_lidar(self):
        if getattr(self.args, 'lidar_mode', '2d') == '3d':
            from livox_sensor import Livox
            self.cloud = Livox(ROOT, ip=self.args.lidar_ip, model=self.args.lidar_model).start()
            while self.lidar_enabled.is_set():
                self.lidar = dict(self.cloud.snapshot(), mode='3d', points=[])
                time.sleep(.2)
            return
        from sensors.lidar import Lidar
        from robot.proximity import Proximity
        from lidar_map import MapFeed
        self.map_feed = MapFeed()
        self.lidar_sensor = Lidar().start()
        proximity = Proximity()
        mapping_error = None
        try:
            from mapping.slam import Slam
            # Same scanner, one serial reader; SLAM consumes its cached revolutions.
            self.slam = Slam(self.lidar_sensor, map_every_s=1.0).start()
        except Exception as e:
            mapping_error = type(e).__name__
        while self.lidar_enabled.is_set():
            try:
                sample = self.lidar_sensor.snapshot()
                fresh = sample['status'] == 'ok' and sample['age_ms'] is not None and sample['age_ms'] <= 500
                points = proximity.to_robot(sample['data']) if fresh and sample['data'] is not None else []
                nearby = proximity.assess(sample['data'] if fresh else None, sample['last_seen'])
                mapping = self.map_feed.update(self.slam.snapshot(), fresh) if self.slam else {
                    'online': False, 'available': False, 'status': 'unavailable', 'detail': mapping_error}
                self.lidar = {'mode':'2d', 'online': fresh, 'reason': sample['status'],
                              'fps': sample['fps'], 'age_ms': sample['age_ms'],
                              'frames': sample['frames'], 'reconnects': sample['reconnects'],
                              'points': points.tolist() if hasattr(points, 'tolist') else [],
                              'proximity': nearby, 'mapping': mapping}
            except Exception as e:
                self.lidar = {'online': False, 'reason': 'reconnecting', 'points': [],
                              'proximity': {'stale': True},
                              'mapping': dict(self.map_feed.meta, online=False, detail=type(e).__name__)}
            time.sleep(.2)

    def close(self):
        self.disarm('Server stopped')
        self.sensor_modes.close()
        if self.vision.thread.is_alive():
            self.vision.close()
        self.camera.close()
        self.thermal.close()
        if self.cloud:
            self.cloud.stop()
        if self.slam:
            self.slam.stop()
        if self.lidar_sensor:
            self.lidar_sensor.stop()

    def camera_ready(self):
        _, updated, state = self.camera.snapshot()
        return state == 'live' and time.monotonic() - updated < 1.0

    def disarm(self, reason):
        with self.lock:
            self.owner = None
            self.lease = None
            self.sequence = -1
            self.reason = reason
            self.motors.close()

    def release(self, owner, lease):
        with self.lock:
            if self.owner == owner and lease and lease == self.lease:
                self.disarm('Sticks released')

    def arm(self, owner):
        with self.lock:
            if not self.args.enable_drive:
                raise ValueError('View-only test mode')
            if self.owner and self.owner != owner:
                raise ValueError('Another phone controls the robot')
            if not self.camera_ready():
                raise ValueError('Live camera required before driving')
            if not self.owner:
                self.motors.open()
            self.motors.send(0, 0, 0)
            self.owner = owner
            self.lease = secrets.token_urlsafe(16)
            self.last_command = time.monotonic()
            self.sequence = -1
            self.reason = 'Armed'
            return self.lease

    def drive(self, owner, seq, linear, angular, sideways=0, lease=None):
        with self.lock:
            if owner != self.owner or not self.owner or lease != self.lease:
                raise ValueError('Not armed on this phone')
            if not self.camera_ready():
                self.disarm('Camera feed lost')
                raise ValueError('Camera feed lost')
            if seq <= self.sequence:
                raise StaleCommand('Old command rejected')
            if not all(math.isfinite(v) and abs(v) <= .35 for v in (linear, angular, sideways)):
                raise ValueError('Command exceeds speed limit')
            try:
                self.motors.send(linear, angular, sideways)
            except Exception:
                self.disarm('Motor connection lost')
                raise ValueError('Motor connection lost')
            self.last_command = time.monotonic()
            if abs(linear)+abs(angular)+abs(sideways)>1e-7:self.last_motion_at=self.last_command
            self.sequence = seq

    def watchdog(self):
        while True:
            with self.lock:
                if self.owner and (time.monotonic() - self.last_command > WATCHDOG or not self.camera_ready()):
                    self.disarm('Control link or camera lost')
            time.sleep(.04)

    def wifi_poll(self):
        # Only reads loopback; never starts Docker or changes the Wi-Fi interfaces.
        while True:
            try:
                token = Path(self.args.ruview_token_file).read_text().strip()
                request = Request('http://127.0.0.1:3000/api/v1/nodes',
                                  headers={'Authorization': 'Bearer ' + token})
                with urlopen(request, timeout=1) as response:
                    data = json.loads(response.read(2_000_000))
                self.wifi = {'online': True, 'updated': time.time(), 'experimental': True, 'nodes': data}
            except Exception:
                self.wifi = {'online': False, 'reason': 'RuView stream or API access unavailable'}
            time.sleep(1)

    def status(self, session):
        with self.lock:
            now = time.monotonic()
            def state(camera):
                _, updated, detail = camera.snapshot()
                return {'online': detail == 'live' and now - updated < 1,
                        'reason': detail, 'age': round(now - updated, 2) if updated else None,
                        **camera.details()}
            lidar = dict(self.lidar) if self.sensor_modes.enabled["lidar"] else {"mode":"2d","online":False,"reason":"LiDAR off","points":[]}
            if lidar.get('received') and now - lidar['received'] > 2:
                lidar = {'online': False, 'reason': 'LiDAR data stale'}
            from sensor_evidence import compose_evidence
            vision=self.vision.details()
            moving=bool(self.owner and self.motors.details().get('moving')) or now-self.last_motion_at<3
            evidence=compose_evidence(self.radar,vision,moving)
            # Keep telemetry bounded and never proxy upstream credentials.
            return {'mode': 'live', 'camera': state(self.camera), 'thermal': state(self.thermal),
                    'wifi': self.wifi, 'radar': self.radar, 'lidar': lidar, 'vision': vision, 'evidence': evidence,
                    'sensors': self.sensor_modes.status(),
                    'control': {'armed': bool(self.owner), 'mine': self.owner == session,
                                'enabled': self.args.enable_drive,
                                'driver': self.motors.details(),
                                'motor_access': os.access(self.args.motor_port, os.R_OK | os.W_OK),
                                'camera_required': True, 'reason': self.reason,
                                'watchdog_ms': int(WATCHDOG * 1000), 'max_duty': 40}}


class Handler(BaseHTTPRequestHandler):
    server_version = 'WaveSense'

    def log_message(self, *_args):
        pass  # No cookies, credentials, or upstream data in access logs.

    @property
    def app(self):
        return self.server.app

    def send(self, code, body, content_type='application/json', headers=None):
        if isinstance(body, dict):
            body = json.dumps(body, allow_nan=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def session(self):
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return None
        entry = cookies.get('wavesense')
        if not entry:
            return None
        key = entry.value
        with self.app.lock:
            touched = self.app.sessions.get(key)
            if touched is None or time.monotonic() - touched > 3600:
                self.app.sessions.pop(key, None)
                return None
            self.app.sessions[key] = time.monotonic()
        return key

    def host_allowed(self):
        return self.headers.get('Host', '') == f'{self.app.args.bind}:{self.app.args.port}'

    def same_origin(self):
        origin = self.headers.get('Origin')
        # POSTs are accepted only from the operator UI on this exact host.
        return origin == 'http://' + self.headers.get('Host', '')

    def do_GET(self):
        if not self.host_allowed():
            return self.send(421, {'error': 'Use the robot address'})
        path = urlparse(self.path).path
        session = self.session()
        if path == '/api/session':
            with self.app.lock:
                if not session:
                    if len(self.app.sessions) > 200:
                        cutoff = time.monotonic() - 3600
                        self.app.sessions = {k:v for k,v in self.app.sessions.items() if v > cutoff}
                    if len(self.app.sessions) > 200:
                        return self.send(503, {'error': 'Too many sessions'})
                    session = secrets.token_urlsafe(32)
                    self.app.sessions[session] = time.monotonic()
            return self.send(200, {'ok': True}, headers={'Set-Cookie': 'wavesense=' + session + '; HttpOnly; SameSite=Strict; Path=/'})
        if path.startswith('/api/') and not session:
            return self.send(401, {'error': 'Open the operator page first'})
        if path == '/api/lidar3d.bin':
            frame = self.app.cloud.binary() if self.app.cloud else None
            return self.send(200, frame, 'application/octet-stream') if frame else self.send(503, {'error': 'No fresh Livox data'})
        if path == '/api/lidar-map.png':
            image = self.app.map_feed.image() if self.app.map_feed else None
            return self.send(200, image, 'image/png') if image else self.send(503, {'error': 'Map building'})
        if path == '/api/status':
            return self.send(200, self.app.status(session))
        if path in ('/api/camera.mjpg', '/api/thermal.mjpg', '/api/camera-analysis.mjpg'):
            camera = self.app.vision if path == '/api/camera-analysis.mjpg' else self.app.camera if path == '/api/camera.mjpg' else self.app.thermal
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            previous = 0
            try:
                self.connection.settimeout(4)
                # Remain connected across USB recovery. Never resend an old frame as live.
                while True:
                    with camera.condition:
                        camera.condition.wait_for(lambda: camera.seq != previous, timeout=2)
                        frame, updated = camera.frame, camera.updated
                        sequence = camera.seq
                    if not frame or sequence == previous or time.monotonic() - updated > 1:
                        previous = sequence
                        continue
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
                    self.wfile.flush()
                    previous = sequence
            except (OSError, TimeoutError):
                pass
            return
        allowed = {'/': 'index.html', '/index.html': 'index.html', '/style.css': 'style.css',
                   '/drive.js': 'drive.js', '/control.js': 'control.js', '/lidar.js': 'lidar.js', '/evidence.js': 'evidence.js', '/person_overlay.js': 'person_overlay.js', '/cloud3d.js': 'cloud3d.js', '/cloud_codec.mjs': 'cloud_codec.mjs',
                   '/vendor/three/three.module.js': 'vendor/three/three.module.js',
                   '/vendor/three/three.core.js': 'vendor/three/three.core.js',
                   '/vendor/three/OrbitControls.js': 'vendor/three/OrbitControls.js', '/live.js': 'live.js', '/manifest.webmanifest': 'manifest.webmanifest',
                   '/icon.svg': 'icon.svg', '/icon-192.png': 'icon-192.png', '/icon-512.png': 'icon-512.png'}
        filename = allowed.get(path)
        if not filename:
            return self.send(404, {'error': 'Not found'})
        mime = 'application/manifest+json' if filename.endswith('.webmanifest') else mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        return self.send(200, (ROOT / filename).read_bytes(), mime)

    def do_POST(self):
        if not self.host_allowed():
            return self.send(421, {'error': 'Use the robot address'})
        if not self.same_origin():
            return self.send(403, {'error': 'Same-origin UI required'})
        session = self.session()
        if not session:
            return self.send(401, {'error': 'Session required'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 100_000:
                return self.send(413, {'error': 'Invalid body size'})
            data = json.loads(self.rfile.read(length))
            path = urlparse(self.path).path
            if path in ('/api/sensor-mode','/api/sensor'):
                return self.send(409, {'error':'Sensors run automatically; refresh the page'})
            if path == '/api/arm':
                if data.get('protocol') != 3:
                    return self.send(426, {'error': 'Refresh the operator page', 'code': 'refresh_required'})
                lease = self.app.arm(session)
                return self.send(200, {'ok': True, 'lease': lease})
            elif path == '/api/drive':
                self.app.drive(session, int(data['seq']), float(data.get('vx', data.get('linear', 0))), float(data.get('wz', data.get('angular', 0))), float(data.get('vy', 0)), lease=data.get('lease'))
            elif path == '/api/release':
                self.app.release(session, data.get('lease'))
            elif path == '/api/stop':
                # Any operator can stop. A different operator cannot take the lease.
                self.app.disarm('Stopped by operator')
            else:
                return self.send(404, {'error': 'Not found'})
            return self.send(200, {'ok': True})
        except StaleCommand:
            return self.send(409, {'error': 'Superseded by newer input', 'code': 'stale_command'})
        except (ValueError, KeyError, TypeError, OverflowError):
            return self.send(409, {'error': 'Control unavailable; check live camera and operator connection'})
        except Exception:
            self.app.disarm('Hardware connection unavailable')
            return self.send(503, {'error': 'Hardware connection unavailable'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--motor-port', default='/dev/ttyAMA0')
    parser.add_argument('--camera')
    parser.add_argument('--thermal')
    parser.add_argument('--ruview-token-file', default=str(Path.home() / '.config/ruview/api_token'))
    parser.add_argument('--lidar-mode', choices=('2d','3d'), default='2d')
    parser.add_argument('--lidar-model', choices=('mid360','mid360s'), default='mid360s')
    parser.add_argument('--lidar-ip', default='192.168.1.3')
    parser.add_argument('--radar-status-file', default='/srv/team28/runtime/radar/status.json')
    parser.add_argument('--enable-drive', action='store_true', help='Enable tested chassis controls for connected Tailscale operators')
    args = parser.parse_args()
    sys.path.insert(0, '/srv/team28/Team-28')
    instance_lock = (ROOT / '.gateway.lock').open('a')
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('WaveSense is already running from this folder')
    app = Gateway(args)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    server.app = app
    atexit.register(app.close)
    def exit_signal(*_):
        app.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, exit_signal)
    signal.signal(signal.SIGINT, exit_signal)
    app.vision.start()
    for target in (app.camera.run, app.thermal.run, app.watchdog, app.wifi_poll, app.lidar_poll, app.radar_poll):
        threading.Thread(target=target, daemon=True).start()
    for sensor in ('lidar','radar'):
        try: app.sensor_modes.set_enabled(sensor, True)
        except Exception as e: print(f'{sensor} startup: {type(e).__name__}', flush=True)
    print(f'WaveSense listening on http://{args.bind}:{args.port}; motors disarmed', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
