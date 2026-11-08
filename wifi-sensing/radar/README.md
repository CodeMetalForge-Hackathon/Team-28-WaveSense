# radar: still and moving person CANDIDATES from a 24 GHz FMCW radar (TinyRad)

**Status: experimental, measured on one recording and one live session (2026-11-07). Not validated for life-safety use.**

The Wi-Fi CSI link cannot tell a still person from an empty chair (see `../xray/README.md`). An FMCW radar can: it measures distance, and a breathing chest moves a few millimetres, which swings the 24 GHz phase by several radians. This adds an Analog Devices **EV-TINYRAD24G** (24.00–24.25 GHz, 2 TX / 4 RX, USB).

| It can | It cannot |
|---|---|
| flag a person near the radar, **including someone sitting still**, with their **range** (0.6 m resolution) | see through concrete, masonry or rubble (24 GHz is blocked) |
| say "moving" or "still, breathing-like", with a breathing-rate estimate | count people, or tell two people at the same range apart (no angle processing yet) |
| ignore static things (walls, furniture, a chair), as they are removed | tell a person from a fan, a pet or anything else that moves |

A candidate is for a human to verify. **No candidate is NOT evidence that nobody is there.**

## Files

| File | Role |
|---|---|
| `tinyrad_dev.py` | headless device access through ADI's own [TinyRadTool](https://github.com/analogdevicesinc/TinyRadTool) code (not vendored), USB reset and drain on start, clean stop |
| `radar_engine.py` | range FFT, static-clutter removal, per-range micro-motion and motion energy against an empty-room calibration, Doppler-based "moving" label, phase-based breathing rate |
| `radar_service.py` | acquisition thread, engine, `.radrec` recorder, HTTP on 127.0.0.1:3101 (status, calibrate, record) |
| `radar_eval.py` | replay a `.radrec` and **measure** it by clock-time segments |

**Phone app (WaveSense):** the service also writes the app's radar contract (schema 1) every second to `/srv/team28/runtime/radar/status.json`, replaced atomically: `candidate` (still), `motion` (moving), `clear`, or `not_judging` (calibrating, uncalibrated, warming up), with one track at the strongest range and `bearing_deg: null` (no angle yet). The app reads it when started with `--radar-status-file /srv/team28/runtime/radar/status.json`. Its default path is inside the shared Team-28 checkout, which is not git-ignored and holds uncommitted work, so the file deliberately goes outside it. `--contract-file none` turns it off.

The Wi-Fi receiver (port 3100) polls the radar status. Its `zone` combines both: a radar candidate is a CANDIDATE (with range); Wi-Fi movement alone is a WEAK candidate; neither means no candidate.

## Setup (once)

```bash
git clone https://github.com/analogdevicesinc/TinyRadTool ~/tinyrad/TinyRadTool      # BSD-style licence
python3 -m venv --system-site-packages ~/tinyrad/venv && ~/tinyrad/venv/bin/pip install pyusb==1.2.1
# USB access for the plugdev group (root, once), then unplug and replug the radar:
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="064b", ATTR{idProduct}=="7823", MODE="0660", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/99-tinyrad.rules
sudo udevadm control --reload && sudo udevadm trigger
```

**Power: put the radar on a powered USB hub.** On 2026-11-08 at 03:28, starting the radar while the C1 lidar and the camera were running tripped the Pi's USB over-current protection (kernel: `over-current change` on four ports). All three sensors dropped at once, although the Pi's own 5 V stayed at 5.03 V with no throttling. The 03:25 radar drop right after the robot app restarted (lidar spin-up) was most likely the same. The Pi's USB ports share one current limit; the radar, the lidar's motor and the camera together exceed it at start-up. The service now reconnects by itself, backing off 3/10/30/60 s.

## Adaptive background (default since 2026-11-08)

No calibration step. Per range bin, the service learns the scene's typical flicker in its first 15 s (it judges ~30 s after start), then keeps tracking it: the level falls within ~20 s when the scene gets quieter, follows rises over ~1 min while nobody is detected there, and only creeps up (15 min time constant) where a candidate is. A candidate is micro-motion above **6×** or motion above **10×** the tracked level, about 3× the empty-room p99 (measured: p99 ≈ 2× and 3× the median).

