# Radar recovery handoff

These files mirror the narrow fixes deployed to the canonical `wifi-sensing/radar`
directory of Team-28-wifi-xray, branch `wifi-xray/near-zone-presence`.
They are snapshots for review; the mobile app never imports/starts the service.
`baseline.json` records the inspected pre-change SHA-256 values.

- `tinyrad_dev.py` + `radar_threading.py`: ADI Connection registers SIGINT during
  construction. The acquisition worker must not call signal.signal. A facade only
  on the vendor Connection module leaves process-wide signal handling unchanged.
- `radar_service.py` + `radar_recovery.py`: reconnect delay survives a successful
  open followed by an immediate read failure. Backoff is 3/10/30/60 s; only two
  seconds of real frames reset it. Failed starts close partially opened devices.
  Error status remains offline until a real frame arrives. Startup version query
  now runs before acquisition threads, avoiding interleaved USB request/reply traffic.
- Tests in `../../tests/test_radar_threading.py` and `test_radar_recovery.py` use
  fake devices/handlers and never start USB capture.

Do not run a second TinyRad reader. Radar + camera passed a 45 s stationary test
with C1 stopped (zero reconnects). All three together caused shared USB over-current.
No automatic switching mechanism was added; keep radar/C1 capture exclusive for now.
No firmware/network/calibration/detection-engine changes are part of this patch.
