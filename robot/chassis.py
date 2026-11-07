"""Mecanum chassis driver for the Hiwonder MasterPi (Pi 5 UART expansion board).

    from robot.chassis import Chassis
    with Chassis(duty_cap=30) as ch:          # stops on exit, exception, SIGTERM, SIGHUP, Ctrl-C
        ch.set_velocity(vx=0.5, vy=0, wz=0)  # must be repeated: 300 ms watchdog
        ...

Inputs are normalised -1..1:  vx forward+, vy left+, wz counter-clockwise+.
Wheel duty: mix(vx, vy, wz) gives -1..1 per wheel; a non-zero command maps into
min_duty..duty_cap (min_duty = motor start threshold from config/robot.json,
duty_cap <= MAX_DUTY = 50), then the per-motor FORWARD_SIGN from robot/MOTORS.md. A 50 Hz control thread applies
slew-rate limiting and the watchdog; stop() and watchdog stops bypass the slew
and write STOP immediately. A separate deadman process (robot/deadman.py)
stops the wheels if this process dies or the control thread stalls.

Battery voltage, reported by the board ~9 Hz, is in status()["battery_v"].
Known limit: if the Pi itself loses power or hard-crashes, the board keeps the
last duty. Test wheels-up, keep duty low, keep the board switch reachable.
"""
import atexit, json, math, os, signal, subprocess, sys, threading, time

from robot.board import FORWARD_SIGN, MAX_DUTY, MOTORS, PORT, STOP_FRAME, FrameParser, motor_frame, open_port

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mix(vx, vy, wz):
    """Mecanum (X roller layout) mixing -> wheel commands in -1..1 for motors 1..4 (FL, FR, RL, RR)."""
    fl, fr, rl, rr = vx - vy - wz, vx + vy + wz, vx + vy - wz, vx - vy + wz
    m = max(1.0, abs(fl), abs(fr), abs(rl), abs(rr))
    return {1: fl / m, 2: fr / m, 3: rl / m, 4: rr / m}


def config_min_duty():
    try:
        with open(os.path.join(ROOT, "config", "robot.json")) as f:
            return float(json.load(f).get("chassis", {}).get("min_duty", 0.0))
    except (OSError, ValueError):
        return 0.0


def wheel_duty(w, min_duty, cap):
    """Wheel command -1..1 -> duty. Non-zero commands start at min_duty (motor start threshold)."""
    if abs(w) < 0.01:
        return 0.0
    lo = min(min_duty, cap)
    return math.copysign(lo + (cap - lo) * abs(w), w)


def _clip(v):
    v = float(v)
    return 0.0 if math.isnan(v) else max(-1.0, min(1.0, v))


