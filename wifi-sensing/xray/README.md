# wifi-xray: near-zone presence candidates from ESP32 CSI

**Status: experimental. Validated only on synthetic data so far. No real-world accuracy has been measured yet** (see [Measuring it](#measuring-it)).

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
| `engine.py` | calibration, thresholds (multiples of the calibration's own 95th percentiles, with floors), hysteresis, the CANDIDATE report |
| `receiver.py` | UDP front door: hotspot-subnet allowlist, unchanged forwarding to RuView, engine, recorder, loopback HTTP |
| `evaluate.py` | replay labelled recordings and **measure** false-candidate and detection rates |

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

### Calibrate on site, with the crowd present

1. Clear the near zone: nobody within about 1.5 m of the ESP32 and the Panda adapter. People further away may keep moving. That is the point: the thresholds learn this environment.
2. `curl -X POST 127.0.0.1:3100/calibrate/start`. Wait **at least 90 s**: 30 s warm-up plus 60 s of windows.
3. `curl -X POST 127.0.0.1:3100/calibrate/finish`. This saves `~/.config/wifi-xray/node<N>.json`.

Recalibrate after moving the node, the Pi or large furniture, or when the crowd changes a lot.

## Measuring it

Only consenting people go in the near zone. Recordings stay on the Pi and are git-ignored. Record at least 3 minutes each:

```bash
python3 -m xray.receiver --iface wlan1 --forward 127.0.0.1:5006 --record ~/missions/csi --label empty-crowd-A
# repeat with --label empty-crowd-B, one-seated-A, two-seated-A, two-seated-crowd-B ...
python3 -m xray.evaluate --calibrate ~/missions/csi/empty-crowd-A-*.csirec --save /tmp/cal.json
python3 -m xray.evaluate --cal /tmp/cal.json ~/missions/csi/empty-crowd-B-*.csirec ~/missions/csi/one-* ~/missions/csi/two-*
```

Report the summary line, false-candidate % (empty) and detection % (one / two), as **measured on these recordings**, with date, room, node placement and crowd level.

## Results

| Date | Data | False candidates (empty, crowd) | Detection (1 still person) | Status |
|---|---|---|---|---|
| 2026-11-07 | synthetic model (`tests/synth_csi.py`) | 0% | >80% (first within 20 s) | **unit test only, not evidence** |
| (pending) | real recordings at the venue | n/a | n/a | needs the ESP32 streaming (see below) |

## Known issues (2026-11-07)

- The hotspot adapter (mt7921u) comes up at **3 dBm transmit power** after every boot, and the ESP32 then logs `reason=201` (no AP found). Fix per boot: `sudo iw dev wlan1 set txpower fixed 2000`. A persistent NetworkManager dispatcher hook is still to be added (needs root).
- The Pi lost power repeatedly (power-on resets, `rsts=0x1000`) with a 5 A wall supply and no prior voltage sag. The supply or its connection is suspect.

## Next steps

1. Get real recordings and fill in the results table. Tune the thresholds from `evaluate.py`, not by eye.
2. **Second ESP32 as node 2.** Two links seen from different angles cut false candidates from background motion, and the near zone becomes where the two links agree.
3. Optional trained 0/1/2 occupancy classifier, only reported once its measured accuracy is shown next to it.
4. Show the status in the WaveSense phone UI (its Wi-Fi tile), with the same CANDIDATE wording.
