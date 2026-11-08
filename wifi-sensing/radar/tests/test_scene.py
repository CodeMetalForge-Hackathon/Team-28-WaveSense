import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
try:
 import numpy as np
 from radar_scene import RadarScene
except ImportError:np=None

@unittest.skipIf(np is None,'Needs numpy / Pi venv')
class RadarSceneTests(unittest.TestCase):
 def make(self,cal=None):return RadarScene(np.arange(257)*.299792458,.012426,cal)
 def target(self,theta=24,errors=None):
  phase=2*np.pi*np.arange(4)*.006217*np.sin(np.deg2rad(theta))/.012426
  if errors is not None:phase+=errors
  return 8000*np.cos(2*np.pi*10*np.arange(256)[:,None]/512+phase)
 def test_known_range_and_calibrated_plane_wave_angle(self):
  scene=self.make(np.ones(4));scene.feed(100,self.target());s=scene.snapshot(100.1)
  self.assertAlmostEqual(s['ranges_m'][int(np.argmax(s['profile_db']))],2.998,places=2)
  row=s['angle_power_u8'][int(np.argmax(s['profile_db']))]
  self.assertAlmostEqual(s['angles_deg'][int(np.argmax(row))],24,delta=3)
  self.assertFalse(s['mounting_verified']);self.assertFalse(s['human_confirmed']);self.assertFalse(s['mimo'])
 def test_factory_correction_restores_channel_phase_errors(self):
  errors=np.array([.7,-.8,.4,1.3]);scene=self.make(np.exp(-1j*errors));scene.feed(100,self.target(-21,errors));s=scene.snapshot(100)
  row=s['angle_power_u8'][int(np.argmax(s['profile_db']))];self.assertAlmostEqual(s['angles_deg'][int(np.argmax(row))],-21,delta=3)
 def test_missing_or_invalid_factory_coefficients_never_invent_direction(self):
  for cal in [None,np.zeros(4),[1,1,np.nan,1]]:
   scene=self.make(cal);scene.feed(100,self.target());s=scene.snapshot(100)
   self.assertFalse(s['angle_available']);self.assertNotIn('angle_power_u8',s)
 def test_stale_and_malformed_input(self):
  scene=self.make();scene.feed(100,self.target());self.assertIsNone(scene.snapshot(102))
  with self.assertRaises(ValueError):scene.feed(103,np.ones((10,4)))
 def test_counter_does_not_create_target_and_input_unchanged(self):
  frame=self.target();frame[:10]=1e9;copy=frame.copy();scene=self.make();scene.feed(100,frame)
  self.assertEqual(np.argmax(scene.snapshot(100)['profile_db']),np.argmin(np.abs(np.array(scene.snapshot(100)['ranges_m'])-2.998)))
  np.testing.assert_array_equal(frame,copy)

 def test_silence_is_not_a_strong_return(self):
  scene=self.make(np.ones(4));scene.feed(100,np.zeros((256,4)));s=scene.snapshot(100,target_range=3)
  self.assertTrue(all(x==-60 for x in s['profile_db']));self.assertTrue(all(not any(row) for row in s['angle_power_u8']));self.assertNotIn('target_angle_deg',s)