class Chassis:
    def __init__(self, port=PORT, duty_cap=40.0, min_duty=None, slew=250.0, watchdog_s=0.3, rate_hz=50,
                 deadman=True, handle_signals=True, ser=None):
        self.duty_cap = max(0.0, min(float(duty_cap), MAX_DUTY))
        self.min_duty = config_min_duty() if min_duty is None else max(0.0, float(min_duty))
        self.slew = float(slew)                # duty units per second
        self.watchdog_s = float(watchdog_s)
        self.dt = 1.0 / rate_hz
        self.port = port
        self.events = []                        # (monotonic t, text) for tests / logs
        self.watchdog_trips = 0
        self.frames_sent = 0
        self.battery_v = None
        self._battery_t = None
        self.error = None
        self._lock = threading.Lock()           # targets / state
        self._wlock = threading.Lock()          # serial writes
        self._target = {m: 0.0 for m in MOTORS}
        self._current = {m: 0.0 for m in MOTORS}
        self._last_cmd = 0.0
        self._last_sent = 0.0
        self._closed = False
        self._ser = ser or open_port(port)
        self._send(STOP_FRAME, "init stop")
        self._deadman = self._start_deadman() if deadman else None
        self._ctl = threading.Thread(target=self._control_loop, name="chassis-control", daemon=True)
        self._rx = threading.Thread(target=self._reader, name="chassis-rx", daemon=True)
        self._ctl.start()
        self._rx.start()
        atexit.register(self.close)
        self._old_handlers = {}
        if handle_signals and threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                self._old_handlers[sig] = signal.signal(sig, self._on_signal)

    # ---- public API ---------------------------------------------------------
    def set_velocity(self, vx, vy, wz):
        """Command a velocity; returns the target board duties. Repeat at >= 4 Hz (watchdog)."""
        if self._closed or self.error:
            raise RuntimeError(f"chassis unavailable: {self.error or 'closed'}")
        w = mix(_clip(vx), _clip(vy), _clip(wz))
        target = {m: wheel_duty(w[m], self.min_duty, self.duty_cap) for m in MOTORS}
        with self._lock:
            self._target = target
            self._last_cmd = time.monotonic()
        return {m: FORWARD_SIGN[m] * target[m] for m in MOTORS}

    def stop(self, reason="stop()"):
        """Immediate stop: zero targets, skip slew, write STOP twice."""
        with self._lock:
            self._target = {m: 0.0 for m in MOTORS}
            self._current = {m: 0.0 for m in MOTORS}
        self._send(STOP_FRAME, reason, repeat=2)

    def status(self):
        with self._lock:
            cur, tgt, last = dict(self._current), dict(self._target), self._last_cmd
        age = None if self._battery_t is None else time.monotonic() - self._battery_t
        return dict(duty_cap=self.duty_cap, min_duty=self.min_duty, current={m: FORWARD_SIGN[m] * cur[m] for m in MOTORS},
                    target={m: FORWARD_SIGN[m] * tgt[m] for m in MOTORS},
                    moving=any(abs(v) > 1e-6 for v in cur.values()),
                    cmd_age_s=None if not last else time.monotonic() - last,
                    watchdog_trips=self.watchdog_trips, frames_sent=self.frames_sent,
                    battery_v=self.battery_v, battery_age_s=age,
                    deadman_alive=self._deadman is not None and self._deadman.poll() is None,
                    error=self.error)

    def close(self):
        if self._closed:
            return
        self.stop("close()")
        self._closed = True
        self._ctl.join(0.5)
        if self._deadman is not None:
            try:
                self._deadman.stdin.close()     # deadman sends its own STOP and exits
                self._deadman.wait(1.0)
            except Exception:
                self._deadman.kill()
        try:
            self._ser.close()
        except Exception:
            pass
        for sig, old in self._old_handlers.items():
            try:
                signal.signal(sig, old)
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- internals -----------------------------------------------------------
    def _log(self, text):
        self.events.append((time.monotonic(), text))
        if len(self.events) > 2000:
            del self.events[:1000]

    def _send(self, data, why=None, repeat=1):
        got = self._wlock.acquire(timeout=0.05)  # a stop must never wait long
        try:
            for _ in range(repeat):
                self._ser.write(data)
            self._ser.flush()
            self.frames_sent += repeat
            self._last_sent = time.monotonic()
            if why:
                self._log(why)
        except Exception as e:
            self.error = f"serial write failed: {e}"
            self._log(self.error)
        finally:
            if got:
                self._wlock.release()

    def _on_signal(self, signum, _frame):
        self.stop(f"signal {signal.Signals(signum).name}")
        raise SystemExit(128 + signum)

    def _start_deadman(self):
        p = subprocess.Popen([sys.executable, "-m", "robot.deadman", str(os.getpid()), self.port],
                             cwd=ROOT, stdin=subprocess.PIPE, start_new_session=True)
        self._log(f"deadman pid {p.pid}")
        return p

    def _heartbeat(self):
        if self._deadman is None:
            return
        try:
            self._deadman.stdin.write(b".")
            self._deadman.stdin.flush()
        except Exception as e:
            self._log(f"deadman heartbeat failed: {e}")
            self._deadman = None

    def _control_loop(self):
        nxt = time.monotonic()
        prev = nxt
        try:
            while not self._closed:
                now = time.monotonic()
                self._heartbeat()
                with self._lock:
                    target, last = dict(self._target), self._last_cmd
                    moving = any(abs(v) > 1e-6 for v in target.values()) or \
                        any(abs(v) > 1e-6 for v in self._current.values())
                # trip two ticks early (260-280 ms at 50 Hz) so scheduling jitter can't push the
                # stop past watchdog_s (one tick early measured 300.02 ms once)
                if moving and now - last >= self.watchdog_s - 2 * self.dt:
                    self.watchdog_trips += 1
                    self.stop(f"watchdog: no command for {1000 * (now - last):.0f} ms")
                else:
                    step = self.slew * (now - prev)
                    with self._lock:
                        changed = False
                        for m in MOTORS:
                            c, t = self._current[m], target[m]
                            n = c + max(-step, min(step, t - c))
                            changed |= n != c
                            self._current[m] = n
                        cur = dict(self._current)
                    keepalive = 0.1 if moving else 0.5
                    if changed or now - self._last_sent >= keepalive:
                        self._send(motor_frame({m: FORWARD_SIGN[m] * cur[m] for m in MOTORS}))
                prev = now
                nxt += self.dt
                time.sleep(max(0.0, nxt - time.monotonic()))
        except Exception as e:
            self.error = f"control loop crashed: {e}"
            self._log(self.error)
            self.stop("control loop crash")

    def _reader(self):
        parser = FrameParser()
        while not self._closed:
            try:
                chunk = self._ser.read(256)
            except Exception:
                if self._closed:
                    return
                time.sleep(0.1)
                continue
            for func, data in parser.feed(chunk):
                if func == 0 and len(data) >= 3 and data[0] == 0x04:
                    self.battery_v = (data[1] | data[2] << 8) / 1000.0
                    self._battery_t = time.monotonic()
