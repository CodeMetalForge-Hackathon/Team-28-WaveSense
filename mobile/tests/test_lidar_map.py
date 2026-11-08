import importlib.util,math
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('lidar_map',Path(__file__).parents[1]/'lidar_map.py')
view=importlib.util.module_from_spec(spec);spec.loader.exec_module(view)
class MapCoordinates(unittest.TestCase):
 def test_world_y_is_up_in_image(self):
  self.assertEqual(view.pixel_pose((1000,2000,450),25,800),[40,719,90])
 def test_invalid_pose_does_not_become_phantom_robot(self):
  for pose,scale in [((float('nan'),0,0),25),((0,0,0),0)]:
   with self.assertRaises(ValueError):view.pixel_pose(pose,scale,800)
 def test_long_path_is_bounded_without_losing_endpoints(self):
  points=[(i,i/2,0) for i in range(10000)]
  result=view.bounded_trail(points,25,800)
  self.assertEqual(len(result),400)
  self.assertEqual(result[0],view.pixel_pose(points[0],25,800)[:2])
  self.assertEqual(result[-1],view.pixel_pose(points[-1],25,800)[:2])
 def test_stale_scan_never_reports_tracking_live(self):
  feed=view.MapFeed();snap={'status':'ok','age_ms':20,'data':{'mm_per_px':25}}
  self.assertTrue(feed.update(snap,True)['online'])
  self.assertFalse(feed.update(snap,False)['online'])
  snap['age_ms']=1001;self.assertFalse(feed.update(snap,True)['online'])
 def test_revision_survives_javascript_number_roundtrip(self):
  feed=view.MapFeed();self.assertEqual(int(float(feed.revision+1)),feed.revision+1)
