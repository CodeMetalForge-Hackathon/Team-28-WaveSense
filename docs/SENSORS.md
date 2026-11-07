# Sensor bring-up

- **Date:** 2026-11-07
- **Host:** Raspberry Pi 5, Raspberry Pi OS, kernel 6.18.50+rpt-rpi-2712
- **Power:** no undervoltage (`vcgencmd get_throttled` = `0x0`)
- **Scope:** test only. No motors, no robot code.

## Summary

| Sensor | Device path | USB ID / port | Resolution | Measured rate | Result | Notes |
|---|---|---|---|---|---|---|
| **RGB**: USB "icspring camera" | `/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0` (→ `/dev/video0`) | `32e6:9005`, USB-A | 640×480 YUYV, the only mode offered | **22.6 fps** (226 frames in 10 s; 30 fps requested) | ✅ Pass | A USB camera, not a CSI Pi camera. Auto-exposure is on (Aperture Priority), which can lower fps in dim light. Image checked by eye. |
| **LiDAR**: SLAMTEC RPLIDAR C1 | `/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_12c5f3c13d70f01191c090301045c30f-if00-port0` (→ `/dev/ttyUSB0`) | `10c4:ea60` (CP2102N), behind the USB hub | about 508 points/rev, 360° | **10.2–10.3 rev/s** at **460800** baud | ✅ Pass | 84–94% of points have Q > 0. Hand read **368 mm**, against an estimated 30 cm; accepted. |
| **Thermal**: HTI HT-203U | `/dev/v4l/by-id/usb-HIK_HikCamera_F10615613-video-index0` (→ `/dev/video2`) | `2bdf:0102` "HIK HikCamera", firmware 4.09 | 256×192 thermal, inside a 256×392 YUYV frame | **24.7 fps** (250 frames in 10.1 s) | ✅ Pass | Hand found at the frame center, 32.5 °C. **Temperatures are uncalibrated.** |

There was **no CSI camera**: `rpicam-hello --list-cameras` printed `No cameras available!`, and the device tree has no camera sensor node. No IMX708 or IMX500 is connected, and none is needed, since the RGB camera is the USB one.

The other USB devices on this Pi weren't touched: a Genesys Logic hub (`05e3:0610` / `05e3:0626`) and a Realtek RTL8153 Ethernet adapter (`0bda:8153`). No ESP32 (`ttyACM*`) was connected during the test.

## RGB camera (USB UVC)

```bash
D=/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0
v4l2-ctl -d $D --list-formats-ext           # YUYV 640x480 only, 5–30 fps
# still: skip 2 s so auto-exposure settles
ffmpeg -f v4l2 -input_format yuyv422 -video_size 640x480 -framerate 30 -i $D \
  -vf "select=gte(n\,60)" -frames:v 1 -q:v 2 -update 1 -y rgb.jpg
# rate: 10 s, count real frames (no duplication)
ffmpeg -f v4l2 -input_format yuyv422 -video_size 640x480 -framerate 30 -i $D \
  -t 10 -fps_mode passthrough -f null -
```

- `rpicam-*` and Picamera2 only work with CSI cameras, so they don't apply here.
- The first still, taken after 1 s of settling, was retaken on request. The retake, after 2 s, was confirmed by eye.

## LiDAR (RPLIDAR C1)

- **SDK:** `~/vendor/rplidar_sdk` (Slamtec, commit `99478e5`), built with `make -j4`. It isn't in this repo.
- **Device reply:** model `0x41`, firmware 1.02, hardware rev 18, health status 0 (OK).
- **Test script:** [`sensors/lidar_stats.py`](../sensors/lidar_stats.py) runs `ultra_simple`. It saves the raw output, timestamps each revolution and stops the program with SIGINT.

```bash
P=/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_12c5f3c13d70f01191c090301045c30f-if00-port0
python3 sensors/lidar_stats.py --run $P --seconds 8 --out lidar.txt                          # baseline
python3 sensors/lidar_stats.py --run $P --seconds 8 --out hand.txt --baseline lidar.txt      # hand test
```

| Run | Revolutions | Points/rev (median) | Q > 0 | rev/s |
|---|---|---|---|---|
| Baseline | 52 | 509 | 83.5 % | 10.23 |
| Hand | 52 | 508 | 94.3 % | 10.33 |

