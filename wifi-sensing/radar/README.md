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

The Wi-Fi receiver (port 3100) polls the radar status. Its `zone` combines both: a radar candidate is a CANDIDATE (with range); Wi-Fi movement alone is a WEAK candidate; neither means no candidate.

## Setup (once)

```bash
git clone https://github.com/analogdevicesinc/TinyRadTool ~/tinyrad/TinyRadTool      # BSD-style licence
python3 -m venv --system-site-packages ~/tinyrad/venv && ~/tinyrad/venv/bin/pip install pyusb==1.2.1
# USB access for the plugdev group (root, once), then unplug and replug the radar:
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="064b", ATTR{idProduct}=="7823", MODE="0660", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/99-tinyrad.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Plug the radar into a USB 2.0 port; a powered hub is better, since this Pi has had power cuts.

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
