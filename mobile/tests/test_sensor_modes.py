import sys,tempfile,unittest,threading,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from sensor_modes import SensorModes

class SensorModeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.active=set();self.starts=0;owner=self
  class Process:
   def __init__(self,*a,**kw):
    owner.assertNotIn('radar',owner.active);owner.active.add('radar');owner.starts+=1;self.alive=True
   def poll(self):return None if self.alive else 0
   def terminate(self):self.alive=False;owner.active.discard('radar')
   def wait(self,timeout):return 0
  def start():self.assertNotIn('lidar',self.active);self.active.add('lidar')
  def stop():self.active.discard('lidar')
  self.m=SensorModes(start,stop,self.tmp.name,popen=Process,clear_export=lambda:None)
 def tearDown(self):self.m.stop_unconfirmed=False;self.m.close();self.tmp.cleanup()
 def test_both_stay_enabled_and_each_can_be_stopped_independently(self):
  self.m.set_enabled('radar',True);self.m.set_enabled('lidar',True)
  self.assertEqual(self.active,{'radar','lidar'});self.assertEqual(self.m.status()['mode'],'both')
  self.m.set_enabled('radar',False);self.assertEqual(self.active,{'lidar'})
  self.m.set_enabled('radar',True);self.m.set_enabled('lidar',False);self.assertEqual(self.active,{'radar'})
 def test_concurrent_independent_requests_do_not_lose_selection(self):
  errors=[]
  def enable(sensor):
   try:self.m.set_enabled(sensor,True)
   except Exception as e:errors.append(e)
  threads=[threading.Thread(target=enable,args=(m,)) for m in ['lidar','radar']*10]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(errors,[]);self.assertEqual(self.active,{'radar','lidar'});self.assertEqual(self.starts,1)
 def test_failed_start_preserves_other_sensor(self):
  self.m.set_enabled('radar',True)
  def fail():raise RuntimeError('missing scanner')
  self.m.start_lidar=fail
  with self.assertRaises(RuntimeError):self.m.set_enabled('lidar',True)
  self.assertEqual(self.active,{'radar'});self.assertEqual(self.m.status()['enabled'],{'radar':True,'lidar':False})
 def test_bad_input_cannot_change_selection(self):
  self.m.select('both')
  for sensor,enabled in [('both',True),('radar',1),('radar','false')]:
   with self.assertRaises(ValueError):self.m.set_enabled(sensor,enabled)
  self.assertEqual(self.active,{'radar','lidar'})
 def test_idempotence_and_all_off(self):
  self.m.select('both');self.m.set_enabled('radar',True);self.assertEqual(self.starts,1)
  self.m.select('off');self.assertFalse(self.active)
 def test_cleanup_stops_lidar_even_if_radar_shutdown_unconfirmed(self):
  self.m.select('both');self.m.stop_unconfirmed=True
  with self.assertRaises(RuntimeError):self.m.close()
  self.assertNotIn('lidar',self.active)

 def test_dead_worker_recovers_without_stopping_lidar(self):
  self.m.select('both');old=self.m.process;old.alive=False;self.active.discard('radar')
  self.m.recover_radar();self.assertIsNot(self.m.process,old);self.assertEqual(self.active,{'radar','lidar'})
  self.m.close();self.m.recover_radar();self.assertFalse(self.active)
