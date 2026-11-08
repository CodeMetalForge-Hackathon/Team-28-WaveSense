import argparse
import sys
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
spec=importlib.util.spec_from_file_location('gateway',Path(__file__).parents[1]/'camera_preview.py')
gateway=importlib.util.module_from_spec(spec);spec.loader.exec_module(gateway)
class FakeMotors:
 def __init__(self):self.commands=[];self.closed=False
 def open(self):self.closed=False
 def send(self,l,a,sideways=0,max_speed=100):self.commands.append((l,a,sideways))
 def idle(self):self.commands.append((0,0,0))
 def close(self):self.closed=True
 def details(self):return {'moving':False,'deadman_alive':True}
class GatewayTests(unittest.TestCase):
 def setUp(self):
  args=argparse.Namespace(camera=None,thermal=None,motor_port='/missing',ruview_token_file='/missing',enable_drive=True)
  self.app=gateway.Gateway(args);self.app.motors=FakeMotors();self.app.camera_ready=lambda:True
 def drive(self,*args,**kwargs):return self.app.drive(*args,lease=self.app.lease,**kwargs)
 def test_old_lease_rejected_after_rearm(self):
  self.app.arm('a');old=self.app.lease;self.app.disarm('Stop');self.app.arm('a')
  with self.assertRaises(ValueError):self.app.drive('a',9,.35,0,lease=old)
 def test_requires_arm(self):
  with self.assertRaises(ValueError):self.drive('phone',1,.1,0)
 def test_no_operator_code_required(self):
  self.app.arm('phone');self.assertEqual(self.app.owner,'phone')
 def test_no_blind_arm(self):
  self.app.camera_ready=lambda:False
  with self.assertRaises(ValueError):self.app.arm('phone')
 def test_single_owner(self):
  self.app.arm('a')
  with self.assertRaises(ValueError):self.app.arm('b')
 def test_stale_commands_rejected(self):
  self.app.arm('a');self.drive('a',2,.1,0)
  with self.assertRaises(ValueError):self.drive('a',1,.2,0)
  self.assertEqual(self.app.motors.commands,[(0,0,0),(.1,0,0)])
 def test_nonfinite_overspeed_rejected_all_axes(self):
  self.app.arm('a')
  for value in (float('nan'),float('inf'),.36):
   with self.assertRaises(ValueError):self.drive('a',1,value,0)
   with self.assertRaises(ValueError):self.drive('a',1,0,0,value)
 def test_sideways_independent_rotation(self):
  self.app.arm('a');self.drive('a',1,.05,-.1,.2);self.assertEqual(self.app.motors.commands[-1],(.05,-.1,.2))
 def test_camera_loss_disarms(self):
  self.app.arm('a');self.app.camera_ready=lambda:False
  with self.assertRaises(ValueError):self.drive('a',1,.1,0)
  self.assertIsNone(self.app.owner);self.assertTrue(self.app.motors.closed)
 def test_stop_closes(self):
  self.app.arm('a');self.app.disarm('Stopped');self.assertTrue(self.app.motors.closed);self.assertIsNone(self.app.owner)
 def test_watchdog_expires(self):
  self.app.arm('a');self.app.last_command=gateway.time.monotonic()-1
  with patch.object(gateway.time,'sleep',side_effect=RuntimeError('end loop')):
   with self.assertRaises(RuntimeError):self.app.watchdog()
  self.assertIsNone(self.app.owner);self.assertTrue(self.app.motors.closed)
 def test_spectator_release_does_not_stop_owner(self):
  lease=self.app.arm('a');self.app.release('b',lease);self.assertEqual(self.app.owner,'a')
 def test_late_release_does_not_cancel_new_lease(self):
  old=self.app.arm('a');new=self.app.arm('a');self.app.release('a',old);self.assertEqual(self.app.lease,new)
 def test_release_stops_current_lease(self):
  lease=self.app.arm('a');self.app.release('a',lease);self.assertIsNone(self.app.owner);self.assertFalse(self.app.motors.closed);self.assertEqual(self.app.motors.commands[-1],(0,0,0))
 def test_late_forward_after_neutral_rejected(self):
  self.app.arm('a');self.drive('a',2,0,0)
  with self.assertRaises(gateway.StaleCommand):self.drive('a',1,.35,0)
  self.assertEqual(self.app.motors.commands[-1],(0,0,0))
 def test_default_watchdog_is_300_ms(self):self.assertEqual(gateway.WATCHDOG,.3)
 def test_radar_cue_pauses_through_release_and_settling_interval(self):
  self.app.radar={'online':True,'state':'candidate','tracks':[{'range_m':1.5}]}
  with patch.object(gateway.time,'monotonic',return_value=100):
   self.app.arm('a');self.drive('a',1,.1,0);self.drive('a',2,0,0);self.app.disarm('Released')
  with patch.object(gateway.time,'monotonic',return_value=102.9):self.assertEqual(self.app.status('a')['evidence']['state'],'paused')
  with patch.object(gateway.time,'monotonic',return_value=103.1):self.assertTrue(self.app.status('a')['evidence']['radar_candidate'])
