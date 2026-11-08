import sys,unittest,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from sensor_evidence import compose_evidence
from person_vision import restore_box, PersonVision
from unittest.mock import patch
class EvidenceTests(unittest.TestCase):
 def test_radar_alone_does_not_locate_human_or_invent_camera_box(self):
  e=compose_evidence({'online':True,'state':'candidate','tracks':[{'range_m':1.5,'bearing_deg':None}]},{'online':True,'boxes':[]})
  self.assertTrue(e['radar_candidate']);self.assertEqual(e['ranges_m'],[1.5]);self.assertFalse(e['visible_person']);self.assertFalse(e['spatially_associated']);self.assertEqual(e['occlusion'],'unknown');self.assertFalse(e['human_confirmed'])
 def test_simultaneous_evidence_is_not_association(self):
  e=compose_evidence({'online':True,'state':'motion','tracks':[{'range_m':2}]},{'online':True,'boxes':[{'box':[0,0,20,50]}]})
  self.assertTrue(e['visible_person']);self.assertTrue(e['radar_candidate']);self.assertFalse(e['spatially_associated'])
 def test_offline_or_moving_suppresses_radar_cue(self):
  r={'online':True,'state':'candidate','tracks':[{'range_m':1}]}
  self.assertEqual(compose_evidence(r,{},True)['state'],'paused');self.assertEqual(compose_evidence(r,{},True)['ranges_m'],[])
  r['online']=False;self.assertFalse(compose_evidence(r,{})['radar_candidate'])
 def test_letterbox_coordinates_restore_landscape_and_clip(self):
  self.assertEqual(restore_box([65,117,195,247],.65,(0,52),640,480),[100,100,300,300])
  self.assertEqual(restore_box([-1,0,500,416],.65,(0,52),640,480),[0,0,640,480])
 def test_invalid_boxes_are_not_drawn(self):
  self.assertIsNone(restore_box([float('nan'),0,2,4],1,(0,0),640,480));self.assertIsNone(restore_box([5,5,1,1],1,(0,0),640,480))
 def test_stale_analysis_clears_boxes_without_a_new_frame(self):
  vision=PersonVision(None,Path(__file__).parents[1]);vision.updated=10;vision.meta={'boxes':[{'box':[0,0,20,50]}]}
  with patch('person_vision.time.monotonic',return_value=10.5):self.assertTrue(vision.details()['boxes'])
  with patch('person_vision.time.monotonic',return_value=11.1):
   self.assertFalse(vision.details()['online']);self.assertEqual(vision.details()['boxes'],[])
