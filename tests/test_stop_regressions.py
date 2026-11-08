"""Regression tests for stale writes after stopping and immediate neutral/reverse braking."""
import threading,time,unittest
from robot.board import STOP_FRAME,FrameParser
from tests.test_chassis import make

def moving_frame(data):
 return data != STOP_FRAME and any(func==3 for func,_ in FrameParser().feed(data))

class StopRegressions(unittest.TestCase):
 def test_old_movement_must_not_be_written_after_stop(self):
  ch,ser=make(duty_cap=30,slew=10000)
  waiting,gate=threading.Event(),threading.Event();original=ch._send;paused=[False]
  def intercept(data,*args,**kwargs):
   if moving_frame(data) and not paused[0]:
    paused[0]=True;waiting.set();gate.wait(1)
   return original(data,*args,**kwargs)
  ch._send=intercept
  try:
   ch.set_velocity(1,0,0);self.assertTrue(waiting.wait(1),'movement frame not produced')
   ch.stop('regression stop');stopped=time.monotonic();gate.set();time.sleep(.06)
   bad=[d for t,d in ser.duties() if t>=stopped and any(d.values())]
   self.assertFalse(bad,'stale movement was transmitted AFTER stop')
  finally:gate.set();ch.close()
 def test_neutral_is_immediate_not_slew_limited(self):
  ch,ser=make(duty_cap=30,slew=250)
  try:
   for _ in range(8):ch.set_velocity(1,0,0);time.sleep(.02)
   t=time.monotonic();ch.set_velocity(0,0,0);time.sleep(.015)
   zero=[tt for tt,d in ser.duties() if tt>=t and not any(d.values())]
   self.assertTrue(zero,'neutral did not send an immediate zero frame')
   self.assertLess(zero[0]-t,.01)
  finally:ch.close()
 def test_reverse_sends_zero_before_reverse(self):
  ch,ser=make(duty_cap=30,slew=250)
  try:
   for _ in range(8):ch.set_velocity(1,0,0);time.sleep(.02)
   t=time.monotonic();ch.set_velocity(-1,0,0);time.sleep(.015)
   after=[(tt,d) for tt,d in ser.duties() if tt>=t]
   self.assertTrue(after and not any(after[0][1].values()),'reverse must start with zero')
   self.assertLess(after[0][0]-t,.01)
   time.sleep(.05)
   self.assertFalse(any(any(d.values()) for tt,d in ser.duties() if t+.005<tt<t+.07),'reverse brake interval was skipped')
  finally:ch.close()
 def test_reverse_resumes_only_after_brake_then_neutral_cancels(self):
  ch,ser=make(duty_cap=30,slew=10000)
  try:
   ch.set_velocity(1,0,0);time.sleep(.06)
   t=time.monotonic();ch.set_velocity(-1,0,0)
   for _ in range(9):time.sleep(.02);ch.set_velocity(-1,0,0)
   reverse=[tt for tt,d in ser.duties() if tt>=t and d[2]<0]
   self.assertTrue(reverse,'reverse never resumed after braking')
   self.assertGreaterEqual(reverse[0]-t,ch.reversal_brake_s-.005)
   ch.set_velocity(1,0,0);ch.set_velocity(0,0,0);t=time.monotonic();time.sleep(.16)
   self.assertFalse(any(any(d.values()) for tt,d in ser.duties() if tt>=t),'neutral allowed queued reverse to resume')
  finally:ch.close()
 def test_serial_lock_timeout_never_writes_unlocked(self):
  ch,ser=make()
  ch._wlock.acquire()
  try:
   n=len(ser.writes);result=ch._send(STOP_FRAME)
   self.assertFalse(result)
   self.assertEqual(len(ser.writes),n,'serial write occurred without owning writer lock')
  finally:ch._wlock.release();ch.close()
if __name__=='__main__':unittest.main()
