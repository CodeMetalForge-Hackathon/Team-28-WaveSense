# Wi-Fi CSI sensing (RuView): handoff

- **Date:** 2026-11-07
- **Host:** Raspberry Pi 5 (16 GB), Raspberry Pi OS 64-bit (Debian 13 "trixie"), kernel 6.18.50+rpt-rpi-2712
- **Status:** **live RF transport works end to end. Sensing accuracy is unknown.** Work is paused while the robot hardware is assembled.
- **Scope:** only the Wi-Fi CSI subsystem. Motors, cameras, LiDAR, radar and UWB are out of scope here.

## What this is

Wi-Fi **channel state information (CSI)** describes how a radio channel changes the signal between a transmitter and a receiver, per subcarrier. People moving nearby change that channel. The question for the search-and-rescue robot is whether those changes can serve as **one more piece of experimental evidence** of motion or occupancy.

The setup has three parts:

1. The Pi runs a dedicated 2.4 GHz hotspot on a USB Wi-Fi adapter.
2. An ESP32-C5 node joins that hotspot, computes CSI from the frames it receives, and streams the CSI to the Pi over UDP.
3. [RuView](https://github.com/ruvnet/RuView) runs in Docker on the Pi. It parses the stream, classifies it and serves a dashboard.

**What this is not.** It is not through-wall imaging. It does not identify people, locate them, estimate pose or measure vital signs. Upstream RuView advertises some of those features, but **none of them has been validated here.** See [RESULTS.md](RESULTS.md) for what was actually measured.

## Architecture

```
  Raspberry Pi 5                                             ESP32-C5 CSI node
 ┌──────────────────────────────────────────────────┐      ┌──────────────────────────┐
 │ Panda PAU0F (wlan1, mt7921u)                     │ 2.4  │ RuView firmware 0.8.17   │
 │ NetworkManager AP "ForgeCSI", ch 6, 10.42.0.1/24 │◄GHz─►│ joins ForgeCSI, computes │
 │                                                  │ Wi-Fi│ CSI per received frame,  │
 │   UDP 10.42.0.1:5005  ◄──────────────────────────┼──────┤ sends UDP to :5005       │
 │        │ docker-proxy (source becomes 172.17.0.1)│      └──────────────────────────┘
 │        ▼                                         │
 │ Docker container "ruview-live"                   │
 │   CSI_SOURCE=esp32                               │
 │   RUVIEW_UDP_ALLOW=<bridge gateway>/32           │
 │   HTTP 127.0.0.1:3000 · WebSocket 127.0.0.1:3001 │
 │        │                                         │
 │ wlan0 (built-in) ── venue Wi-Fi ── Tailscale ────┼──► laptop: VS Code Remote SSH,
 └──────────────────────────────────────────────────┘    ports 3000/3001 forwarded,
                                                         http://localhost:3000/ui/index.html
```

`wlan0` is the Pi's only uplink and carries SSH over Tailscale. **Nothing in the CSI setup touches `wlan0`.**

## Hardware

| Part | Details | Role | Status (2026-11-07) |
|---|---|---|---|
| CanaKit Raspberry Pi 5, 16 GB | Raspberry Pi OS 64-bit, headless over VS Code Remote SSH + Tailscale. Login `adritforge@Adrit` | Hotspot host, Docker, dashboard | Working |
| 26 TOPS AI accelerator | Installed on the Pi | Not used by this subsystem | **Not verified** |
| Panda Wireless PAU0F AXE3000 | USB `0e8d:7961` (MediaTek), driver `mt7921u`, interface `wlan1`, AP mode listed by `iw phy` | Hotspot transmitter | Working |
| Built-in Wi-Fi (`wlan0`) | Joined `BASE-X Guest` during testing | Uplink and Tailscale | Leave it alone |
| ESP32-C5HR8, rev v1.0 | 8 MB flash, 8 MB PSRAM, USB `303a:1001`, `/dev/ttyACM0` | CSI node | Flashed, provisioned, streamed |
| Second team ESP32 | Chip **not identified** | None yet | **Do not flash** until `esptool chip-id` identifies it |

> The first board was described as an **ESP32-S3**, but `esptool` identified it as an **ESP32-C5**. Never flash it with S3 firmware.

## Measured vs not established

| Item | Status |
|---|---|
| Panda hotspot `ForgeCSI` on `wlan1` (2.4 GHz, ch 6, `10.42.0.1/24`) | **Measured** |
| ESP32-C5 identified, RuView C5 firmware flashed, network provisioned | **Measured** |
| Real CSI callbacks on the node's serial console (27 CSI log lines in 60 s) | **Measured** |
| UDP: 2,166 packets received, 2,166 decoded, 1 node, 30.9 Hz CSI, 1×53 CSI shape | **Measured** |
| Reported packet loss 33.4% | **Measured estimate.** It probably includes intentional firmware rate limiting ([RESULTS.md](RESULTS.md#inferred-numbers)) |
| Authenticated Docker dashboard showing live, changing data | **Measured** (qualitative) |
| RuView's `motion_level: present_moving`, `person_count: 1` | **Application output, not validated** |
| Detection accuracy, false-positive rate, calibration, node position | **Not established** |
| Through-wall sensing, pose, localization, vital signs | **Not established** |

## Documents

| File | Contents |
|---|---|
| [SETUP.md](SETUP.md) | Rebuild the pipeline from scratch: Pi packages, adapter, hotspot, ESP32-C5 ID, firmware checksums, backup, flash plan, optional guarded flashing, provisioning, Docker, port forwarding, dashboard |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Every problem we actually hit, with its cause and fix |
| [RESULTS.md](RESULTS.md) | Exact measurements from 2026-11-07, each labelled measured, inferred or unverified |
| [NEXT_STEPS.md](NEXT_STEPS.md) | Prioritized research plan for the next owner |
| [`wifi-sensing/`](../../wifi-sensing/README.md) | Read-only diagnostics, stream check, guarded start helper, offline tests |

## Quick start for the next owner

```bash
bash wifi-sensing/tests/run_tests.sh       # offline tests, no hardware or Docker needed
sudo -v                                    # Docker needs root; the scripts only use 'sudo -n' and never prompt
wifi-sensing/scripts/diagnose.sh           # read-only: what's plugged in, up and listening
wifi-sensing/scripts/check_stream.sh       # read-only: are real CSI frames arriving right now?
wifi-sensing/scripts/start_ruview.sh       # prints the plan only; add --start to launch
```

## State left behind on 2026-11-07

Read with `diagnose.sh` and `docker inspect` at about 17:15 EST:

- Container `ruview-live` **exited with code 255** at 12:50 EST. It was not restarted, since its restart policy is `no`. It still holds its old API token, so `docker start ruview-live` would bring it back with the same settings.
- The Panda adapter is plugged in and `wlan1` exists, but the hotspot `ForgeCSI` is **not active**. Its profile has `autoconnect no`.
- The ESP32-C5 is not on the Pi's USB.
- There is no `~/.config/ruview/api_token` file yet. Create one before using `start_ruview.sh` ([SETUP.md §10](SETUP.md#10-docker-live-receiver)).
- The firmware bundle in `~/ruview-firmware-c5/c5-bundle` still matches its checksums.
- `~/esp-backups/*.bin` are mode 0664 (readable by other local users). Run `chmod 600 ~/esp-backups/*.bin`.

## Ground rules

- **Consent.** Run sensing experiments only with informed, consenting participants. Tell everyone in radio range that RF sensing is running, and stop if anyone objects.
- **No surprise flashing.** The working ESP32-C5 does not need reflashing. Never reflash it automatically, and never with ESP32-S3 firmware.
- **Never commit** flash backups, ESP32 provisioning state, API tokens, Wi-Fi passwords, or captures that involve people. `wifi-sensing/.gitignore` covers the usual paths.
- **Rotate the hotspot password** before wider use. It was visible in an earlier screenshot ([SETUP.md §9.3](SETUP.md#93-rotating-the-hotspot-password)).
- **Report honestly.** A healthy API or a `person_count` from the classifier is not evidence of accurate human detection.
