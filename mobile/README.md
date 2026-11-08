# WaveSense phone sensor console

Phone: **http://100.99.36.78:8080/** while connected to the robot's Tailscale network.
Refresh after updates. The current deployment enables driving through the sticks; wheels open only on fresh operator input. Release either stick to neutral; the control watchdog remains active.

## Current handoff: camera people + TinyRad evidence + 2D C1 mapping

The C1 is the default again. Camera is the main view; tap LiDAR or Radar to enlarge,
or use the arrow to float a preview. Absent thermal hardware is hidden. Camera
images fit completely in portrait and landscape; they are never cropped to fill.
Black bars preserve the original aspect ratio. The views have no frame borders.

- **Camera boxes:** white corner brackets labelled `CAM person`, from local
  OpenCV NanoDet inference. Only the person class is used. Boxes are burned onto
  the exact image that was analysed, avoiding overlays drifting over a newer frame.
  `Boxes` switches to annotated video; `Live` switches to the faster original video.
  Detection is asynchronous, has one shared camera reader, and never queues old
  images. Capture age includes processing time; stale detections clear after 1 s.
  If the detector is missing or fails, raw video remains available.
- **Radar evidence:** an amber `Radar candidate ~N m` cue appears separately on the
  camera view, with range-only rings in the radar pane. TinyRad's current processing
  measures range, **not azimuth**. It cannot locate a return at a camera pixel or on
  the LiDAR map, count people, confirm a human, or say which side of a wall they are
  on. Camera/radar concurrence explicitly says **association unknown**. No person
  outline is invented from a range-only return. No breathing/heart-rate display.
- **Movement:** radar evidence is suppressed while an accepted drive command is
  active and for 3 s after the last nonzero command. This reduces obvious motion
  contamination, but is not IMU-based motion compensation. Current tests never drive.
- **2D LiDAR:** `Map` shows the accumulated local SLAM estimate; `Scan` shows current
  obstacle returns. Initial scale fits the scan. Drag to pan, pinch or +/- to zoom,
  and use the target button to recenter/follow. The preview always follows the robot.
  Distances are from the LiDAR centre, not bumper clearance. L/R mounting remains
  unverified. Tracking loss dims retained map data. SLAM can drift and does not see
  above/below the scanner plane. Radar is never projected onto uncalibrated geometry.
- Optional Livox support is retained behind `--lidar-mode 3d`; historical setup and
  limitations are in [LIVOX-HANDOFF.md](LIVOX-HANDOFF.md). It is not the current source.

## Start without driving

```sh
cd /srv/team28/wavesense-mobile
./start-stationary.sh
```

Uses `/srv/team28/Team-28/.venv`, which provides OpenCV, numpy, pyserial and
BreezySLAM, plus the team's tested camera/LiDAR modules. RGB stable device:
`/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0` (resolved before OpenCV).
The C1 serial reader is shared with SLAM/proximity; do not start another sensor tool
while the console owns the devices. The historical `start.sh` enables driving;
**do not use it for this stationary integration**. No autostart service is installed.

The browser cannot force iPhone Safari chrome to disappear on plain HTTP. Use Add
to Home Screen for the installed app layout where supported. Full-screen button
uses the browser API when available. This is separate from camera image fitting.

## Radar service integration

Canonical radar project on the Pi: `/home/adritforge/Team-28-wifi-xray`, branch
`wifi-xray/near-zone-presence`, directory `wifi-sensing/radar`. It owns USB capture.
The app only reads its atomic export **`/srv/team28/runtime/radar/status.json`**.
Status must be <2 s old; an enumerated USB device alone never means detection.

```json
{"schema":1,"timestamp_ms":1794100000000,"online":true,"state":"candidate","tracks":[{"range_m":1.8,"bearing_deg":null}]}
```

Use current Unix milliseconds. States: candidate, motion, clear, not_judging.
`clear` means no candidate, not proof of an empty scene. Warmup/background learning
can take tens of seconds. A candidate may have non-human causes. Through-wall
performance and camera/radar spatial alignment have not been validated here.

[integration/radar](integration/radar) contains matching source snapshots and tests
for two recovery fixes: constrain ADI's SIGINT hook to main-thread construction;
retain 3/10/30/60 s backoff across successful opens followed by failed captures;
serialize the startup version query before acquisition begins.
A successful USB open is not recovery until frames arrive; sustained good capture
resets backoff. The app does not launch or overwrite this separate service.

## Actual validation on 2026-11-08

