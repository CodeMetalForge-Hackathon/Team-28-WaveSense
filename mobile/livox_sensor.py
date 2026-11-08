"""Livox SDK2 subprocess, checked UDP decoding and bounded sensor-frame previews.
No chassis imports, motor devices, firmware changes or network reconfiguration.
"""
from collections import deque
import binascii
import ipaddress
import math
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

HEADER = struct.Struct('<4sBBHIIQff')  # WS3D, v1, stride8, header32, seq, count, receive_ms, age_ms, window_s


def decode_packet(packet):
    import numpy as np
    if len(packet) < 36 or packet[0] != 0:
        raise ValueError('Unsupported or truncated Livox header')
    length, count = struct.unpack_from('<H', packet, 1)[0], struct.unpack_from('<H', packet, 5)[0]
    kind = packet[10]
    if length != len(packet) or length > 1400 or not 0 < count <= 128:
        raise ValueError('Invalid Livox length or point count')
    if binascii.crc32(packet[28:]) & 0xffffffff != struct.unpack_from('<I', packet, 24)[0]:
        raise ValueError('Livox payload CRC mismatch')
    widths = {0: 24, 1: 14, 2: 8, 3: 10}
    if kind not in widths or len(packet) != 36 + count*widths[kind]:
        raise ValueError('Unsupported or malformed Livox data type')
    timestamp = struct.unpack_from('<Q', packet, 28)[0]
    if kind == 0:
        vals = struct.unpack_from('<6f', packet, 36)
        if count != 1 or not all(math.isfinite(x) for x in vals):
            raise ValueError('Invalid IMU sample')
        return {'kind': 'imu', 'gyro_rad_s': vals[:3], 'accel_g': vals[3:], 'sensor_ns': str(timestamp)}
    if kind in (1, 2):
        unit = .001 if kind == 1 else .01
        dtype = np.dtype([('xyz', '<i4' if kind == 1 else '<i2', (3,)), ('r', 'u1'), ('tag', 'u1')])
        raw = np.frombuffer(packet, dtype=dtype, count=count, offset=36)
        xyz, reflectivity, tag = raw['xyz'].astype(np.float32)*unit, raw['r'], raw['tag']
    else:
        raw = np.frombuffer(packet, dtype=np.dtype([('d','<u4'),('theta','<u2'),('phi','<u2'),('r','u1'),('tag','u1')]), count=count, offset=36)
        d, th, ph = raw['d']*.001, np.radians(raw['theta']*.01), np.radians(raw['phi']*.01)
        xyz = np.stack([d*np.sin(th)*np.cos(ph), d*np.sin(th)*np.sin(ph), d*np.cos(th)], axis=1).astype(np.float32)
        reflectivity, tag = raw['r'], raw['tag']
    dist2 = (xyz*xyz).sum(axis=1)
    # Exclude no-return origin, close blind zone, points outside local preview, and tagged low confidence.
    good = np.isfinite(xyz).all(axis=1) & (dist2 >= .1**2) & (dist2 <= 35**2) & ((tag & 0x3f) == 0)
    return {'kind':'points', 'points':np.column_stack((xyz[good],reflectivity[good],tag[good])),
            'sensor_ns':str(timestamp), 'raw_count':count, 'filtered':int((~good).sum())}


def pack_cloud(points, sequence, received_ms, age_ms=0, window_s=.3):
    import numpy as np
    if len(points) > 18000:
        points = points[np.linspace(0,len(points)-1,18000,dtype=int)]
    packed = np.empty(len(points), dtype=np.dtype([('xyz','<i2',(3,)),('r','u1'),('tag','u1')]))
    packed['xyz'] = np.rint(points[:,:3]*100).astype('<i2')  # centimetres, covers ±327 m
    packed['r'], packed['tag'] = points[:,3].astype('u1'), points[:,4].astype('u1')
    return HEADER.pack(b'WS3D',1,8,32,sequence & 0xffffffff,len(points),int(received_ms),float(age_ms),window_s)+packed.tobytes()


