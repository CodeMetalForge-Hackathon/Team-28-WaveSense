#!/usr/bin/env python3
"""Loopback-only UI fixture: supplied camera test image + explicitly synthetic radar/scan.
Never imports hardware code. Never use this fixture as the robot server.
"""
import argparse
import json
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path, help='Already annotated test JPEG')
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    jpeg = args.image.read_bytes()
    points = []
    for i in range(100):
        x = -2 + i*.04
        points.extend([[x, 3], [x, -1]])
        y = -1 + i*.04
        points.extend([[-2, y], [2, y]])
    points = [[math.degrees(math.atan2(-x, y)), math.hypot(x, y)*1000] for x, y in points]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def send(self, code, body, mime='application/json'):
            if isinstance(body, dict): body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self): self.send(403, {'error': 'UI fixture only; no robot connection'})
        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/api/session': return self.send(200, {'ok': True})
            if path == '/api/status':
                return self.send(200, {
                    'camera': {'online': True}, 'vision': {'online': True},
                    'thermal': {'online': False}, 'wifi': {'online': False},
                    'radar': {'online': True, 'state': 'candidate'},
                    'evidence': {'radar_candidate': True, 'visible_person': True,
                                 'ranges_m': [1.8], 'spatially_associated': False},
                    'lidar': {'mode': '2d', 'online': True, 'points': points,
                              'mapping': {'available': False, 'online': False}},
                    'control': {'enabled': False, 'armed': False, 'mine': False, 'motor_access': False}})
            if path in ('/api/camera.mjpg', '/api/camera-analysis.mjpg'):
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                try:
                    self.connection.settimeout(3)
                    while True:
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n')
                        self.wfile.flush()
                        time.sleep(.25)
                except OSError: pass
                return
            if path == '/':
                html = (ROOT/'index.html').read_text().replace('WAVESENSE</div>', 'WAVESENSE / TEST</div>')
                html = html.replace('<body>', '<body><div style="position:fixed;bottom:0;left:0;right:0;z-index:99;text-align:center;font:10px monospace;color:#ffce72;background:#17120a">TEST IMAGE + SYNTHETIC RADAR / SCAN · NO ROBOT</div>')
                return self.send(200, html.encode(), 'text/html')
            target = (ROOT/path.lstrip('/')).resolve()
            if ROOT not in target.parents or target.suffix not in ('.js', '.mjs', '.css', '.svg', '.png', '.webmanifest') or not target.is_file():
                return self.send(404, {'error': 'Not found'})
            return self.send(200, target.read_bytes(), {'.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'}.get(target.suffix,'application/json'))
    print(f'UI test only: http://127.0.0.1:{args.port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__': main()