| Replay of the 2026-11-07 recordings, no calibration at all | Result |
|---|---|
| empty room after learning | 0/31 s candidate |
| seated still ~1 m | 57/60 s, first at +3 s; cleared 19 s after leaving |
| seated still behind a wooden door | 95/95 s; quiet empty afterwards cleared after 20 s |

Same as the manual calibration on these recordings (measured, in-sample: the parameters were set from these recordings). Synthetic tests: still person kept for 10 min, empty-room noise tripling over 3 min gives no candidate.

- **People in front at power-on are not learned away.** A learning-free "wobble" rule runs from the first second: a bin whose micro-motion power is > 30× this radar's own noise floor (`device_floor.json`, measured once from quiet periods of two scenes) **and** > 3× its own static echo power wobbles like a body, not like a wall, a door or noise. Bins it flags are excluded from the learning, and it stays on as a second detector. Replayed cold starts (measured, in-sample): person seated in the open at power-on detected from +17 s (as soon as the 10 s window fills), then continuously; person behind the door at power-on from +29 s, then continuously; empty starts as before. Alone, the wobble rule caught 100% of seconds seated in the open but only 60% behind the door, so the adaptive layer still matters.
- The radar only sees in front of its antenna face. People behind or well beside the robot hardly register (not measured: check by walking behind it while watching the status).
- `device_floor.json` belongs to this radar unit and these settings (N=256, 24.00–24.25 GHz). For another unit, re-measure it from a quiet empty minute.
- `curl -X POST 127.0.0.1:3101/calibrate/start` relearns now (useful after moving the radar).
- A fan or other moving thing in view stays a candidate for 15–30 min; point the radar away from it.
- **Robot driving is not detected by the radar.** A person close to it disturbs the whole scene as much as motion does (scene coherence 0.29–0.71 measured with a person at ~1 m), so the "robot moving" signal has to come from the drive controller. Until then, judge only while the robot stands still.
- `--fixed-calibration` restores the manual calibration mode.

## Running it

```bash
cd ~/Team-28-wifi-xray/wifi-sensing/radar
setsid nohup ~/tinyrad/venv/bin/python -I radar_service.py > ~/missions/radar-service.log 2>&1 &
curl -s 127.0.0.1:3101/status.json
curl -X POST 127.0.0.1:3101/calibrate/start     # nobody in the radar's view within ~2 m, >= 60 s
curl -X POST 127.0.0.1:3101/calibrate/finish    # saves ~/.config/wifi-xray/radar1.json
```

Point the radar's antenna side at the seats you want to watch, 1–1.5 m away. Recalibrate after moving the radar or large furniture. Stop it with `kill -TERM <pid>`, which stops the transmitter. If the radar ever stops answering (USB timeout on start), unplug and replug it once.

## Results (2026-11-07, one room, one person)

| Data | Result | Label |
|---|---|---|
| Recording 23:22–23:25: calibration on 23:22–23:23 (nobody in view) | 0 of 45 s candidate in the calibration minute | measured, in-sample |
| 23:23–23:24: one person seated still at ~1 m | **candidate 57/60 s (95%), 100% after the first 10 s, first after 3 s, median range 0.9 m**; presence ~145× the empty level | measured |
| 23:24–23:25: nobody, right after the person left | candidate 18/60 s; all of it is the person leaving and the 10 s window emptying; afterwards presence 0.1–0.7× (threshold 3×) | measured, held out |
| **behind a wooden door**, 23:45:37–23:51:49 (`door-cmd`): 32 s live calibration with nobody behind the door; marks by the user's words "sit" 23:46:46, "empty" 23:48:21 and again 23:49:39 | **seated still behind the door: candidate 95/95 s**, median signal 2 600× the empty level, at ~1.5 m. Empty with people moving in the background: 77/77 s (they trigger it). Empty and quiet: cleared 21 s after the mark, decaying to 1.4× | measured, one run |
| breathing rate | reads ~9/min in **every** period, empty ones included: it locks onto slow drift at the lowest bin of its 20 s window | measured: **unreliable, do not show it as a vital sign**. The "breathing-like" label is unreliable for the same reason |
| frame rate | 250 raw frames/s, averaged to 25 Hz, no DMA errors | measured |

Results page: `results-2026-11-07.html` (also published as a private claude.ai artifact).

The 10 s presence window and the 5 s hysteresis were chosen after looking at the first recording (a 20 s window took ~35 s to clear). A fresh held-out live test is still to be done.
