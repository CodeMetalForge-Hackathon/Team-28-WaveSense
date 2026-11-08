import sys,signal,threading,types,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'integration/radar'))
from radar_threading import guard_vendor_signals
class RadarThreadSignals(unittest.TestCase):
 def test_worker_constructor_leaves_process_handler_unchanged(self):
  module=types.SimpleNamespace(signal=signal);guard_vendor_signals(module);handler=signal.getsignal(signal.SIGINT);errors=[]
  def worker():
   try:module.signal.signal(signal.SIGINT,lambda *_:None)
   except Exception as e:errors.append(e)
  t=threading.Thread(target=worker);t.start();t.join();self.assertEqual(errors,[]);self.assertIs(signal.getsignal(signal.SIGINT),handler);self.assertIs(module.signal.original.signal,signal.signal)
 def test_main_thread_still_registers_and_guard_is_idempotent(self):
  module=types.SimpleNamespace(signal=signal);guard_vendor_signals(module);facade=module.signal;guard_vendor_signals(module);self.assertIs(facade,module.signal)
  old=signal.getsignal(signal.SIGINT);handler=lambda *_:None
  try:module.signal.signal(signal.SIGINT,handler);self.assertIs(signal.getsignal(signal.SIGINT),handler)
  finally:signal.signal(signal.SIGINT,old)
