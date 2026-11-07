#!/usr/bin/env python3
"""Read-only power logger, to find out why the Pi loses power.

Every --every seconds appends one line to --out and fsyncs it, so the last lines
survive a sudden power cut:
  time  EXT5V_V  throttled  PSU max_current(mA)  usb_disconnects  overcurrent_events  load1
Nothing is changed on the system.

  nohup .venv/bin/python tools/power_log.py --out ~/missions/power.log &
"""
import argparse, os, re, subprocess, time


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/missions/power.log"))
    ap.add_argument("--every", type=float, default=2.0)
    a = ap.parse_args()
    try:
        with open("/proc/device-tree/chosen/power/max_current", "rb") as f:
            max_ma = int.from_bytes(f.read(4), "big")
    except OSError:
        max_ma = -1
    fd = os.open(a.out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.write(fd, f"# start {time.strftime('%F %T')} boot max_current={max_ma} mA\n".encode())
    while True:
        v = re.search(r"EXT5V_V volt\(\d+\)=([\d.]+)", run("vcgencmd", "pmic_read_adc", "EXT5V_V"))
        thr = run("vcgencmd", "get_throttled").strip().split("=")[-1]
        dm = run("dmesg")
        line = (f"{time.strftime('%F %T')} ext5v={v.group(1) if v else '?'} throttled={thr} max_ma={max_ma} "
                f"usb_disc={dm.count('USB disconnect')} overcurrent={dm.count('over-current change')} "
                f"load1={os.getloadavg()[0]:.2f}\n")
        os.write(fd, line.encode())
        os.fsync(fd)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