if __name__=='__main__':unittest.main()

class WarmMotorSafety(unittest.TestCase):
 def setUp(self):
  helper=GatewayTests();helper.setUp();self.app=helper.app
 def watchdog_once(self):
  with patch.object(gateway.time,'sleep',side_effect=RuntimeError('end')):
   with self.assertRaises(RuntimeError):self.app.watchdog()
 def test_release_zeroes_and_invalidates_lease_while_ready(self):
  lease=self.app.arm('a');self.app.drive('a',1,.1,0,lease=lease);self.app.release('a',lease)
  self.assertEqual(self.app.motors.commands[-1],(0,0,0));self.assertIsNone(self.app.owner)
  self.assertFalse(self.app.motors.closed)
  with self.assertRaises(ValueError):self.app.drive('a',2,.1,0,lease=lease)
 def test_idle_timeout_and_camera_loss_close_connection(self):
  lease=self.app.arm('a');self.app.release('a',lease);self.app.motor_idle_until=gateway.time.monotonic()-1;self.watchdog_once();self.assertTrue(self.app.motors.closed)
  lease=self.app.arm('a');self.app.release('a',lease);self.app.camera_ready=lambda:False;self.watchdog_once();self.assertTrue(self.app.motors.closed)
 def test_selected_speed_is_validated_before_sending(self):
  lease=self.app.arm('a')
  for speed in [-1,101,float('nan'),True]:
   with self.assertRaises(ValueError):self.app.drive('a',1,.1,0,lease=lease,max_speed=speed)
  with self.assertRaises(ValueError):self.app.drive('a',1,.35,0,lease=lease,max_speed=50)
  self.app.drive('a',1,.175,0,lease=lease,max_speed=50)

 def test_stopped_connection_remains_ready_for_thirty_seconds(self):
  with patch.object(gateway.time,'monotonic',return_value=100):
   lease=self.app.arm('a');self.app.release('a',lease)
  self.assertEqual(self.app.motor_idle_until,130)
  with patch.object(gateway.time,'monotonic',return_value=125):self.watchdog_once()
  self.assertFalse(self.app.motors.closed)
  with patch.object(gateway.time,'monotonic',return_value=131):self.watchdog_once()
  self.assertTrue(self.app.motors.closed)

class RadarRelearnAfterDriving(unittest.TestCase):
 def setUp(self):
  helper=GatewayTests();helper.setUp();self.app=helper.app;self.calls=[]
 def post(self):self.calls.append(1)
 def at(self,now,post=None):
  with patch.object(gateway.time,'monotonic',return_value=now):self.app.tell_radar_if_stopped(post or self.post)
 def test_told_once_after_the_wheels_stop(self):
  self.app.last_motion_at=100
  self.at(100.5);self.assertEqual(self.calls,[])
  self.at(101.5);self.at(102);self.assertEqual(self.calls,[1])
 def test_each_new_drive_is_reported(self):
  self.app.last_motion_at=100;self.at(102)
  self.app.last_motion_at=110;self.at(112);self.assertEqual(self.calls,[1,1])
 def test_never_driven_tells_nothing(self):
  self.at(500);self.assertEqual(self.calls,[])
 def test_radar_being_off_is_harmless(self):
  def down():raise OSError('refused')
  self.app.last_motion_at=100;self.at(102,down);self.assertEqual(self.app.radar_told_of_motion,100)
