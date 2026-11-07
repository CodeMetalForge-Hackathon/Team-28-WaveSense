"""Chassis driver tests without hardware (fake serial port, pty for the deadman).

  .venv/bin/python -m unittest tests.test_chassis -v
"""
import os, signal, struct, subprocess, sys, tempfile, textwrap, threading, time, tty, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from robot.board import STOP_FRAME, FrameParser, frame, motor_frame  # noqa: E402
from robot.chassis import Chassis, mix, wheel_duty  # noqa: E402
from robot.nudge import FORWARD_SIGN  # noqa: E402


class FakeSerial:
    def __init__(self, rx=b""):
        self.writes, self.rx, self.lock = [], bytearray(rx), threading.Lock()

    def write(self, data):
        with self.lock:
            self.writes.append((time.monotonic(), bytes(data)))

    def flush(self):
        pass

    def read(self, n):
        time.sleep(0.01)
        with self.lock:
            out, self.rx[:] = bytes(self.rx[:n]), self.rx[n:]
        return out

    def close(self):
        pass

    def duties(self):
        """Decoded motor frames: list of (t, {motor: duty})."""
        out = []
        for t, data in self.writes:
            for func, d in FrameParser().feed(data):
                if func == 3:
                    n = d[1]
                    out.append((t, {d[2 + 5 * i] + 1: struct.unpack_from("<f", d, 3 + 5 * i)[0] for i in range(n)}))
        return out


def make(**kw):
    ser = FakeSerial(kw.pop("rx", b""))
    kw.setdefault("deadman", False)
    kw.setdefault("min_duty", 0)
    kw.setdefault("handle_signals", False)
    return Chassis(ser=ser, **kw), ser


class Mixing(unittest.TestCase):
    def test_patterns(self):
        self.assertEqual(mix(1, 0, 0), {1: 1, 2: 1, 3: 1, 4: 1})
        self.assertEqual(mix(0, 1, 0), {1: -1, 2: 1, 3: 1, 4: -1})      # strafe left
        self.assertEqual(mix(0, 0, 1), {1: -1, 2: 1, 3: -1, 4: 1})      # rotate CCW
        w = mix(1, 1, 1)
        self.assertAlmostEqual(max(abs(v) for v in w.values()), 1.0)  # normalised

    def test_duty_cap_hard_limit(self):
        ch, _ = make(duty_cap=80)
        self.assertEqual(ch.duty_cap, 50.0)
        d = ch.set_velocity(5, 0, 0)                  # input clipped to 1
        self.assertEqual({m: abs(v) for m, v in d.items()}, {1: 50, 2: 50, 3: 50, 4: 50})
        self.assertEqual({m: (v > 0) - (v < 0) for m, v in d.items()}, FORWARD_SIGN)
        ch.close()
        f = motor_frame({1: 999.0})                   # last-line clamp in the frame builder
        self.assertEqual(struct.unpack_from("<f", f, 7)[0], 50.0)

    def test_min_duty_mapping(self):
        self.assertEqual(wheel_duty(0.005, 35, 40), 0.0)        # tiny command -> stop
        self.assertEqual(wheel_duty(0.5, 35, 40), 37.5)
        self.assertEqual(wheel_duty(-1, 35, 40), -40.0)
        self.assertEqual(wheel_duty(0.2, 35, 30), 30.0)         # cap below min: cap wins
        ch, _ = make(duty_cap=40, min_duty=35)
        d = ch.set_velocity(0.1, 0, 0)
        self.assertEqual({m: abs(v) for m, v in d.items()}, {1: 35.5, 2: 35.5, 3: 35.5, 4: 35.5})
        ch.close()

    def test_nan_is_zero(self):
        ch, _ = make()
        self.assertEqual(ch.set_velocity(float("nan"), 0, 0), {1: 0, 2: 0, 3: 0, 4: 0})
        ch.close()