**Hand test:**
- The nearest point was **368 mm at about 184°**, with a sharp edge at 187°. The surface got steadily farther toward about 121°, which fits a forearm reaching in from that side.
- The hand was held at an estimated 30 cm but wasn't measured. 368 mm was accepted as the pass.
- Other parts of the scene changed between the two scans. One example: an object 80 mm away at about 46° in the baseline was gone in the hand scan. A stricter test would take the baseline and the hand scan back to back, with the hand at a measured distance.

**Gotchas:**
- **Exit takes about 6 s after Ctrl-C.** `ultra_simple` sends STOP (`A5 25`) and the motor-off signal right away. Then `close()` on the CP2102N port blocks for about 5 s in the kernel, while one USB control request in the `cp210x` driver times out. Don't wrap it in `timeout -k 3`, which kills it mid-close. `lidar_stats.py` waits up to 12 s.
- **Motor control is by DTR.** The SDK treats this C1 as having no motor-control board. So it starts the motor by clearing the adapter's DTR line and stops it by setting DTR. Whether the motor physically stops can't be read back over serial.
- **Use `timeout -s INT`, not plain `timeout`.** SIGTERM kills `ultra_simple` before it can send its stop commands.

## Thermal camera (HT-203U)

- **Reference:** `~/vendor/ht-203u-linux` (Krastanov, commit `7d9eeba`), only read, not run or installed. No udev rules, kernel modules or direct USB access were set up.
- **Test script:** [`sensors/thermal_grab.py`](../sensors/thermal_grab.py) follows the reference's decoding of the default stream.

```bash
T=/dev/v4l/by-id/usb-HIK_HikCamera_F10615613-video-index0
v4l2-ctl -d $T --stream-mmap --stream-count=1 --stream-to=thermal.raw     # 200,704 bytes
python3 sensors/thermal_grab.py --out thermal.png --save-raw base.raw                       # baseline
python3 sensors/thermal_grab.py --out hand.png --save-raw hand.raw --baseline base.raw      # hand test
python3 sensors/thermal_grab.py --input hand.raw --out hand.png                             # re-decode a saved frame
```

**Frame layout.** The camera's default mode is 256×392 YUYV at 25 fps. Each frame is **200,704 bytes = 256 × 392 × 2 bytes per pixel**, but it isn't a normal image:

| Rows | Content |
|---|---|
| 0–191 | 256×192 raw sensor counts, little-endian uint16 |
| 193 | layout marker `0xAABBCCDD` |
| 196–387 | 256×192 YUYV preview image |
| 388 | the camera's own summary: `FFFF EEEE`, then max code, x, y, min code, x, y, mean, center. °C = code / 64 − 50. Updates about 2.5 Hz. |

**Results:**

| Frame | Camera max | Where | Camera min |
|---|---|---|---|
| Baseline, no hand | 26.3 °C | top-right | 23.2 °C |
| Hand in front | **32.5 °C** | **center, (115, 86)** | 19.8 °C |

The warmest 2% of pixels were centered at (114, 78). That matches where the hand was held, as confirmed by the person holding it.

**Gotchas:**
- **Don't set a format.** `ffmpeg -f v4l2 -i $T` fails with `Input/output error`. The kernel logs `Failed to query (GET_MIN) UVC probe control : -32`, a known problem with this firmware. `v4l2-ctl --set-fmt-video` fails the same way. Streaming the camera's current mode with `v4l2-ctl --stream-mmap` works.
- **Raw counts don't compare across grabs.** Subtracting two frames' raw counts gave large negative regions, so the camera rescales the counts between grabs. The hand check therefore uses the warmest region within a single frame plus the camera's reported max position.
- **Temperatures are uncalibrated.** They're the camera's own estimates, using the manufacturer app's code-to-°C formula, and weren't checked against a reference thermometer.

## Files

- In this repo: `docs/SENSORS.md`, `sensors/lidar_stats.py`, `sensors/thermal_grab.py`.
- Not in the repo: test outputs (images, raw frames, scan dumps) are kept locally in `~/sensor-test/`, and third-party code in `~/vendor/`.
