#!/usr/bin/env python3
"""Run RGB + thermal + LiDAR modules together and measure them.

Waits until all three report ok (max --warmup s), then measures for --seconds:
average fps from frame counts, crashes (dead threads), reconnects, and total Pi
CPU % from `top` (5 s samples). Prints one status line per second.

  .venv/bin/python tools/sensor_soak.py --seconds 60
  .venv/bin/python tools/sensor_soak.py --seconds 180 --log ~/missions/test/soak.log   # unplug test
"""
import argparse, logging, os, re, signal, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sensors.camera import Camera  # noqa: E402
from sensors.lidar import Lidar  # noqa: E402
from sensors.thermal import Thermal  # noqa: E402


def top_sampler(seconds, out):
    """Average busy % across all cores from `top -b -d 5`, first (since-boot) sample dropped."""
    n = int(seconds // 5) + 1
    p = subprocess.run(["top", "-b", "-d", "5", "-n", str(n)], capture_output=True, text=True)
    busy = [100.0 - float(m.group(1)) for m in re.finditer(r"%Cpu\(s\):.*?([\d.]+)\s+id", p.stdout)][1:]
    out.extend(busy)


def line(t, sensors):
    parts = [f"{t:6.1f}s"]
    for s in sensors:
        sn = s.snapshot()
        age = "-" if sn["age_ms"] is None else f"{sn['age_ms']}ms"
        extra = ""
        d = sn["data"]
        if s.name == "thermal" and d and d["temp"]:
            extra = f" max~{d['temp']['max_c']:.1f}C(uncal.)"
        if s.name == "lidar" and d is not None:
            extra = f" pts={len(d)}"
        parts.append(f"{s.name}:{sn['status']} {sn['fps']:4.1f}fps age={age} rc={sn['reconnects']}{extra}")
    return " | ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--warmup", type=float, default=20)
    ap.add_argument("--log", help="also append status lines to this file")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logf = open(os.path.expanduser(a.log), "a", buffering=1) if a.log else None

    def emit(msg):
        print(msg, flush=True)
        if logf:
            logf.write(time.strftime("%H:%M:%S ") + msg + "\n")

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())

    sensors = [Camera().start(), Thermal().start(), Lidar().start()]
    t0 = time.monotonic()
    while not stop.is_set() and time.monotonic() - t0 < a.warmup:
        if all(s.snapshot()["status"] == "ok" for s in sensors):
            break
        stop.wait(0.2)
    emit(f"warm-up {time.monotonic() - t0:.1f} s: " + line(0, sensors))
    th = sensors[1]
    emit(f"thermal backend: {th.backend} ({th.opencv_error or 'OpenCV ok'}); "
         f"camera fourcc: {sensors[0].fourcc}; lidar health: {sensors[2].health}")

    cpu = []
    sampler = threading.Thread(target=top_sampler, args=(a.seconds, cpu), daemon=True)
    f0 = {s.name: s.frames for s in sensors}
    pcpu0, w0 = time.process_time(), time.monotonic()
    sampler.start()
    crashes = set()
    while not stop.is_set() and time.monotonic() - w0 < a.seconds:
        stop.wait(1.0)
        for s in sensors:
            if not s.alive():
                crashes.add(s.name)
        emit(line(time.monotonic() - w0, sensors))
    wall = time.monotonic() - w0
    pcpu = time.process_time() - pcpu0
    sampler.join(timeout=10)

    emit("=== summary ===")
    for s in sensors:
        n = s.frames - f0[s.name]
        emit(f"{s.name:8s} frames={n:5d} avg={n / wall:5.2f}/s reconnects={s.reconnects} errors={s.errors}"
             f" last_error={s.last_error or '-'}")
    emit(f"lidar resyncs={sensors[2].resyncs}  thermal resyncs={th.resyncs}")
    emit(f"crashed threads: {sorted(crashes) or 'none'}")
    emit(f"this process: {100 * pcpu / wall:.0f}% of one core")
    if cpu:
        emit(f"Pi total CPU (top, {len(cpu)} x 5 s, avg of 4 cores): mean {sum(cpu) / len(cpu):.1f}%"
             f", max {max(cpu):.1f}%")
    for s in sensors:
        s.stop()
    emit("stopped (lidar port close continues in background)")


if __name__ == "__main__":
    main()