- 52 Python regression tests pass on the Pi, with mocked motors and USB in tests.
  JS checks pass for control release, map projection, cloud decoding and evidence.
- RGB measured ~12.5 fps; annotated output ~4.5 fps; inference ~90 ms on Pi 5.
  C1 measured ~10 sweeps/s. A 45 s stationary monitor had fresh camera, analysis
  and C1 data throughout. No wheel movement was commanded.
- An official OpenCV basketball test image produced two visible-person detections.
  This validates decoding, coordinate restoration and exact-frame rendering, not
  live detection accuracy. Live camera was aimed at the floor, so no live-person
  detection or paired/occluded-human experiment was completed.
- With RGB + C1 + radar, capture caused shared USB over-current and camera/C1
  re-enumeration. `lsusb -t` showed no external hub. After stopping the C1 scanner
  and its motor, a **45 s camera + radar comparison passed**: 45/45 camera and
  analysis samples fresh, camera ~22.6 fps, radar ~250 frames/s, zero radar
  reconnects. A startup USB command race was also removed before that comparison,
  so the test supports reduced concurrent load but does not isolate the sole cause.
  On the new two-source supply and powered hub, a 45 s simultaneous camera +
  radar + C1 + SLAM test passed: all 45 samples fresh, camera minimum 22.4 fps,
  C1 minimum 9.8 sweeps/s, radar advanced 11,045 frames, zero radar/C1 reconnects,
  unchanged camera reconnect count. Through-wall classification remains unvalidated.
- Thermal was absent. No thermal-live or Livox-live result is claimed.

Camera, radar and C1 may now capture together on the tested two-source supply.
Do not start a second reader for any device. Let radar background learn with an empty scene, then compare
empty, camera-visible person, and occluded person cases with the robot stationary.
Check both false positives and recovery. A range-only candidate must remain a range
cue, even when someone visible is at a similar distance. Angle processing plus
measured camera/radar extrinsics are required before a spatial overlay can be made.

```sh
/srv/team28/Team-28/.venv/bin/python -m unittest discover -s tests -v
node tests/test_control.js
node tests/test_lidar.js
node tests/test_cloud_codec.mjs
node tests/test_evidence.js
```

Graphics-only preview: `python3 tools/preview_evidence.py --image /path/to/annotated-test.jpg`
opens loopback port 8767, permanently labels its test image and synthetic radar/scan,
rejects all POSTs, and never imports hardware code. It is not used on the robot.

## Model provenance

Model and reference decoder: [official OpenCV Zoo NanoDet](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet),
`object_detection_nanodet_2022nov.onnx` (NanoDet-m-plus-1.5x_416), included locally
as `models/person_nanodet.onnx`; Apache 2.0 licence in `vendor/nanodet/LICENSE`.
SHA-256: `4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186`.
All images and inference remain on the Pi. The generic person model is not face or
identity recognition and is not trained or validated for this robot's environment.

## Automatic sensors and driving

Run `./start.sh` to enable driving; it uses the existing adritforge account for
TinyRad access. `./start-stationary.sh` keeps driving disabled. Both launchers start
camera, radar and C1 automatically. Sensor previews select the main view; there are
no power buttons. The sources keep capturing across view changes and phone reconnects.
A failed startup of one source does not prevent the other from starting. Each live
reader reconnects after unplug. Shutdown cleans up both sources even if a stop fails.
Legacy toggle endpoints reject cached clients with a refresh message.
Do not start a second reader for any device while this server owns them.

Live validation: simultaneous radar/C1/camera/SLAM passed 45/45 fresh samples with
zero reconnects, driving enabled but no wheel command sent. Main camera uses the
space formerly taken by the preview row, preserving the complete uncropped frame.

## Camera capability and Live cadence

The connected icSpring camera enumerates only YUYV 640×480, nominal 30/25/20/15/10/5
fps. Its active V4L2 interval is already 30 fps; it cannot capture 1920×1080.
Live is now the default raw camera stream. The former fixed 80 ms encoding sleep
has been removed; fresh frames are encoded as they arrive. Actual source and
encoded-stream rates are reported separately as `camera.fps` and `camera.stream_fps`,
with actual `camera.resolution`. Person Boxes remains an opt-in, slower exact-frame
analysis view. A 1080p30-capable camera is required for real 1080p30 capture.

The main camera label reports measured FPS for the selected Live or Boxes stream;
LiDAR uses Hz for scan rate. FPS is not the nominal camera setting.
