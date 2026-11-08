import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

RADAR = Path(__file__).parents[1] / 'integration/radar'
sys.path.insert(0, str(RADAR))
from radar_recovery import RecoveryBackoff


class RecoveryTests(unittest.TestCase):
    def load_service(self):
        engine_stub = types.SimpleNamespace(RadarCalibration=object, RadarEngine=object)
        with patch.dict(sys.modules, {'radar_engine': engine_stub}):
            spec = importlib.util.spec_from_file_location('radar_recovery_service', RADAR/'radar_service.py')
            service = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(service)
        return service

    def test_open_success_followed_by_immediate_capture_failure_keeps_backing_off(self):
        # Exercise the actual acquisition loop without opening USB or importing ADI.
        service = self.load_service()
        class Stop:
            def __init__(self): self.delays = []; self.done = False
            def is_set(self): return self.done
            def wait(self, delay):
                self.delays.append(delay)
                self.done = len(self.delays) >= 4
                return self.done
        class Device:
            def __init__(self): self.starts = 0
            def frames(self, stop): raise OSError('USB lost on first frame')
            def start(self): self.starts += 1
            def close(self): pass
        svc = service.RadarService.__new__(service.RadarService)
        svc.dev, svc.stop, svc.lock = Device(), Stop(), threading.Lock()
        svc.eng = types.SimpleNamespace(buf=[], adaptive=False)
        svc.reconnects, svc.error = 0, None
        svc.acquire()
        self.assertEqual(svc.stop.delays, [3, 10, 30, 60])
        self.assertEqual(svc.dev.starts, 3)
        self.assertIsNotNone(svc.error)  # USB open alone never reports recovery.

    def test_sustained_frames_reset_failure_streak(self):
        recovery = RecoveryBackoff()
        self.assertEqual(recovery.failed(), 3)
        self.assertEqual(recovery.failed(), 10)
        recovery.received(100)
        recovery.received(100.5)
        self.assertEqual(recovery.failed(), 30)
        recovery.received(200)
        recovery.received(202.1)
        self.assertEqual(recovery.failed(), 3)

    def test_version_query_never_races_acquisition_usb_traffic(self):
        service = self.load_service()
        state = {'workers_started': False, 'raced': False}
        class Device:
            def start(self): return self
            def version(self):
                state['raced'] = state['workers_started']
                return {'version': 'fixture'}
            def close(self): pass
        class Thread:
            def __init__(self, **kwargs): pass
            def start(self): state['workers_started'] = True
        svc = types.SimpleNamespace(stop=threading.Event(), eng=types.SimpleNamespace(adaptive=True),
                                    acquire=lambda:None, assess_loop=lambda:None)
        httpd = types.SimpleNamespace(serve_forever=lambda:None, shutdown=lambda:None)
        # Exit the main loop immediately. This test never opens USB or a socket.
        with patch.dict(sys.modules, {'tinyrad_dev':types.SimpleNamespace(TinyRadDevice=Device)}), \
             patch.object(service, 'RadarService', return_value=svc), \
             patch.object(service, 'make_http', return_value=httpd), \
             patch.object(service.os, 'makedirs'), \
             patch.object(service.threading, 'Thread', Thread), \
             patch.object(service.time, 'sleep', side_effect=[KeyboardInterrupt(),None]), \
             patch('signal.signal'):
            service.main(['--contract-file','none'])
        self.assertFalse(state['raced'])
