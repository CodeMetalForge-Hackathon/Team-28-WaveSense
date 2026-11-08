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
  for percent,expected in [(25,38.75),(50,42.5),(100,50)]:
   motors.send(.35*percent/100,0,max_speed=percent)
   cap,v=motors.chassis.commands[-1];self.assertEqual(cap,expected);self.assertAlmostEqual(v['vx'],1)
  motors.send(0,0,max_speed=0);self.assertTrue(motors.chassis.stops)
 def test_healthy_idle_connection_is_reused_without_opening_uart(self):
  motors=gateway.Motors('/never-opened');fake=ChassisFake();motors.chassis=fake
  motors.idle();motors.open();self.assertIs(motors.chassis,fake);self.assertEqual(fake.stops,['sticks released'])
