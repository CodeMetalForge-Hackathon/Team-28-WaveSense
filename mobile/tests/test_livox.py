import binascii,importlib.util,json,struct,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from livox_sensor import decode_packet,pack_cloud,Livox
from radar_status import read_radar
try:
 import numpy as np
except ImportError:
 np=None

def packet(kind,payload,count):
 h=bytearray(36);h[0]=0;struct.pack_into('<H',h,1,36+len(payload));struct.pack_into('<H',h,5,count);h[10]=kind;struct.pack_into('<Q',h,28,123456789)
 struct.pack_into('<I',h,24,binascii.crc32(h[28:]+payload)&0xffffffff)
 return bytes(h)+payload

@unittest.skipUnless(np is not None,'Run with the Pi shared virtual environment (numpy)')
class LivoxProtocol(unittest.TestCase):
 def test_millimetres_and_confidence_filter(self):
  payload=b''.join(struct.pack('<iiiBB',*p) for p in [(1000,2000,-500,88,0),(0,0,0,0,0),(2000,0,0,100,2)])
  s=decode_packet(packet(1,payload,3));np.testing.assert_allclose(s['points'][0],[1,2,-.5,88,0]);self.assertEqual(len(s['points']),1);self.assertEqual(s['filtered'],2)
 def test_low_resolution_units_are_centimetres(self):
  s=decode_packet(packet(2,struct.pack('<hhhBB',100,20,-30,44,0),1));np.testing.assert_allclose(s['points'][0][:3],[1,.2,-.3],atol=1e-6)
 def test_spherical_axis_conversion(self):
  s=decode_packet(packet(3,struct.pack('<IHHBB',2000,9000,9000,40,0),1));np.testing.assert_allclose(s['points'][0][:3],[0,2,0],atol=1e-6)
 def test_imu_is_not_a_point_cloud(self):
  s=decode_packet(packet(0,struct.pack('<6f',1,2,3,0,0,1),1));self.assertEqual(s['kind'],'imu');self.assertEqual(s['gyro_rad_s'],(1,2,3))
 def test_crc_truncation_and_count_rejected(self):
  p=packet(1,struct.pack('<iiiBB',1000,0,0,10,0),1)
  for bad in [p[:-1],p[:-1]+b'\x01',p[:5]+b'\xff\xff'+p[7:]]:
   with self.assertRaises(ValueError):decode_packet(bad)
 def test_phone_binary_cm_scale_and_limit(self):
  pts=np.tile([1.23,-2.34,.56,88,0],(20000,1));b=pack_cloud(pts,9,1234)
  self.assertEqual(b[:4],b'WS3D');self.assertEqual(struct.unpack_from('<I',b,12)[0],18000);self.assertEqual(len(b),32+18000*8)
  self.assertEqual(struct.unpack_from('<hhhBB',b,32),(123,-234,56,88,0))
 def test_stale_frame_is_not_served(self):
  s=Livox('/missing');s.frame=b'x';s.last_seen=time.monotonic()-1;self.assertIsNone(s.binary());self.assertFalse(s.snapshot()['online'])
 def test_receiver_lifecycle_with_simulated_sdk_transport(self):
  import shutil
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'bin').mkdir();(root/'livox').mkdir()
   shutil.copy2(Path(__file__).parents[1]/'livox/config.json',root/'livox/config.json')
   datagram=b'WSLV'+bytes([192,168,1,3,35])+packet(1,struct.pack('<iiiBB',1000,2000,100,50,0),1)
   code='#!'+sys.executable+'\nimport socket,sys,time\ns=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\nfor i in range(15):\n s.sendto(bytes.fromhex('+repr(datagram.hex())+'),("127.0.0.1",int(sys.argv[3])))\n time.sleep(.04)\n'
   worker=root/'bin/livox-bridge';worker.write_text(code);worker.chmod(0o755)
   sensor=Livox(root,interface='TEST_INTERFACE').start()
   try:
    end=time.monotonic()+3
    while sensor.binary() is None and time.monotonic()<end:time.sleep(.02)
    self.assertIsNotNone(sensor.binary(),'Loopback SDK packet did not reach phone publisher')
    self.assertTrue(sensor.snapshot()['online']);self.assertEqual(sensor.snapshot()['model'],'MID-360S')
   finally:sensor.stop()
   self.assertFalse(sensor.thread.is_alive());self.assertIsNotNone(sensor.worker.poll())

class RadarContract(unittest.TestCase):
 def test_no_feed_never_invents_people(self):
  s=read_radar('/missing');self.assertFalse(s['online']);self.assertEqual(s['tracks'],[])
 def test_fresh_candidate_is_separate_and_unregistered(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'status.json';p.write_text(json.dumps({'schema':1,'timestamp_ms':100000,'online':True,'state':'candidate','tracks':[{'range_m':2.5}]}))
   s=read_radar(p,now=100.1);self.assertTrue(s['online']);self.assertIsNone(s['tracks'][0]['bearing_deg']);self.assertFalse(s['spatial_alignment_verified']);self.assertFalse(s['through_wall_validated'])
   self.assertFalse(read_radar(p,now=103)['online'])
