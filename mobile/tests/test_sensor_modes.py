import sys,tempfile,unittest,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from sensor_modes import SensorModes

class SensorModeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.events=[];self.active=set()
  owner=self
  class Process:
   def __init__(self,*a,**kw):
    owner.assertFalse(owner.active);owner.active.add('radar');self.alive=True
   def poll(self):return None if self.alive else 0
   def terminate(self):owner.events.append('radar-stop');self.alive=False;owner.active.discard('radar')
   def wait(self,timeout):return 0
  def start():self.assertFalse(self.active);self.events.append('lidar-start');self.active.add('lidar')
  def stop():self.events.append('lidar-stop');self.active.discard('lidar')
  self.m=SensorModes(start,stop,self.tmp.name,popen=Process,clear_export=lambda:None)
 def tearDown(self):self.m.close();self.tmp.cleanup()
 def test_switches_never_overlap_and_selection_sticks(self):
  self.m.select('lidar');self.assertEqual(self.active,{'lidar'})
  self.m.select('radar');self.assertEqual(self.active,{'radar'});self.assertEqual(self.m.status()['mode'],'radar')
  self.m.select('radar');self.assertEqual(self.active,{'radar'})
  self.m.select('lidar');self.assertEqual(self.active,{'lidar'})
  self.m.select('off');self.assertFalse(self.active)
 def test_failed_stop_never_starts_other_sensor(self):
  self.m.select('lidar');original=self.m.stop_lidar
  def fail():raise RuntimeError('still running')
  self.m.stop_lidar=fail
  with self.assertRaises(RuntimeError):self.m.select('radar')
  self.assertEqual(self.active,{'lidar'});self.assertIsNone(self.m.process)
  self.m.stop_lidar=original
 def test_concurrent_requests_are_serialized(self):
  threads=[threading.Thread(target=self.m.select,args=(m,)) for m in ['lidar','radar']*10]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertLessEqual(len(self.active),1)
 def test_invalid_selection_does_not_change_state(self):
  self.m.select('radar')
  with self.assertRaises(ValueError):self.m.select('both')
  self.assertEqual(self.active,{'radar'})

 def test_unconfirmed_radar_stop_blocks_lidar(self):
  import subprocess
  self.m.select('radar');p=self.m.process
  def wait(timeout):
   if timeout==8:raise subprocess.TimeoutExpired('radar',8)
   return 0
  p.wait=wait;p.kill=lambda:None
  with self.assertRaises(RuntimeError):self.m.select('lidar')
  self.assertNotIn('lidar',self.active)
  with self.assertRaises(RuntimeError):self.m.select('lidar')
  self.m.stop_unconfirmed=False