class Livox:
    def __init__(self, root, ip='192.168.1.3', interface='eth0', window_s=.3, model='mid360s'):
        self.root, self.ip, self.interface, self.window_s = Path(root), str(ipaddress.IPv4Address(ip)), interface, window_s
        if model not in ('mid360','mid360s'):raise ValueError('Unknown Livox model')
        self.model = model
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run_guarded, name='livox-preview', daemon=True)
        self.last_seen = None
        self.frame = None
        self.meta = {'online':False, 'reason':'Starting Livox receiver', 'source':'livox', 'frame':'sensor', 'mount_verified':False}
        self.worker = None
        self.socket = None

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.done.set()
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
        self.thread.join(3)
        if self.worker and self.worker.poll() is None:
            self.worker.kill()

    def snapshot(self):
        with self.lock:
            age = None if self.last_seen is None else (time.monotonic()-self.last_seen)*1000
            fresh=bool(age is not None and age <= 750)
            reason=self.meta['reason'] if fresh or self.last_seen is None else 'Point cloud stale: check sensor power and Ethernet'
            return dict(self.meta, reason=reason, online=fresh, age_ms=round(age) if age is not None else None)

    def binary(self):
        with self.lock:
            if self.last_seen is None or time.monotonic()-self.last_seen > .75:
                return None
            age=(time.monotonic()-self.last_seen)*1000
            return self.frame[:24]+struct.pack('<f',age)+self.frame[28:] if self.frame else None

    def _run_guarded(self):
        try:
            self.run()
        except Exception as e:
            with self.lock:
                self.meta['reason'] = 'Livox receiver error: '+type(e).__name__

    def run(self):
        import numpy as np
        batches, rates = deque(), deque()
        sequence = packets = rejected = filtered = 0
        publish_at = 0
        last_worker_attempt = 0
        last_point = None
        log_path = self.root/'livox-worker.log'
        self.socket = sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,2_000_000)
        sock.bind(('127.0.0.1',0));sock.settimeout(.05)
        binary = self.root/'bin/livox-bridge'
        config = json.loads((self.root/'livox/config.json').read_text())
        sensor = config.pop('Mid360s')
        sensor['host_net_info'][0]['lidar_ip'] = [self.ip]
        config['MID360' if self.model=='mid360' else 'Mid360s'] = sensor
        runtime_config = self.root/'.livox-config.json'
        runtime_config.write_text(json.dumps(config))
        wanted_ip = ipaddress.IPv4Address(self.ip).packed
        try:
            while not self.done.is_set():
                now = time.monotonic()
                if self.worker is None or self.worker.poll() is not None:
                    if now-last_worker_attempt > 3:
                        last_worker_attempt = now
                        reason = 'Waiting for Livox packets'
                        carrier = Path('/sys/class/net')/self.interface/'carrier'
                        if carrier.exists() and carrier.read_text().strip() != '1':
                            reason = 'Ethernet link down: check LiDAR power and cable'
                        elif not binary.exists():
                            reason = 'Livox bridge not built'
                        else:
                            if log_path.exists() and log_path.stat().st_size > 2_000_000:
                                log_path.write_text('')
                            with log_path.open('ab') as log:
                                self.worker = subprocess.Popen([str(binary),str(runtime_config),self.ip,str(sock.getsockname()[1])], stdout=subprocess.DEVNULL,stderr=log)
                        with self.lock:self.meta['reason'] = reason
                try:
                    data, peer = sock.recvfrom(4096)
                    if peer[0] != '127.0.0.1' or len(data)<45 or data[:4]!=b'WSLV' or data[4:8]!=wanted_ip:
                        rejected += 1;continue
                    sample = decode_packet(data[9:]);packets += 1
                    if sample['kind'] == 'imu':
                        with self.lock:self.meta['imu'] = {k:sample[k] for k in ('gyro_rad_s','accel_g','sensor_ns')}
                        continue
                    now = time.monotonic()
                    if last_point is not None and now-last_point > .75:
                        batches.clear()  # Never combine pre-unplug and post-unplug coordinates.
                    last_point = now
                    batches.append((now,sample['points']));rates.append((now,sample['raw_count']))
                    filtered += sample['filtered']
                    while batches and now-batches[0][0] > self.window_s:batches.popleft()
                    while rates and now-rates[0][0]>1:rates.popleft()
                    if now < publish_at:continue
                    publish_at = now+.2
                    pts = np.concatenate([p for _,p in reversed(batches)])
                    if len(pts):
                        _, unique = np.unique(np.floor(pts[:,:3]/.08).astype(np.int32),axis=0,return_index=True)
                        pts = pts[unique]
                    sequence += 1
                    received_ms = int(time.time()*1000)
                    frame = pack_cloud(pts,sequence,received_ms,0,self.window_s)
                    with self.lock:
                        self.frame, self.last_seen = frame, now
                        self.meta.update(reason='Live point cloud', model={9:'MID-360',35:'MID-360S'}.get(data[8],'Livox '+str(data[8])),
                                         ip=self.ip, points=min(len(pts),18000), point_rate=sum(n for _,n in rates),
                                         packets=packets,rejected_packets=rejected,filtered_points=filtered,sequence=sequence,
                                         received_ms=received_ms,window_s=self.window_s,voxel_m=.08)
                except socket.timeout:
                    pass
                except (ValueError, OSError):
                    rejected += 1
        finally:
            if self.worker and self.worker.poll() is None:
                self.worker.terminate()
                try:self.worker.wait(2)
                except subprocess.TimeoutExpired:self.worker.kill();self.worker.wait()
            sock.close()
