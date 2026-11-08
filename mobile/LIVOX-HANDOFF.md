> Historical 3D handoff. Current default is 2D C1; see README.md.

# WaveSense mobile sensor console

Phone URL on the robot's Tailscale network: **http://100.99.36.78:8080/**.

## Current handoff: Livox 3D + radar input contract

The default LiDAR source is now **Livox MID-360S at 192.168.1.3** (model inferred from the supplied box label). `--lidar-model mid360` selects the original MID-360 from the supplied manual instead. Read actual device identity from SDK discovery before treating the model as verified.

- GPU point cloud: perspective orbit, orthographic top/front, fit, height colour and camera-aligned X/Y/Z orientation widget. One-finger orbit in 3D; one-finger pan in orthographic views; pinch zoom. Camera remains the default main view; thermal, LiDAR and radar previews can take over or float.
- Metric, sensor-local coordinates. XY grid spacing is **1 m**. Height colouring spans **-1 to +3 m relative to the sensor**, not above the floor. Mounting orientation is not calibrated. This is **not a registered world map**, SLAM or person classification.
- Official Livox SDK2 handles device discovery/configuration and point/IMU capture. Small C++ bridge forwards only the selected IP to an ephemeral loopback UDP socket. It never opens the robot UART, changes device IP, reboots or reflashes anything.
- Python checks header length, sample count and CRC, decodes Cartesian high/low and spherical samples, separates IMU, removes invalid/low-confidence returns and produces an 8 cm voxel preview. Rolling sensor-frame window 0.3 s, local range 0.1-35 m, at most 18,000 points, 5 previews/s. Moving objects/robot motion can smear this short window; there is no invented motion compensation.
- Packed browser data is 8 bytes/point (centimetre XYZ, reflectivity, tag), with a versioned 32-byte header. Browser uses fixed GPU buffers, event-driven drawing, one in-flight cloud request and a 2D preview thumbnail. Hidden tabs stop polling. No external CDN is required.
- Old clouds visibly dim and never remain labelled fresh. API refuses stale point clouds. Reconnect discards pre-outage points. Empty sensor input never becomes synthetic data.
- Radar is separate from Wi-Fi CSI. The former Wi-Fi tile is now **Radar**. USB enumeration alone is never interpreted as a person. Radar observations are not drawn on LiDAR geometry until spatial registration is measured.

## Stationary startup (no driving)

On the Pi:

```sh
cd /srv/team28/wavesense-mobile
./livox/setup-network.sh
./start-stationary.sh
```

The network script creates only a dedicated `eth0` profile, `192.168.1.50/24`, with **no default route**. Wi-Fi, Tailscale, Docker, firmware and hotspot settings are untouched. The profile autoconnects when Ethernet carrier appears. It cannot fix missing power/cabling.

`start-stationary.sh` does **not** pass `--enable-drive`; arm/drive requests are rejected. The historical `start.sh` retains its previously requested driving configuration. Do not use that launcher for stationary integration checks. No autostart service is installed.

After copying this directory from GitHub to a clean Pi, first build the native receiver:

```sh
sudo apt-get install -y --no-install-recommends cmake g++ make
./livox/build.sh
```

SDK source is pinned to `c0796f04c143143899c87a773d9f6b7136453c0b`. Three.js is vendored from npm `three@0.187.0`, with its MIT licence; OrbitControls has only its import path rewritten to the local module. Python uses the team's `/srv/team28/Team-28/.venv` (numpy/OpenCV/pyserial/BreezySLAM). Build outputs, runtime configuration and logs are excluded from Git.

## Power and Ethernet: unresolved physical check

The supplied MID-360 manual specifies 9-27 V DC, recommends 12 V, and lists about 18 W during startup (~1.5 A at 12 V). A USB-C power bank advertising 12 V is insufficient evidence of actual 12 V at the sensor: verify the selected output, compatible PD trigger, polarity and voltage under startup load. Do not wire from connector appearance alone. The S variant should also be checked against its own current manual.

At the latest check, the Pi reported `eth0` **NO-CARRIER**, and 192.168.1.3 was unreachable. The supplied ports 52000-52500 are not the official MID-360/S defaults in the pinned SDK: UDP discovery 56000; sensor command/push/point/IMU/log 56100/56200/56300/56400/56500. Host receiving ports are configurable and set to 56101-56501. Do not interpret a TCP port scan as proof that the correct UDP sensor stream works. Do not change sensor firmware/IP to guess around this discrepancy.

Once carrier is up:

1. Confirm `ip route get 192.168.1.3` uses `eth0`, source 192.168.1.50.
2. Check `livox-worker.log` for discovered model and configuration errors.
3. Check `/api/status`: LiDAR `online`, changing `sequence`, point rate, rejected packet count and IMU samples. `/api/lidar3d.bin` must return fresh WS3D frames.
4. Compare a measured target distance and known sensor axes; calibrate sensor-to-robot mounting before fusion or robot-frame clearance claims.
5. Verify camera and sensor stability together without driving.

## Radar teammate contract

No radar PR was available at inspection: accessible GitHub PRs were #1 (motor bring-up) and #2 (Wi-Fi CSI). An Analog Devices USB device `064b:7823` is enumerated, but neither its exact radar model nor a working radar capture driver was verified. The existing `wifi-xray` patch is CSI candidate logic and is **not** repurposed as 24 GHz radar data.

The app polls `/srv/team28/Team-28/runtime/radar/status.json` (override `--radar-status-file`). Driver should atomically replace the file with:

```json
{"schema":1,"timestamp_ms":1794100000000,"online":true,"state":"candidate","tracks":[{"range_m":2.5,"bearing_deg":null}]}
```

Use the actual current Unix timestamp in milliseconds. Accepted states: `candidate`, `motion`, `clear`, `not_judging`. A range without measured azimuth must keep `bearing_deg: null`. Only fresh data (<2 s) is displayed; at most 32 bounded tracks are accepted. Candidates are unverified; a clear result is not proof that nobody is present. Through-wall performance and spatial alignment remain unvalidated. No person counts or fake silhouettes are generated.

## Validation and graphics fixture

```sh
/srv/team28/Team-28/.venv/bin/python -m unittest discover -s tests -v
node tests/test_control.js
node tests/test_lidar.js
node tests/test_cloud_codec.mjs
```

34 Python tests passed on the Pi, including all supported coordinate formats, CRC/truncation, millimetre/centimetre conversions, bounded binary payloads, simulated loopback SDK receiver lifecycle, stale output rejection, radar freshness and control leases. JS transport/projection/binary checks pass. Native bridge compiled against the pinned official SDK. Browser graphics checked in portrait/landscape with synthetic geometry, explicitly labelled as such, with no robot connection.

Optional graphics-only fixture: `python3 tools/preview_3d.py`, then http://127.0.0.1:8766/. It binds loopback, never imports hardware code, rejects every POST, and displays a permanent synthetic-data banner. It is never used by the live server.

Legacy `--lidar-mode 2d` still uses the single C1 reader shared by `mapping.slam.Slam` and proximity. The C1/thermal were not enumerated at this handoff check. Actual RGB/radar/Livox hardware results must be reported individually; software tests do not establish real person detection or successful sensor startup.

## References

- Supplied manual: https://terra-1-g.djicdn.com/851d20f7b9f64838a34cd02351370894/Livox/Livox_Mid-360_User_Manual_EN.pdf
- Official SDK: https://github.com/Livox-SDK/Livox-SDK2
- Protocol: https://livox-wiki-en.readthedocs.io/en/latest/tutorials/new_product/mid360/livox_eth_protocol_mid360.html
