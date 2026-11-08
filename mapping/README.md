# Mapping (Phase 5)

`mapping/slam.py` runs BreezySLAM `RMHC_SLAM` (scan matching only, no odometry) on every RPLIDAR C1 revolution:
- **Install:** `~/vendor/BreezySLAM` at commit `1170285`, installed into `.venv` with `pip install --no-build-isolation ~/vendor/BreezySLAM/python`.
- **Map:** 800 px × 20 m (25 mm per px).
- **Parameters:** quality 25, hole width 600 mm, sigma_xy 50 mm, sigma_theta 20°, 3000 iterations, chosen by a sweep on simulated scans (details in the `slam.py` docstring).

```bash
.venv/bin/python tools/slam_record.py --seconds 90 --out ~/missions/test            # robot driving
.venv/bin/python tools/slam_record.py --seconds 90 --out ~/missions/test --carry    # robot carried by a person
.venv/bin/python -m unittest tests.test_slam -v
```

`--carry` ignores returns within 700 mm across the robot's back 120°, which is where the person holding it stands.

## Results, 2026-11-07

| Test | Result |
|---|---|
| Static, 20 s | 10.1 updates/s, drift ≤ 8 mm and 0.1°, 6.8 ms per update, 6.9% total Pi CPU |
| Carry, 60 s, one lap of the room | 604 updates (10.0/s, 0 skipped), 3.9 ms per update, 7.1% total Pi CPU. Trail 12.7 m. End pose +51 / −167 mm and −358.2° from the start: about 18 cm and 2° after a full clockwise lap. |

The operator confirmed the carry map matches the room's shape: a room of about 4.1 × 2.6 m. The walls with windows come out blurred, because the laser passes through or reflects off the glass.

## Limits

- **No odometry or loop closure.** Drift grows with distance travelled.
- **sigma_xy 50 mm assumes under about 0.5 m/s.** Faster motion or quick turns can lose track.
- **Glass and dark surfaces** give missing or blurred walls.
- **Left/right is unverified.** If `lidar.angle_direction` in `config/robot.json` is wrong, the map comes out flipped top-to-bottom. This room is too symmetric to tell. Check it with a hand at the robot's left in the live LiDAR view.
- **USB power:** with the thermal camera, Wi-Fi dongle and ESP32 all plugged into the Pi's 3 A-limited USB (600 mA total), the LiDAR trips over-current and never delivers a revolution. This run worked with the thermal camera unplugged.
