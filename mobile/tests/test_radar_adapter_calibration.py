import importlib.util,struct,sys,types,unittest,signal
from pathlib import Path
from unittest.mock import patch
try:import numpy as np
except ImportError:np=None
RADAR=Path(__file__).parents[1]/'integration/radar'
sys.path.insert(0,str(RADAR))

def eeprom(values):
 """EEPROM bytes in ADI's layout: little-endian signed re/im words scaled by 2**24."""
 return b''.join(struct.pack('<2i',round(complex(v).real*2**24),round(complex(v).imag*2**24)) for v in values)

@unittest.skipIf(np is None,'Needs numpy')
class FactoryCalibrationRead(unittest.TestCase):
 def test_factory_read_precedes_capture_and_missing_calibration_is_range_only(self):
  spec=importlib.util.spec_from_file_location('adapter_calibration_test',RADAR/'tinyrad_dev.py');adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
  for fail in [False,True]:
   order=[]
   class Board:
    def BrdRst(self):order.append('reset')
    def BrdRdEEPROM(self,addr):
     order.append('read_factory')
     if fail:raise IOError('no factory correction')
     return [0,[eeprom([1,1,1,1])[addr]]]
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

 def test_eeprom_words_keep_high_bytes_and_sign_under_numpy2(self):
  spec=importlib.util.spec_from_file_location('adapter_decode_test',RADAR/'tinyrad_dev.py');adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
  want=[1+0j,-.5+.25j,.75-.125j,-1-1j];raw=eeprom(want)
  board=types.SimpleNamespace(BrdRdEEPROM=lambda addr:[0,[np.uint8(raw[addr])]])
  np.testing.assert_array_equal(adapter.read_factory_calibration(board),np.array(want))
