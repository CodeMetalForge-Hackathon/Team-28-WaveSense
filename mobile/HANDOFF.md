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

## Stick response and maximum speed update

Small slider between the sticks: 0–100%, default 50%, persisted on that phone. Zero
sends neutral. It caps the combined normalized movement/rotation wheel mix, so
diagonal movement plus turning cannot exceed the selected command limit. The
existing physical motor limit is unchanged. This is a command percentage, not a
calibrated ground-speed percentage; the chassis still has its wheel breakaway floor.

Changed inputs no longer wait for the 75 ms heartbeat after an acknowledgement.
At most two movement requests run concurrently; only the latest pending position
is retained. Reversal/slowing input is prioritized. Full neutral bypasses movement
requests, and clearing one motion axis also sends a priority zero before continuing
the remaining axes. Sequence numbers and leases still reject stale commands.
Heartbeat is 40 ms; normal moving input is coalesced at up to 50 Hz. Stick deadzone
is reduced from 12% to 6%. Network and initial motor-controller arm handshake
latency still apply. No automatic arming or movement on reconnect.

Validation: existing control transport tests, new responsive-transport/speed-cap
tests, and three local HTTP tests with fake motors passed. Slider UI was checked
with an explicitly synthetic preview. No robot wheel movement was commanded.

## Follow-up: real speed cap and motor readiness

This supersedes the earlier 50% default and command-only mapping. The old slider
only varied duty between 35 and 40, and every release reopened the motor connection
on the next touch. Protocol 4 now carries the selected speed to the server.
Full stick maps 25%→38.75 duty, 50%→42.5, 100%→50, within the chassis/board hard
limit; 0% stops. Actual ground speed remains uncalibrated. The default is now 100%,
using a new preference key so an old saved 50% does not silently persist.

Normal release sends immediate chassis STOP and invalidates the lease, while
keeping the zero-speed connection ready for five seconds. It is reused only with
a healthy deadman and fresh battery telemetry. Idle expiry, camera loss, faults,
watchdog and server shutdown close it. Initial centre contact begins zero-speed
readiness; movement still requires stick deflection. The 120 ms reversal brake
and 300 ms control watchdog remain. Old protocol-3 pages must refresh.

Checked with fake motors/serial only: real PWM-cap mapping, healthy connection
reuse, lease invalidation, zero stop, idle expiry, camera-loss close and rejection
of commands exceeding the selected limit. No physical drive test was performed.


## Full protocol duty and transport response — 2026-11-08

Supersedes the previous 50-duty maximum and five-second readiness window.
Operator requested maximum speed and faster stick response. The old 50 ceiling
was a software bring-up restriction imported from `robot/nudge.py`, not the UART
protocol maximum. `robot/board.py` now clamps at +/-100 and the app requests a
100-duty ceiling. The separate nudge tool retains its conservative 50-duty cap.
`config/robot.json` records the new ceiling. At full stick, slider values
25/50/100 map to duties 51.25/67.5/100 with min_duty=35. Zero still stops;
combined translation/rotation is normalized. Speed is not measured ground speed.
The new phone preference key starts the slider at 100% after refresh.

The app requests slew=1000 duty/s (previously 250): nominal full-duty ramp is
100 ms rather than 400 ms, excluding transport, board readiness and the 50 Hz
tick. Immediate STOP, lease invalidation, 300 ms watchdog, independent deadman,
120 ms reversal brake and camera interlock are retained. A normal release keeps
the stopped connection ready for 30 seconds; no lease survives release and no
movement resumes without fresh input. Idle expiry, camera loss and faults still
close the connection. First touch after cold/expired readiness must still wait
for healthy deadman and fresh battery telemetry. No automatic motor opening.

HTTP/1.1 reuses control connections, disables Nagle buffering and expands the
accept backlog to 64. Rejected unread POSTs close their connection, preventing
leftover bodies from becoming requests; indefinite video streams close too.
These changes remove avoidable transport overhead, not all network latency.

Validation uses fake motors, fake serial and a pseudo-terminal for the deadman.
No nonzero commands or physical wheel tests by the agent. Confirm floor speed
and reaction as the operator. Before deployment the live radar status was fresh
with range/angle telemetry, unlike the previous BrdRst-timeout handoff; mounting
remains unverified and the radar is not a confirmed-human classifier. No radar
reader, Wi-Fi, Tailscale, Docker or firmware changes are included.

Deployment verification: camera, 2D LiDAR and radar were online after restarting
the canonical app. Live status reports max_duty=100 and motors disarmed. Twelve
read-only status requests reused one HTTP/1.1 socket (36 ms median, 120 ms max
round trip from the development computer); this is not a measured drive response.
Rollback files: `/srv/team28/wavesense-mobile/before-full-speed-20261108-105908`.
