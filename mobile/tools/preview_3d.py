#!/usr/bin/env python3
"""Explicit, loopback-only synthetic graphics fixture. Never imports robot or sensor code."""
import json,math,struct,time,random
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
points=[]
for i in range(121):
 for j in range(51):
  x=-3+i*.05;z=-.8+j*.06
  for y in (-2,2):points.append((x,y,z,150))
for i in range(81):
 for j in range(51):
  y=-2+i*.05;z=-.8+j*.06
  for x in (-3,3):points.append((x,y,z,180))
# A clearly geometric box, not a simulated person.
for i in range(25):
 for j in range(25):points.append((.5+i*.04,.2+j*.04,.1,210))
points=random.Random(28).sample(points,min(16000,len(points)))
payload=b''.join(struct.pack('<hhhBB',round(x*100),round(y*100),round(z*100),r,0) for x,y,z,r in points)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def send(self,code,body,content_type='application/json'):
  if isinstance(body,dict):body=json.dumps(body).encode()
  self.send_response(code);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
 def do_POST(self):self.send(403,{'error':'Synthetic graphics test only; no robot connection'})
 def do_GET(self):
  path=self.path.split('?')[0]
  if path=='/api/session':return self.send(200,{'ok':True})
  if path=='/api/status':return self.send(200,{'camera':{'online':False},'thermal':{'online':False},'wifi':{'online':False},'radar':{'online':False,'reason':'Synthetic fixture; radar not simulated'},'lidar':{'mode':'3d','online':True,'reason':'SIMULATED TEST DATA','points':[]},'control':{'armed':False,'mine':False,'enabled':False,'motor_access':False}})
  if path=='/api/lidar3d.bin':return self.send(200,struct.pack('<4sBBHIIQff',b'WS3D',1,8,32,int(time.monotonic()*5)%2**32,len(points),int(time.time()*1000),0,.3)+payload,'application/octet-stream')
  if path=='/':
   html=(ROOT/'index.html').read_text().replace('WAVESENSE</div>','WAVESENSE / TEST</div>')
   html=html.replace('<body>','<body><div style="position:fixed;bottom:0;left:0;right:0;z-index:99;text-align:center;font:10px monospace;color:#ffce72;background:#17120a">SIMULATED GRAPHICS TEST · NO ROBOT CONNECTION</div>')
   return self.send(200,html.encode(),'text/html')
  target=(ROOT/path.lstrip('/')).resolve()
  if not target.is_relative_to(ROOT) or target.suffix not in ('.js','.mjs','.css','.svg','.webmanifest','.png') or not target.is_file():return self.send(404,{'error':'Not found'})
  return self.send(200,target.read_bytes(),{'.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'}.get(target.suffix,'application/json'))
if __name__=='__main__':
 print('Synthetic graphics fixture: http://127.0.0.1:8766 (no hardware)',flush=True)
 ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
