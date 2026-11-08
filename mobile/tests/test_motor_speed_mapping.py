import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
from test_gateway import gateway
class ChassisFake:
 min_duty=35;duty_cap=40;error=None
 def __init__(self):self.commands=[];self.stops=[]
 def set_velocity(self,**v):self.commands.append((self.duty_cap,v))
 def stop(self,reason):self.stops.append(reason)
 def status(self):return {'error':None,'deadman_alive':True,'battery_age_s':.1}
class MotorSpeedMapping(unittest.TestCase):
 def test_slider_changes_real_cap_across_usable_driver_range(self):
  motors=gateway.Motors('/never-opened');motors.chassis=ChassisFake()
  for percent,expected in [(25,51.25),(50,67.5),(100,100)]:
   motors.send(.35*percent/100,0,max_speed=percent)
   cap,v=motors.chassis.commands[-1];self.assertEqual(cap,expected);self.assertAlmostEqual(v['vx'],1)
  motors.send(0,0,max_speed=0);self.assertTrue(motors.chassis.stops)
 def test_healthy_idle_connection_is_reused_without_opening_uart(self):
  motors=gateway.Motors('/never-opened');fake=ChassisFake();motors.chassis=fake
  motors.idle();motors.open();self.assertIs(motors.chassis,fake);self.assertEqual(fake.stops,['sticks released'])

 def test_open_requests_full_duty_and_fast_slew_without_real_uart(self):
  import types
  fake=ChassisFake();received={}
  def construct(**kwargs):received.update(kwargs);return fake
  parent=types.ModuleType('robot');parent.__path__=[]
  with patch.dict(sys.modules,{'robot':parent,'robot.chassis':types.SimpleNamespace(Chassis=construct)}):
   motors=gateway.Motors('/never-opened');motors.open()
  self.assertEqual(received['duty_cap'],100)
  self.assertEqual(received['slew'],1000)
  self.assertEqual(received['watchdog_s'],.3)
  self.assertTrue(received['deadman'])
 def test_actual_driver_ceiling_is_respected(self):
  motors=gateway.Motors('/never-opened');motors.chassis=ChassisFake();motors.max_duty=50
  motors.send(.35,0,max_speed=100)
  self.assertEqual(motors.chassis.commands[-1][0],50)