class Timing(unittest.TestCase):
    def test_slew_then_watchdog_within_300ms(self):
        ch, ser = make(duty_cap=30, slew=250)
        t_cmd = time.monotonic()
        ch.set_velocity(1, 0, 0)                      # one command, then silence
        time.sleep(0.6)
        ch.close()
        moving = [(t, d) for t, d in ser.duties() if t > t_cmd and any(d.values())]
        self.assertTrue(moving, "no motion frames sent")
        self.assertLess(abs(moving[0][1][2]), 30, "first frame should be slew-limited, not full duty")
        stops = [t for t, d in ser.duties() if t > moving[-1][0] and not any(d.values())]
        dt = stops[0] - t_cmd
        self.assertLessEqual(dt, 0.300, f"watchdog stop after {dt * 1000:.0f} ms")
        self.assertEqual(ch.watchdog_trips, 1)

    def test_stop_is_immediate(self):
        ch, ser = make(duty_cap=30)
        for _ in range(10):
            ch.set_velocity(1, 0, 0); time.sleep(0.02)
        t = time.monotonic(); ch.stop()
        first_zero = min(tt for tt, d in ser.duties() if tt >= t and not any(d.values()))
        self.assertLess(first_zero - t, 0.01)
        ch.close()

    def test_exception_in_with_block_stops(self):
        ser = None
        try:
            with Chassis(ser=FakeSerial(), deadman=False, handle_signals=False) as ch:
                ser = ch._ser
                ch.set_velocity(1, 0, 0); time.sleep(0.1)
                raise ValueError("boom")
        except ValueError:
            pass
        self.assertFalse(any(ser.duties()[-1][1].values()), "last frame must be STOP")

    def test_sigterm_stops(self):
        ch = Chassis(ser=FakeSerial(), deadman=False, handle_signals=True)
        ch.set_velocity(1, 0, 0); time.sleep(0.1)
        t = time.monotonic()
        with self.assertRaises(SystemExit):
            os.kill(os.getpid(), signal.SIGTERM); time.sleep(0.5)
        zero = [tt for tt, d in ch._ser.duties() if tt >= t and not any(d.values())]
        self.assertTrue(zero and zero[0] - t < 0.05)
        ch.close()


class Battery(unittest.TestCase):
    def test_battery_frames(self):
        rx = b"\x00\x13" + frame(0, bytes([0x04]) + struct.pack("<H", 7171)) + b"\xAA\x55\x00"
        ch, _ = make(rx=rx)
        time.sleep(0.1)
        self.assertAlmostEqual(ch.status()["battery_v"], 7.171)
        ch.close()


class Deadman(unittest.TestCase):
    def pty(self):
        master, slave = os.openpty()
        tty.setraw(master)
        os.set_blocking(master, False)
        return master, os.ttyname(slave)

    def read_stops(self, master, wait):
        end, buf = time.monotonic() + wait, b""
        while time.monotonic() < end:
            try:
                buf += os.read(master, 4096)
            except BlockingIOError:
                time.sleep(0.005)
            if STOP_FRAME in buf:
                return time.monotonic()
        return None

    def test_sigkill_parent(self):
        master, port = self.pty()
        code = textwrap.dedent(f"""
            import sys, time; sys.path.insert(0, {ROOT!r})
            from robot.chassis import Chassis
            from tests.test_chassis import FakeSerial
            ch = Chassis(ser=FakeSerial(), port={port!r}, deadman=True, handle_signals=False)
            print("ready", flush=True)
            while True:
                ch.set_velocity(1, 0, 0); time.sleep(0.05)
        """)
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, cwd=ROOT)
        self.assertEqual(p.stdout.readline().strip(), b"ready")
        time.sleep(0.3)
        t = time.monotonic(); p.kill(); p.wait()
        got = self.read_stops(master, 1.0)
        self.assertIsNotNone(got, "deadman did not send STOP after SIGKILL")
        self.assertLess(got - t, 0.2)

    def test_heartbeat_loss(self):
        master, port = self.pty()
        p = subprocess.Popen([sys.executable, "-m", "robot.deadman", str(os.getpid()), port],
                             cwd=ROOT, stdin=subprocess.PIPE)
        time.sleep(0.3)
        for _ in range(10):
            p.stdin.write(b"."); p.stdin.flush(); time.sleep(0.02)
        self.assertIsNone(self.read_stops(master, 0.05))       # no stop while beating
        t = time.monotonic()                                    # beats stop now
        got = self.read_stops(master, 1.0)
        self.assertIsNotNone(got, "no STOP on heartbeat loss")
        self.assertLess(got - t, 0.40)
        p.stdin.close(); p.wait(2)


if __name__ == "__main__":
    unittest.main()
