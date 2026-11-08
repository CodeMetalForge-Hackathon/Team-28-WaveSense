# wifi-xray: near-zone presence candidates from ESP32 CSI

**Status: experimental. Validated only on synthetic data so far. No real-world accuracy has been measured yet** (see [Measuring it](#measuring-it)).

## Bottom line so far (2026-11-07, measured, one link)

- With correct decoding, this link **cannot reliably tell a person sitting still from an empty zone**. Furniture moved into the link (inferred: the chair) and slow drift change the channel as much as or more than a seated person, and a one-time calibration cannot separate them. "Candidate" therefore means "the channel changed": a person, a moved object, or drift.
- **Movement near the link is detectable.** Walking through the link was flagged in 78% of judged windows (the rest are the 3 s hysteresis at the start), and the empty minutes next to it gave 0% candidates. Over all empty segments, false candidates were about 15%: movement *near* the zone counts too. The motion threshold was tuned on these recordings, so a fresh test is still needed.
- Since 22:40 the engine only says CANDIDATE for movement or breathing-like evidence. A profile shift alone is reported as **CHANGED since calibration** (a person, a moved object or drift), never as a person.
- About a third of the frames the node captures come from **other transmitters** on the channel. The engine keeps only frames within 3 dB of the running RSSI mode, which is the node's own link.
- For still people (the first-responder case), use a dedicated sensor that measures breathing directly, such as mmWave radar. Single-link Wi-Fi CSI is not that sensor.

## What it is for, and what it is not

A single ESP32-C5 link (node ↔ Raspberry Pi hotspot) sees how the radio channel between the two changes. People near that link change it in two measurable ways:

- **Profile shift.** A body sitting near the link changes the time-averaged multipath "shape", even when perfectly still. This is the main signal for someone who cannot move.
- **Breathing-like periodicity.** Chest motion of a few millimetres modulates the channel at 0.12–0.6 Hz (7–36 per minute). It shows up when the person is still and close to the link.

Motion anywhere in radio range also shows up. That is exactly why a crowd confuses naive detectors.

The engine reports **`candidate_present` / `no_candidate` for the zone close to the link**, with its evidence. It never claims more than that.

| It can (once calibrated and measured) | It cannot |
|---|---|
| flag that something like a person is probably close to the link, including someone still | say **where** the person is (one link has no geometry) |
| flag breathing-like periodicity when the area is still | **count** people. Counting is off until trained and measured on labelled recordings |
| ignore background motion further away, to the extent the calibration captured it | see through rubble reliably. Metal, wet concrete and distance absorb or scatter 2.4 GHz |
| keep working with people moving around further away | tell a person from a pet, a fan or a swaying object |

**For first responders.** Treat a candidate as a reason to look, call out or bring a proper sensor. Treat the absence of a candidate as **no information**, never as "nobody is there". Rescue-grade through-debris detection uses dedicated radar (UWB/FMCW). The upgrade path is under [Next steps](#next-steps).

## How it fits together

```
ESP32-C5 node ──Wi-Fi CSI UDP──▶ wifi-xray front door ──same datagrams──▶ RuView (website, port 3000)
   (ForgeCSI hotspot)         <hotspot IP>:5005               127.0.0.1:5006 (start_ruview.sh --udp-loopback 5006)
                                    │
                                    ├─ NearZoneEngine per node ─▶ http://127.0.0.1:3100 (status.json + page)
                                    └─ optional recorder ─▶ ~/missions/csi/*.csirec (stays on the Pi, never in git)
```

| File | Role |
|---|---|
| `csi_packet.py` | parse RuView ESP32 CSI datagrams (magic `0xC5110001`; layout from the server's `parse_esp32_frame`) |
| `dsp.py` | resampling, per-frame normalisation (cancels automatic gain steps), Hampel filter, motion energy, breathing-like periodicity, profile distance |
| `engine.py` | calibration, thresholds (multiples of the calibration's own 95th percentiles, with floors), hysteresis, RSSI gate for frames from distant transmitters (no measurable effect on the 2026-11-07 recordings), the CANDIDATE report |
| `receiver.py` | UDP front door: hotspot-subnet allowlist, unchanged forwarding to RuView, engine, recorder, loopback HTTP |
| `fusion.py` | zone summary across nodes: CANDIDATE only when two judging links agree; one link of several gives a WEAK candidate |
| `evaluate.py` | replay labelled recordings and **measure** false-candidate and detection rates |
| `../scripts/esp32_log.py` | read a node's serial console without resetting it; explains why it is not streaming |

It needs `python3-numpy`, which ships with Raspberry Pi OS. The shell tooling stays standard-library only.

## Running it

```bash
cd ~/Team-28-wifi-xray/wifi-sensing
# 1. RuView behind the front door (replaces a container that publishes on the hotspot address)
scripts/start_ruview.sh --udp-loopback 5006            # plan; add --start (and --replace-existing) to run
# 2. the front door
python3 -m xray.receiver --iface wlan1 --forward 127.0.0.1:5006
# 3. status: forward port 3100 in VS Code and open http://localhost:3100
curl -s 127.0.0.1:3100/status.json
```

`status.json` holds `link` (counters, `stations` associated with the hotspot, `last_frame_age_s`, and `hint`: why no CSI is arriving, in plain words), `zone` (the fused summary, see `fusion.py`) and `nodes` (one engine report per ESP32).

### When no CSI arrives

1. `curl -s 127.0.0.1:3100/status.json` and read `link.hint`.
2. `python3 scripts/esp32_log.py` reads the node's console for 40 s **without resetting it**, and translates the Wi-Fi disconnect reason. It accepts only `/dev/ttyACM*` by default (`/dev/ttyUSB0` on this Pi is the robot lidar).
3. `reason=201` (NO_AP_FOUND) means the node hears no AP with its SSID on any channel. Check in this order: the SSID shows up on a phone next to the Pi (the hotspot really beacons); the node's antenna is fitted; the node is close to the Panda adapter; the provisioned SSID matches.

### Placement: the link must cross the zone you want to watch

One link senses mostly what is **on or near the line between its two antennas** (the ESP32's and the Panda adapter's). With both devices plugged in side by side, that line is a few centimetres long: a person seated 60 cm away was not detected (measured 2026-11-07, 0% of 164 windows). Put the ESP32 on its own USB power 1.5–3 m from the Pi, so the line runs through the seats you want to watch, at about table height and clear of large metal. Keep the node's RSSI above about -85 dBm (`status.json` `nodes.<n>.rssi_dbm`). Recalibrate after every move.

### Calibrate on site, with the crowd present

1. Clear the near zone: nobody within about 1.5 m of the ESP32 and the Panda adapter. People further away may keep moving. That is the point: the thresholds learn this environment.
2. `curl -X POST 127.0.0.1:3100/calibrate/start`. Wait **at least 90 s**: 30 s warm-up plus 60 s of windows.
3. `curl -X POST 127.0.0.1:3100/calibrate/finish`. This saves `~/.config/wifi-xray/node<N>.json`.

Recalibrate after moving the node, the Pi or large furniture, or when the crowd changes a lot.

## Measuring it

Only consenting people go in the near zone. Recordings stay on the Pi (`~/missions/csi/`) and are git-ignored. Record at least 3 minutes each. The running receiver records on request, so RuView forwarding never stops:

```bash
curl -X POST '127.0.0.1:3100/record/start?label=empty-crowd-A'    # ... wait 3+ minutes ...
curl -X POST 127.0.0.1:3100/record/stop
# repeat with labels empty-crowd-B, one-seated-A, two-seated-A, two-seated-crowd-B ...
cd ~/Team-28-wifi-xray/wifi-sensing
python3 -m xray.evaluate --calibrate ~/missions/csi/empty-crowd-A-*.csirec --save /tmp/cal.json
python3 -m xray.evaluate --cal /tmp/cal.json ~/missions/csi/empty-crowd-B-*.csirec ~/missions/csi/one-* ~/missions/csi/two-*
```

Report the summary line, false-candidate % (empty) and detection % (one / two), as **measured on these recordings**, with date, room, node placement and crowd level.

## Results

| Date | Data | False candidates (empty, crowd) | Detection (1 still person) | Status |
|---|---|---|---|---|
| 2026-11-07 | synthetic model (`tests/synth_csi.py`) | 0% | >80% (first within 20 s) | **unit test only, not evidence** |
| 2026-11-07 21:21 | venue, node 1. Live calibration 21:17:54–21:19:34 (99 windows), scored on a **separate** empty recording `empty-crowd-A` 21:21:25–21:24:41 (196 s, 7,406 datagrams) | **0.0%** of 164 judged windows (median profile 0.93 vs threshold 4.0; breathing SNR 13.2 vs 35.9) | not measured yet | **measured, one recording only.** Crowd level and node placement not recorded (unverified). Says nothing yet about detection |
| 2026-11-07 21:27 | `one-seated-A` 21:27:14–21:30:31 (196 s): one consenting person seated **about 60 cm beside** the devices, with the ESP32 and the Panda adapter **side by side** on the Pi | n/a | **0.0%** of 164 judged windows (median profile 1.0 vs threshold 4.0) | **measured: not detected.** Inferred cause: the link is a few cm long, so the person was never in its path (see Placement). Offline, 10 s profiles were shifted (median 1.77 vs empty 1.01), but drift over the 6 min between recordings was not ruled out |
| 2026-11-07 22:26–22:28 | same placement and calibration. Motion test on a clock schedule: `mot-empty-1`, `mot-walk-1` (walking back and forth across the link), `mot-empty-2`, `mot-walk-2`, 30 s each. Scored with the own-link gate and motion evidence (`MOTION_K` 2.5, tuned on these recordings) | **0%** of 18 windows candidate (100% "changed": the lasting step) | walking: **77.8%** of 18 windows (first candidate 2 s after judging starts) | **measured, threshold tuned in-sample.** Earlier empty segments `alt-empty-1` and `alt-empty-3` gave 33% and 37% motion candidates (movement near the zone, inferred); all empty segments pooled: ~15% |
| 2026-11-07 22:09–22:23 | same placement, 12-bit decoder. Recalibrated live 22:09:38–22:11:19 (100 windows), then alternating 1 min segments: empty-1, one-1, empty-2, (one-2 discarded: the person was unsure whether to sit), empty-3, one-3, empty-4, one-4 | the live engine read **candidate in every segment after the first sit-down**, empty or not | not separable | **measured: a still, seated person is NOT detectable with this setup.** Profile distance to the calibration: empty-1 0.5, **one-1 5.0**, empty-2 4.6, empty-3 5.2, one-3 6.0, empty-4 5.7, one-4 5.5. The step at the first sit-down never reverted (likely the chair or something moved and stayed: inferred). Drift-robust check: one-3 is 2.6 / 0.9 from its empty neighbours, which are 2.0 from each other. Short-term variability shows no seated/empty difference either |
| 2026-11-07 21:50–22:07 | antenna fitted, ESP32 on a power bank across the seat, **12-bit decoder** (see below). Calibrated offline on `empty-ant-A` (21:50:57–21:54:13); `one-seated-ant-A` 21:59:21–22:02:37; `empty-ant-B` 22:04:20–22:07:36 | `empty-ant-B`: **candidate in nearly every window** (profile 8.5–13.8 vs threshold 4.0, live) | `one-seated-ant-A`: 98.8% of 164 windows (scored) | **measured, and the detection is NOT valid evidence:** the empty recording after the seated one was further from the calibration (10 s profile distance 35–40) than the seated one (15–29), and RSSI fell -39 → -42 → -44 dBm. Something changed and stayed changed (object moved, device nudged, or warm-up drift; not yet known). The 97% re-score of `one-seated-A` (old placement) has the same unresolved confound |

## The ESP32-C5 payload is 12-bit, not int8 (found 2026-11-07 21:57)

The C5 sends its CSI as little-endian 16-bit words holding **signed 12-bit** values, in (I, Q) word pairs: a 106-byte payload is 26 complex subcarriers. Read as int8 pairs, as the documented ESP32 layout and RuView's parser do, it is noise. Measured on an empty-room recording: the frame-to-frame correlation of the amplitude profile was **0.01 read as int8 and 0.998 read as 12-bit**, and every word fits in 12 bits. `csi_packet.py` now auto-detects the format. **Everything this node produced before the fix, here and in RuView, was computed on noise**, including RuView's motion and person-count outputs. The real/imaginary order within a pair is not verified.

## Link facts (2026-11-07 21:14, node 1, measured)

| Fact | Value | Label |
|---|---|---|
| CSI frames delivered to the front door | **27.9 /s** over 60 s (1,676 frames); other node datagrams 4.7 /s | measured |
| Forwarded to RuView | 1,961 of 1,961 datagrams, 0 errors, 0 dropped by the allowlist. RuView `/health`: `source_state=live`, `udp_datagrams` rising | measured |
| Subcarriers per frame | **52** (1 antenna) | measured |
| RSSI in the CSI frames | **-69 to -71 dBm** (engine mean over its buffer) | measured |
| Hotspot's view of the node | signal -61 dBm, tx MCS 3, rx MCS 2, 0 tx failures | measured (`iw station dump`) |
| Firmware sequence rate | 36 /s (RuView `check_stream.sh`); the firmware numbers every capture but sends at most one frame per 20 ms, so this exceeds the delivered rate | app output |
| Node position relative to the hotspot | not recorded yet | unverified |

## Known issues (2026-11-07)

- **Fixed at 21:08: the hotspot was silent on air.** NetworkManager and wpa_supplicant reported ForgeCSI enabled, but neither a phone next to the Pi nor the ESP32 could see it (reason=201 on every retry). A driver reload did not help. Re-plugging the Panda adapter from a USB 3 port (5000M) into a **USB 2.0 port** (480M) did: the AP came on air at 21:08:18 and the ESP32 joined one second later (measured). Whether the port or the re-plug was the cure is not known. USB 3 radiating noise at 2.4 GHz is a documented effect (inferred). **Keep the adapter on USB 2.0.**
- **Node 1 has no antenna fitted** (empty U.FL socket, user check 21:44). Measured: -61 to -74 dBm between it and the Panda adapter a few cm apart, and no link at 2–3 m. Fit a 2.4 GHz U.FL (IPEX MHF1) antenna before placing the node for sensing; without one, the usable range is well under 2 m.
- `iw dev wlan1 info` reports `txpower 3.00 dBm` whatever is set. On mt7921 this is a known reporting bug, and the figure is not the real power.
- **Pressing BOOT on the ESP32-C5 board leaves it in download mode** (silent console, no Wi-Fi). esptool's RTS hard reset did not get it out over USB-Serial/JTAG; `esptool --before no-reset --after watchdog-reset chip-id` did (measured, 21:13). To restart the node by hand, press **RST**, not BOOT.
- The Pi lost power repeatedly (7 times since 18:00, last at about 20:54) with a 5 A wall supply and no prior voltage sag. The supply or its connection is suspect.

## Next steps

1. Get real recordings and fill in the results table. Tune the thresholds from `evaluate.py`, not by eye.
2. **Second ESP32 as node 2.** Two links seen from different angles cut false candidates from background motion, and the near zone becomes where the two links agree. The fusion logic is in place (`fusion.py`, tested on synthetic reports only); it needs the second node provisioned with `--node-id 2`.
3. Optional trained 0/1/2 occupancy classifier, only reported once its measured accuracy is shown next to it.
4. Show the status in the WaveSense phone UI (its Wi-Fi tile), with the same CANDIDATE wording. A patch is ready for the app owner: `integrations/wavesense-wifi-tile.patch` (also in `/srv/team28/patches/`), with notes in `integrations/wavesense-wifi-tile.txt`. Not yet applied or tested on a phone.
