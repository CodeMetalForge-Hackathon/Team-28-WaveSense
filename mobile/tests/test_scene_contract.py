import unittest,sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from scene_contract import clean_scene

class SceneContractTests(unittest.TestCase):
 def setUp(self):self.scene={'schema':1,'timestamp_ms':100000,'ranges_m':[.3,.6,.9],'profile_db':[-40,-10,0],'angle_available':True,'angles_deg':[-30,0,30],'angle_power_u8':[[0,0,0],[0,255,0],[0,0,1]],'target_range_m':.6,'target_angle_deg':0,'mounting_verified':True,'human_confirmed':True}
 def test_valid_return_does_not_certify_human_or_mounting(self):
  s=clean_scene(self.scene,100.5);self.assertTrue(s['angle_available']);self.assertFalse(s['human_confirmed']);self.assertFalse(s['mounting_verified']);self.assertEqual(s['target_angle_deg'],0)
 def test_old_return_and_bad_profile_rejected(self):
  self.assertIsNone(clean_scene(self.scene,103))
  s=copy.deepcopy(self.scene);s['profile_db'][0]=float('nan');self.assertIsNone(clean_scene(s,100.1))
  s=copy.deepcopy(self.scene);s['ranges_m']=[.6,.3,.9];self.assertIsNone(clean_scene(s,100.1))
 def test_bad_angle_grid_keeps_only_range_data(self):
  s=copy.deepcopy(self.scene);s['angle_power_u8'][0]=[float('nan'),0,0];r=clean_scene(s,100.1);self.assertFalse(r['angle_available']);self.assertNotIn('target_angle_deg',r)
