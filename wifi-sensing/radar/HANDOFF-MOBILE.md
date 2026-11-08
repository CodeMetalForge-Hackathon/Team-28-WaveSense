# WaveSense teammate handoff — 2026-11-08

## Current app

Phone: http://100.99.36.78:8080/ (Tailscale). Canonical app: `/srv/team28/wavesense-mobile`.
The server is running with driving enabled; no movement commands were sent by this agent.

- Camera is main; top-left LiDAR mini view swaps with it on tap. Swapping moves the existing video element, preserving its connection. Tap again to swap back.
- Boxes are always enabled. No Live/Boxes toggle or old LiDAR/Radar tile row. Video stays full rate; camera-only detection overlay updates asynchronously and expires after 450 ms. Positions may briefly lag video.
- Main camera now fills the viewport proportionally (`cover`); landscape crops top/bottom. SVG overlay uses matching `slice`. Camera in the mini view preserves its complete frame (`contain` / SVG `meet`).
- Radar, LiDAR and camera start automatically. Sticks remain available. Sensor capture never opens the motor UART; only fresh operator drive input does.
- Radar chip opens measured distance/profile, candidate history and separate camera/LiDAR context. Amber means unconfirmed. Received signal strength is not a probability or a person count.
- The attached icSpring camera enumerates only 640×480 YUYV; nominal 30 fps mode, actual video measured roughly 20–23 fps. Real 1080p30 requires another camera.

## Radar: important, unresolved hardware issue

TinyRad enumerates on USB (`064b:7823`) through the powered hub, but after the reboot it stopped answering its initial `BrdRst` command. The current failure is **USBTimeoutError before factory calibration or capture starts**. Camera and C1 remain available. Software USB resets have not restored command replies. The user has been asked to unplug only the radar USB for five seconds and reconnect it; this has not been confirmed yet.

The radar startup now retries with 3/10/30/60 s backoff. The app also supervises/restarts a dead radar subprocess. Do not start another radar reader alongside it. After a confirmed physical replug, stopping only the managed radar child lets the app restart it without restarting camera/drive services. Do not reflash or change firmware to work around a USB timeout.

Canonical radar code: `/home/adritforge/Team-28-wifi-xray/wifi-sensing/radar`, branch `wifi-xray/near-zone-presence`. App uses its existing venv and reads `/srv/team28/runtime/radar/status.json`.

## Radar software changes

- The existing engine analyses 0.3–5 m but the phone previously watched only 2 m. The app now launches `--near-range-m 5.0`, retaining existing thresholds and adaptive background logic.
- Replay of the team's documented empty/person recording gave identical 2 m and 5 m results: empty-before 0/31 candidate seconds; seated person 57/60; empty-after-first-10-seconds 10/50 (window/hysteresis carryover). These are in-sample results, not general accuracy or a fresh human test.
- New `radar_scene.py` publishes bounded measured range profiles and experimental four-RX angle-return data using ADI's antenna geometry and factory complex channel correction. It keeps the existing single-TX capture mode. Factory correction is read only, before capture; failure leaves range processing available and direction unavailable.
- Angle output is sensor-local and **mounting unverified**. Camera/LiDAR extrinsics and left/right orientation have not been measured. The angle heatmap is not a registered human location. No radar silhouettes are projected onto camera/map.
- Missing/stale/malformed scene data is rejected. Silence does not become a strong return. New scene telemetry does not change the legacy presence classifier.
- No vital signs are shown. Motion/micro-motion can be caused by people, fans, cables or multipath. No candidate is not proof of an empty room. Current radar cannot certify a human behind a wall.

## Next required work

1. Restore radar command replies with a physical USB/power check; verify changing frames, no USB resets, and whether real factory correction loads.
2. Validate centre/left/right known reflectors or people to establish angle sign and mounting. Test empty scene, visible person, then a person behind a known obstacle with the robot stationary. Do not claim human detection accuracy from synthetic tests or the existing recording alone.
3. Inspect range/angle returns and false positives before changing classifier thresholds. Through-wall human localization still needs this validation and spatial calibration.

## Validation and rollback

The last completed Pi regression suite passed 62 tests. Synthetic tests cover range/angle geometry and phase correction, stale/bounded telemetry and missing correction. Additional tests for managed-worker and cold-start recovery are included. User requested publication without further measurement; no new live-person validation was completed.

App backup: `/srv/team28/wavesense-mobile/before-mini-swap`. Radar backup: `/home/adritforge/missions/before-swap-radar`. No raw recordings, camera images, credentials or runtime logs are included in Git.

Source method: https://github.com/analogdevicesinc/TinyRadTool (commit dcea3878b73af183fa4404fc457da61380c29933), `RfGet('RxPosn')`, `ModBpa` factory correction/range preprocessing. Manufacturer guide: https://www.analog.com/media/en/technical-documentation/user-guides/ev-tinyrad24g-ug-1709.pdf
