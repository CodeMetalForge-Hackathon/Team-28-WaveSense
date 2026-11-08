#!/usr/bin/env python3
"""Read an ESP32 CSI node's serial console WITHOUT resetting or reconfiguring it,
and summarise why it is (not) streaming.

    python3 wifi-sensing/scripts/esp32_log.py                    # /dev/ttyACM0, 40 s
    python3 wifi-sensing/scripts/esp32_log.py --seconds 70 --lines 40
    python3 wifi-sensing/scripts/esp32_log.py --file saved.log   # parse a saved log

Safety:
- Opens the port read-only (O_RDONLY|O_NONBLOCK|O_NOCTTY), sets raw mode with
  HUPCL cleared, and never issues DTR/RTS ioctls. Opening any tty still raises
  DTR/RTS once (kernel behaviour); on the ESP32-C5 USB-Serial/JTAG port
  (/dev/ttyACM*) that was observed NOT to reset the chip (2026-11-07: node
  uptime continuous across reads). Boards with a USB-UART auto-reset circuit
  may behave differently, so only /dev/ttyACM* is accepted unless --any-port.
  /dev/ttyUSB0 on this Pi is the robot's RPLIDAR, not an ESP32.
- Never writes to the port. Masks MAC addresses and password/key/token fields.
- The summary is printed BEFORE the port is closed: closing a cdc-acm port can
  block for ~30 s in the kernel (tty_wait_until_sent).

Standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import select
import sys
import termios
import time

# ESP-IDF wifi_err_reason_t (esp_wifi_types.h), with what each means for a CSI node.
REASONS = {
    2: ("AUTH_EXPIRE", "authentication timed out: weak signal or a busy AP"),
    3: ("AUTH_LEAVE", "the AP deauthenticated the node"),
    4: ("ASSOC_EXPIRE", "association timed out / inactivity"),
    8: ("ASSOC_LEAVE", "the node left (normally a deliberate disconnect)"),
    15: ("4WAY_HANDSHAKE_TIMEOUT", "WPA handshake failed: usually a WRONG PASSWORD"),
    200: ("BEACON_TIMEOUT", "lost the AP's beacons: weak signal, interference or the AP went away"),
    201: ("NO_AP_FOUND", "no AP with the configured SSID heard on any channel scanned. Check: is the "
                         "hotspot really beaconing (look for the SSID on a phone next to the Pi)? Is the "
                         "node's antenna fitted and close enough? Does the provisioned SSID match exactly?"),
    202: ("AUTH_FAIL", "authentication rejected: wrong password or security mismatch"),
    203: ("ASSOC_FAIL", "association rejected by the AP"),
    204: ("HANDSHAKE_TIMEOUT", "WPA handshake failed: usually a WRONG PASSWORD"),
    205: ("CONNECTION_FAIL", "connection failed"),
    210: ("NO_AP_FOUND_W_COMPATIBLE_SECURITY", "the SSID exists but its security mode is incompatible"),
    211: ("NO_AP_FOUND_IN_AUTHMODE_THRESHOLD", "the SSID exists but is weaker than the node's auth threshold"),
    212: ("NO_AP_FOUND_IN_RSSI_THRESHOLD", "the SSID exists but is below the node's RSSI threshold"),
}

MAC = re.compile(r"(?i)\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
SECRET = re.compile(r"(?i)\b(password|passwd|pass|psk|pwd|key|token|secret)\b(\s*[=:]\s*)\S+")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
DISC = re.compile(r"WiFi disconnected, reason=(\d+) rssi=(-?\d+)")
ATTEMPT = re.compile(r"Reconnecting in (\d+) ms \(attempt (\d+)\)")
CSI_CB = re.compile(r"CSI cb #(\d+): len=(\d+) rssi=(-?\d+) ch=(\d+)")
UPTIME = re.compile(r"^[IWE] \((\d+)\)")
NOISE = re.compile(r"Haven.t to connect|sendto failed|medium tick|feature_state emit|rv_mesh_send|c6_espnow")
INTERESTING = re.compile(r"(?i)wifi|reason=|got ip|ssid|channel|csi cb|reconnect|rst:|boot|panic|guru|"
                         r"watchdog|brownout|error|fail")


def mask(text: str) -> str:
    text = ANSI.sub("", text)
    text = MAC.sub("XX:XX:XX:XX:XX:XX", text)
    return SECRET.sub(lambda m: m.group(1) + m.group(2) + "***", text)


def summarise(text: str) -> dict:
    """Facts parsed from a console log (already masked or not: only numbers are kept)."""
    s: dict = dict(disconnects=0, reasons={}, last_reason=None, last_attempt=None, got_ip=False,
                   csi_callbacks=0, csi_channels=set(), csi_rssi=[], uptime_ms=None, sendto_failed=0)
    for line in ANSI.sub("", text).splitlines():
        if m := UPTIME.match(line):
            s["uptime_ms"] = int(m.group(1))
        if m := DISC.search(line):
            r = int(m.group(1))
            s["disconnects"] += 1
            s["reasons"][r] = s["reasons"].get(r, 0) + 1
            s["last_reason"] = r
        elif m := ATTEMPT.search(line):
            s["last_attempt"] = int(m.group(2))
        elif m := CSI_CB.search(line):
            s["csi_callbacks"] += 1
            s["csi_rssi"].append(int(m.group(3)))
            s["csi_channels"].add(int(m.group(4)))
        elif "Got IP" in line:
            s["got_ip"] = True
            s["last_reason"] = None                     # a later success supersedes earlier failures
        elif "sendto failed" in line:
            s["sendto_failed"] += 1
    return s


def report(s: dict) -> list[str]:
    out = []
    if s["uptime_ms"] is not None:
        out.append(f"node uptime: {s['uptime_ms'] / 1000:.0f} s (from the log timestamps)")
    if s["got_ip"]:
        out.append("Wi-Fi: CONNECTED (Got IP seen in this window)")
    elif s["disconnects"]:
        r = s["last_reason"]
        name, why = REASONS.get(r, (f"reason {r}", "see esp_wifi_types.h"))
        att = f", reconnect attempt {s['last_attempt']}" if s["last_attempt"] else ""
        out.append(f"Wi-Fi: NOT CONNECTED. Last reason={r} {name}{att}")
        out.append(f"  meaning: {why}")
        if len(s["reasons"]) > 1:
            out.append("  all reasons in window: " + ", ".join(f"{k}x{v}" for k, v in sorted(s["reasons"].items())))
    else:
        out.append("Wi-Fi: no connect/disconnect event in this window (read longer: the node retries every ~30 s)")
    if s["csi_callbacks"]:
        rs = s["csi_rssi"]
        out.append(f"CSI capture: running on channel(s) {sorted(s['csi_channels'])}, logged RSSI "
                   f"{min(rs)}..{max(rs)} dBm over {len(rs)} log lines")
        if not s["got_ip"] and s["disconnects"]:
            out.append("  (a node that never associated falls back to its default CSI channel, so this channel "
                       "says nothing about the hotspot's channel)")
        if max(rs) <= -85:
            out.append("  every logged frame is very weak (<= -85 dBm): suspect the node's antenna (missing "
                       "U.FL antenna on a -1U module?), its distance, or shielding")
    if s["sendto_failed"]:
        out.append(f"UDP send failures: {s['sendto_failed']} (errno 118 = no route: not connected)")
    return out


def open_quiet(dev: str) -> int:
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    a = termios.tcgetattr(fd)
    a[0] = 0                                                       # iflag: raw
    a[1] = 0                                                       # oflag
    a[2] = (a[2] & ~(termios.HUPCL | termios.CSIZE | termios.PARENB)) | termios.CS8 | termios.CREAD | termios.CLOCAL
    a[3] = 0                                                       # lflag: no echo, no canonical
    a[4] = a[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def read_for(fd: int, seconds: float) -> str:
    buf, end = b"", time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        r, _, _ = select.select([fd], [], [], min(0.5, left))
        if r:
            try:
                buf += os.read(fd, 4096)
            except BlockingIOError:
                pass
    return buf.decode("utf-8", "replace")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dev", default="/dev/ttyACM0")
    ap.add_argument("--seconds", type=float, default=40.0, help="read time; the node retries Wi-Fi every ~30 s")
    ap.add_argument("--lines", type=int, default=25, help="masked log lines to show (0 = summary only)")
    ap.add_argument("--file", help="parse a saved log instead of reading the port")
    ap.add_argument("--any-port", action="store_true", help="allow ports other than /dev/ttyACM*")
    a = ap.parse_args(argv)

    fd = None
    if a.file:
        with open(a.file, errors="replace") as f:
            text = f.read()
    else:
        real = os.path.realpath(a.dev)
        if not os.path.basename(real).startswith("ttyACM") and not a.any_port:
            print(f"refusing {a.dev}: only ESP32 USB-Serial/JTAG ports (/dev/ttyACM*) by default; "
                  "/dev/ttyUSB0 here is the robot lidar. Use --any-port if you are sure.", file=sys.stderr)
            return 2
        fd = open_quiet(a.dev)
        print(f"reading {a.dev} for {a.seconds:.0f} s (read-only, no reset)...", flush=True)
        text = read_for(fd, a.seconds)

    print("\n".join(report(summarise(text))), flush=True)
    if a.lines:
        keep = [mask(l) for l in text.splitlines() if l.strip() and INTERESTING.search(l) and not NOISE.search(l)]
        if keep:
            print(f"--- last {min(a.lines, len(keep))} relevant lines (MACs and secrets masked) ---")
            print("\n".join(keep[-a.lines:]), flush=True)
    if fd is not None:
        os.close(fd)                       # may block ~30 s on cdc-acm; output is already out
    return 0


if __name__ == "__main__":
    sys.exit(main())
