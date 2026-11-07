#!/usr/bin/env python3
"""Wheels-up tests for robot/chassis.py. EVERY command here can spin the motors:
confirm the wheels are off the ground first.

  motion forward|back|left|right|ccw|cw [--duty 30] [--time 0.5]
  watchdog   one command, then silence: when does the watchdog stop it?
  sigterm    drive continuously, SIGTERM ourselves after 0.4 s
  sigkill    a child process drives; we SIGKILL it after 0.4 s; the deadman must stop
"""
import argparse, os, signal, subprocess, sys, textwrap, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from robot.chassis import Chassis  # noqa: E402

MOTIONS = {"forward": (1, 0, 0), "back": (-1, 0, 0), "left": (0, 1, 0), "right": (0, -1, 0),
           "ccw": (0, 0, 1), "cw": (0, 0, -1)}
MAX_TIME = 1.0


def show(ch, t0):
    for t, e in ch.events:
        if t >= t0 - 0.001:
            print(f"  +{1000 * (t - t0):6.1f} ms  {e}")
    st = ch.status()
    print(f"  battery {st['battery_v']} V, watchdog trips {st['watchdog_trips']}, error {st['error']}")


def motion(a):
    vx, vy, wz = MOTIONS[a.name]
    dur = min(a.time, MAX_TIME)
    with Chassis(duty_cap=a.duty) as ch:
        t0 = time.monotonic()
        while time.monotonic() - t0 < dur:
            duties = ch.set_velocity(vx, vy, wz)
            time.sleep(0.05)
        ch.stop("test end")
        print(f"{a.name}: board duties {duties} for {dur:.2f} s")
        show(ch, t0)


def watchdog(a):
    with Chassis(duty_cap=a.duty) as ch:
        t0 = time.monotonic()
        ch.set_velocity(1, 0, 0)          # forward, once, then nothing
        time.sleep(1.0)
        print("watchdog test: one forward command at +0 ms, then silence")
        show(ch, t0)


def sigterm(a):
    ch = Chassis(duty_cap=a.duty)
    t0 = time.monotonic()
    threading.Timer(0.4, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    try:
        while time.monotonic() - t0 < MAX_TIME:
            ch.set_velocity(1, 0, 0)
            time.sleep(0.05)
        print("SIGTERM never arrived?!")
    except SystemExit as e:
        print(f"sigterm test: SystemExit({e.code}) raised in the drive loop")
    finally:
        show(ch, t0)
        ch.close()


def sigkill(a):
    code = textwrap.dedent(f"""
        import sys, time; sys.path.insert(0, {ROOT!r})
        from robot.chassis import Chassis
        ch = Chassis(duty_cap={a.duty})
        print("driving", flush=True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < {MAX_TIME}:
            ch.set_velocity(1, 0, 0); time.sleep(0.05)
        ch.close()
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, cwd=ROOT)
    assert p.stdout.readline().strip() == b"driving"
    time.sleep(0.4)
    print(f"[test {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}] SIGKILL -> driver pid {p.pid}",
          flush=True)
    p.kill()
    p.wait()
    time.sleep(0.5)    # deadman's STOP line (stderr, inherited) should appear above


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["motion", "watchdog", "sigterm", "sigkill"])
    ap.add_argument("name", nargs="?", choices=sorted(MOTIONS))
    ap.add_argument("--duty", type=float, default=30)
    ap.add_argument("--time", type=float, default=0.5)
    a = ap.parse_args()
    if a.duty > 50:
        ap.error("duty cap is 50")
    if a.cmd == "motion" and not a.name:
        ap.error("motion needs a name")
    dict(motion=motion, watchdog=watchdog, sigterm=sigterm, sigkill=sigkill)[a.cmd](a)


if __name__ == "__main__":
    main()
