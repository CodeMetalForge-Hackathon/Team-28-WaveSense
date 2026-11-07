# Wi-Fi CSI results, 2026-11-07

All results were **observed on November 7, 2026** on the hardware in [README.md](README.md). No raw captures were saved or committed.

**Labels used below:**

| Label | Meaning |
|---|---|
| **Measured** | Observed directly on our hardware or software, with the command shown |
| **App output** | A value that RuView computed or classified. It's a real reading of the program's output, but it says nothing about whether the classification is correct |
| **Inferred** | Derived by arithmetic or by reading source code. Not measured |
| **Unverified** | Reported or assumed, but not checked |

## Conditions

| | |
|---|---|
| Transmitter | Panda PAU0F on `wlan1`, NetworkManager AP `ForgeCSI`, 2.4 GHz, channel 6 requested, `10.42.0.1/24` |
| Node | One ESP32-C5HR8 rev v1.0, RuView firmware 0.8.17 (C5, 4 MB layout), provisioned to `10.42.0.1:5005` |
| Receiver | `ruvnet/wifi-densepose`, digest `sha256:73dd3f62…` (see [SETUP §10.4](SETUP.md#104-the-container-as-it-was-configured)) |
| Not recorded | Node placement, distance, orientation, room layout, and who was present during each run |

Since placement and occupancy weren't recorded, **none of the numbers below can be tied to a motion or occupancy condition.**

## R1. Hardware identification

| Result | Value | Label |
|---|---|---|
| Chip | ESP32-C5, revision v1.0 (`esptool`). It had been described as an ESP32-S3 | **Measured** |
| Flash / PSRAM | 8 MB / 8 MB | **Measured** |
| USB IDs | ESP32 `303a:1001` on `/dev/ttyACM0`; Panda `0e8d:7961` | **Measured** |
| Panda driver, AP mode | `mt7921u`; `iw phy` lists `AP` (re-read 17:16 EST) | **Measured** |
| AI accelerator | Installed, not exercised | **Unverified** |

## R2. Firmware and backup integrity

| Result | Value | Label |
|---|---|---|
| Release archive SHA-256 | `6050ea1c…b28b49bb`, which matches the release's `SHA256SUMS.txt` | **Measured** |
| Bundle images | 4 images + `version.txt`: every `sha256sum -c` OK (re-run 17:16 EST) | **Measured** |
| Flash plan | 4/4 integrity checks passed. Offsets `0x2000` bootloader, `0x8000` partition table, `0xF000` OTA data, `0x20000` app | **Measured** |
| Full flash backup | `esp32c5-original-8mb.bin`, 8,388,608 bytes | **Measured** |
| Backup readback | First 64 KiB of a second read is identical (`cmp`) | **Measured** |
| Full-image readback | Not done; impossible now that the board is reflashed | **Unverified** |

## R3. Serial console

`node harness/ruview/bin/cli.js monitor --port /dev/ttyACM0 --seconds 60`

| Condition | Result | Label |
|---|---|---|
| Before provisioning | No CSI measurements reported | **Measured** |
| After provisioning, result | `OK` | **Measured** |
| After provisioning, CSI log lines | **27** (lines matching `CSI cb #N`) | **Measured** |
| After provisioning, console lines | **240** | **Measured** |
| Serial baud | 115200 | **Measured** |

## R4. UDP receiver (host capture)

`node harness/ruview/bin/cli.js esp32 --udp-port 5005 --seconds 60`

| Metric | Value | Label |
|---|---|---|
| Received packets | **2,166** | **Measured** |
| Successfully decoded packets | **2,166** (100% of those received) | **Measured** |
| Active CSI nodes | **1** | **Measured** |
| CSI rate | **30.9 Hz** | **Measured** |
| Reported packet loss | **33.4%** (estimate from sequence gaps) | **Measured estimate**; see the inferred section below |
| RSSI | **-65 dBm** | **Measured** |
| CSI shape | **1 × 53** (antennas × subcarriers) | **Measured** |

100% decoding of *received* packets doesn't mean zero loss. The 33.4% loss figure is a separate estimate.

## R5. Live node API snapshot

`GET /api/v1/nodes` with a bearer token, from the running `ruview-live` container:

| Field | Value | Label | What it means |
|---|---|---|---|
| `csi_status` | `active` | **Measured** | The server accepted a CSI frame from this node within the last 5 s |
| `csi_last_seen_ms` | `90` | **Measured** | Age of the newest CSI frame |
| `csi_sequence` | `58462` | **Measured** | The node's firmware sequence counter |
| `rssi_dbm` | `-81` | **Measured** | The single latest RSSI sample, not an average |
| `position_configured` | `false` | **Measured** (config fact) | No `--node-positions` entry, so any position shown is a placeholder |
| `motion_level` | `present_moving` | **App output** | RuView's classification; not checked against ground truth |
| `person_count` | `1` | **App output** | RuView's estimate; not checked against ground truth |
| `person_count_valid` | `true` | **App output / unverified** | A flag from the **node's own firmware** (edge vitals packet). It is not external validation |

## R6. Dashboard

| Result | Label |
|---|---|
| After port forwarding and entering the token, the dashboard authenticated and displayed **live, changing** sensor data | **Measured** (qualitative) |
| The visualization looked **noisy** | Observation (qualitative, not quantified) |

## R7. Container configuration

Read with `docker inspect` (formatted, so the token wasn't printed): UDP `10.42.0.1:5005`, HTTP `127.0.0.1:3000`, WebSocket `127.0.0.1:3001`, `CSI_SOURCE=esp32`, `RUVIEW_UDP_ALLOW=172.17.0.1/32`, token set, restart policy `no`. The last run went from 12:41 to 12:50 EST and ended `Exited (255)`. **Measured**

## Inferred numbers

These are derived, not measured. Check them before relying on them.

| Inference | Basis |
|---|---|
| **A large share of the 33.4% "loss" is probably firmware rate limiting, not radio loss.** | v0.8.17 `csi_collector.c` numbers every CSI callback, then sends at most one UDP frame per 20 ms. Unsent frames leave sequence gaps |
| Implied CSI callback rate ≈ **46 Hz** | 30.9 Hz ÷ (1 − 0.334) |
| The serial log suggests ≈ **40–45 callbacks/s** | The firmware logs callbacks #1–3, then every 100th: 27 lines in 60 s means roughly 2,400–2,700 callbacks. This came from a different run than R4 |
| 2,166 packets in 60 s (≈36/s) is more than 30.9 Hz × 60 s (≈1,854) | The harness counts all packet kinds (CSI plus vitals, feature and sync packets) as "received", but the rate covers CSI frames only |

## Not established

None of the following has been shown, so none of it should be claimed:

- accuracy of motion, presence or person-count classification;
- false-positive rate in an empty area;
- any calibration or baseline for this room;
- node position or localization;
- through-wall sensing, imaging or pose inference;
- vital signs;
- the real radio or network packet-loss rate, as opposed to rate-limit gaps;
- long-run stability (the only container run with recorded start and stop times lasted about 9 minutes);
- the identity of the second ESP32 board, or its firmware compatibility.

**Conclusion:** live RF transport works, from hotspot to node to UDP to RuView to the authenticated dashboard. Everything about what the signal *means* is still open. See [NEXT_STEPS.md](NEXT_STEPS.md).
