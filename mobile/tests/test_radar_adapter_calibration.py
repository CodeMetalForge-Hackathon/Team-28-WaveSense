import importlib.util,sys,types,unittest,signal
from pathlib import Path
from unittest.mock import patch
try:import numpy as np
except ImportError:np=None
RADAR=Path(__file__).parents[1]/'integration/radar'
sys.path.insert(0,str(RADAR))

@unittest.skipIf(np is None,'Needs numpy')
class FactoryCalibrationRead(unittest.TestCase):
 def test_factory_read_precedes_capture_and_missing_calibration_is_range_only(self):
  spec=importlib.util.spec_from_file_location('adapter_calibration_test',RADAR/'tinyrad_dev.py');adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
  for fail in [False,True]:
   order=[]
   class Board:
    def BrdRst(self):order.append('reset')
    def BrdGetCalDat(self):
     order.append('read_factory')
     if fail:raise IOError('no factory correction')
     return {'Dat':np.ones(8,dtype=complex)}
    def RfRxEna(self):order.append('rx')
    def RfTxEna(self,*a):order.append('tx')
    def RfMeas(self,*a):order.append('capture')
   modules={'src':types.ModuleType('src'),'src.cmd_modules':types.ModuleType('src.cmd_modules'),'src.cmd_modules.TinyRad':types.SimpleNamespace(TinyRad=Board),'src.cmd_modules.Connection':types.SimpleNamespace(signal=signal)}
   modules['src.cmd_modules'].Connection=modules['src.cmd_modules.Connection']
   with patch.dict(sys.modules,modules),patch.object(adapter.TinyRadDevice,'usb_reset'),patch.object(adapter,'_stub_pyqtgraph'):
    device=adapter.TinyRadDevice().start()
   self.assertLess(order.index('read_factory'),order.index('capture'))
   if fail:self.assertIsNone(device.rx_calibration)
   else:np.testing.assert_array_equal(device.rx_calibration,np.ones(4))
