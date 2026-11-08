# Handoff: Wi-Fi "x-ray" (ESP32 CSI) work, 2026-11-07 ~20:20 EST

For a fresh Claude Code session. Read this file fully, then `wifi-sensing/CLAUDE.md` (its rules are binding), then `wifi-sensing/xray/README.md`.

## Goal (from the user)

Make the Wi-Fi sensing work in a crowded hackathon venue. Only the 2 teammates sitting right next to the sensor should count as "near". The long-term aim is a tool that helps first responders find people behind rubble.

**Be honest about limits.** One ESP32 link gives presence and breathing-like **candidates** near the link. It gives no location, no count, and no reliable through-rubble detection. "No candidate" must never mean "nobody there". RuView's own `person_count` and pose are heuristics or placeholders; I read its code: the pose is a template skeleton, and the count is a min-cut heuristic that its own docs say overcounts.

The user wants autonomous work and **constant pushes to GitHub**. They will fix the power problem later.

## Where things are

| What | Where |
|---|---|
| This work | branch `wifi-xray/near-zone-presence`, worktree `~/Team-28-wifi-xray`, pushed to `origin`, no PR yet. Based on `docs/ruview-csi-handoff` (PR #2, another session's CSI tooling) |
| New code | `wifi-sensing/xray/` (`csi_packet.py`, `dsp.py`, `engine.py`, `receiver.py`, `evaluate.py`, `README.md`) |
| Tests | `bash wifi-sensing/tests/run_tests.sh`: 63 shell tests plus all Python tests pass. The xray tests are `wifi-sensing/tests/test_xray*.py`, on SYNTHETIC data from `tests/synth_csi.py`, so they are not evidence of accuracy |
| Robot work (separate) | `~/Team-28`, branch `robot-scout` (local only, phases 1–5). Do not switch branches or stash there. Shared copy: `/srv/team28/Team-28` |
| Teammate's phone app | `/srv/team28/wavesense-mobile` (group `team28`). Motor patch waiting: `/srv/team28/patches/chassis-motors.patch` |
| RuView source (read-only reference) | `~/ruview-source`. Server: `v2/crates/wifi-densepose-sensing-server/src/main.rs` |
| Logs | `~/missions/power.log` (power logger), `~/missions/xray-receiver.log` |

Commits on the branch: `9a1edf8` false-"live" fix → `2080e0b` engine → `2e44410` tests and SNR fix → `eb817e5` receiver → `16761b8` `--udp-loopback` → `e45cda5` evaluate → `fc7e736` README → `aed7ee9` this handoff → `2544267` `scripts/esp32_log.py` → `8ba612d` link hints in status, `fusion.py` (two links must agree). `git log` has the rest.

Commit identity, no global git config:

```
git -c user.name=Trade42 -c user.email=103878080+Trade42@users.noreply.github.com commit ...
```

End each commit message with the Co-Authored-By / Claude-Session lines given in your system reminder. Run `sync` after commits, because power cuts happen.

## Live system right now (it does not survive a reboot)

```
ESP32-C5 node 1 (USB /dev/ttyACM0, provisioned for SSID ForgeCSI -> 10.42.0.1:5005)
   └─(should join)─> hotspot ForgeCSI on wlan1 (mt7921u, 10.42.0.1/24, ch 6, autoconnect=yes)
xray front door: python3 -m xray.receiver --iface wlan1 --forward 127.0.0.1:5006   (nohup, run from wifi-sensing/)
   └─> RuView container ruview-live: UDP published 127.0.0.1:5006, web on 127.0.0.1:3000 (token ~/.config/ruview/api_token, never print)
status: curl -s 127.0.0.1:3100/status.json      page: http://localhost:3100 (forward the port)
```

**After a reboot**, run these. You are in the `docker` group. The hotspot comes back by itself.

```bash
cd ~/Team-28 && nohup .venv/bin/python tools/power_log.py --every 1 >/dev/null 2>&1 &
docker start ruview-live
cd ~/Team-28-wifi-xray/wifi-sensing && nohup python3 -m xray.receiver --iface wlan1 --forward 127.0.0.1:5006 > ~/missions/xray-receiver.log 2>&1 &
```

## RESOLVED 21:13: the ESP32 streams CSI

- **Cause of the silent hotspot (fixed 21:08):** the Panda adapter on a USB 3 port. The user moved it to a **USB 2.0 port**; the AP came on air at 21:08:18 and the ESP32 joined at 21:08:19. A driver reload (21:02) had not helped. Keep it on USB 2.0. The `txpower 3.00 dBm` reading is an mt7921 reporting bug, not a cause.
- **BOOT button = download mode.** The user pressed BOOT on the ESP32 at about 21:08:36, and it sat silent in the ROM loader. `esptool --before no-reset --after hard-reset chip-id` did NOT reset it ("Stub flasher is already running" afterwards). **`esptool --port /dev/ttyACM0 --before no-reset --after watchdog-reset chip-id` did** (21:13). chip-id is on the allowed list.
- **Measured link facts** (21:14, in the README "Link facts" table): 27.9 CSI frames/s, 52 subcarriers, RSSI -69 to -71 dBm, all datagrams forwarded to RuView; RuView `check_stream.sh` says VERIFIED.
- `/dev/ttyUSB0` (CP2102N) is the robot's RPLIDAR C1, not the second ESP32. Never open it.

## Other open problems

- **Power cuts:** 7 since 18:00 (18:07, ~18:45, 19:17, 19:41, 19:51, 19:58, and ~20:54: the power log stops at 20:54:38 at 5.13 V, no throttling, no shutdown logged) on a 5 A wall USB-C supply. `rsts=0x1000` means real power-on resets. The 1 Hz log shows a healthy 5.13–5.16 V with no throttling right up to each cut. Suspects: an overheating charger, a loose connector, or a shared multi-port charger. The user said they will fix it later.
- **Persistent journal still off:** Raspberry Pi OS ships `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` with `Storage=volatile`. The user needs to run: `sudo mkdir -p /etc/systemd/journald.conf.d && printf '[Journal]\nStorage=persistent\n' | sudo tee /etc/systemd/journald.conf.d/99-persistent.conf && sudo systemctl restart systemd-journald`
- **Power logger at boot:** a crontab `@reboot` entry was blocked as unauthorized persistence. Only add it if the user explicitly asks.
- **Second ESP32 (same model, C5):** not identified or set up yet. It should become node 2 (`provision.py --node-id 2`). Flashing and provisioning need the user's explicit OK for that exact action (see `wifi-sensing/CLAUDE.md`). `esptool chip-id` / `flash-id` / `read-flash` are allowed.
- **Unplugged:** the thermal camera (to cut USB load).

## Next steps, in order

1. ~~Record the packet rate, RSSI and subcarrier count as measured facts.~~ Done 21:14 (README "Link facts").
2. ~~Calibrate on site~~ **Done 21:17:54–21:19:34**: the user confirmed the near zone clear, 30 s settle, 99 valid windows at 32.2 Hz, saved to `~/.config/wifi-xray/node1.json`. Thresholds: profile 4.0 (the floor; p95 1.05), breathing SNR 35.9 (p95 23.9), motion median 0.36. Right after, the empty zone read `no_candidate` 6 of 6 checks (app output, not an accuracy measurement). Recalibrate if the node, the Pi or the furniture moves. Original instructions: `curl -X POST 127.0.0.1:3100/calibrate/start`, wait at least 90 s, then `/calibrate/finish`.
3. **In progress.** `empty-crowd-A` recorded 21:21:25–21:24:41 with the user's OK (`~/missions/csi/empty-crowd-A-20261107-212125.csirec`). The live calibration gives 0.0% false candidates on it (164 windows, measured). `one-seated-A` (21:27:14–21:30:31): **not detected, 0%**, with the ESP32 and the Panda adapter side by side on the Pi and the user about 60 cm away. The link is too short to cross anyone. **21:35: asked the user to move the ESP32 onto its own USB power, 1.5–3 m away, so the link crosses the teammates' seats.** Then recalibrate and redo the sessions.
   **21:33–21:44:** on a power bank 2–3 m away, the node never rejoined. Back next to the Pi on the same power bank, it rejoined at 21:41:02 and streamed about 30 frames/s at -72..-74 dBm (measured), so power was fine and range was the problem. **21:44, the user looked at the module: it has an empty U.FL socket, so no antenna is fitted.** That explains -61..-74 dBm at a few cm, the venue AP heard 35 dB weaker than by wlan0, and no link at 2–3 m (inferred). **Action: fit a 2.4 GHz U.FL/IPEX MHF1 antenna** (not MHF4); check the second ESP32's packaging first. Then place it 1.5–3 m across the seats and recalibrate. **21:45** antenna fitted (+30 dB). **21:57 ROOT CAUSE of the earlier misses: the C5 payload is signed 12-bit words, not int8** (commit `0d49c2c`, README section). Re-scored, the seated recordings look like detections (97%, 98.8%), **but `empty-ant-B` (22:04, zone empty) also reads candidate in nearly every window**, further from the calibration than the seated session. So the profile evidence is confounded by a lasting change (object moved, device nudged, or warm-up drift) and NOTHING is shown yet. **22:09–22:23 alternating test (see README results): a still seated person is NOT separable from empty;** a lasting step at the first sit-down (likely the chair), then drift. `UNCERTAIN-alt-one-2` was discarded (the user was unsure whether to sit). **22:24:** offered the user (a) a 2-minute motion test (walking through the link vs empty) or (b) a write-up recommending mmWave radar for still people. **22:08 (earlier):** asked the user what changed. Plan: touch nothing, recalibrate, then alternate 1 min empty / 1 min seated three times (labels `alt-empty-1`, `alt-one-1`, ...), so drift hits both conditions equally. Still to do: **Record labelled sessions with the two consenting teammates**, at least 3 min each: `empty-crowd-A`, `empty-crowd-B`, `one-seated-A`, `two-seated-A`, `two-seated-crowd-B`. Use `curl -X POST '127.0.0.1:3100/record/start?label=NAME'` and `/record/stop` on the running receiver (no restart needed; files go to `~/missions/csi/`). Recordings stay on the Pi and are never committed.
4. `python3 -m xray.evaluate --calibrate <empty-A> --save /tmp/cal.json` then `--cal /tmp/cal.json <the others>`. Tune the thresholds in `engine.py` (class constants) from that data only. Fill the README results table, labelling every number measured / app output / inferred.
5. Set up node 2, with the user's OK. Two links from different angles cut false candidates; fuse them by requiring agreement.
6. Show the status in the WaveSense Wi-Fi tile (`/srv/team28/wavesense-mobile`), coordinating with the teammate, who edits there too. Keep the wording: "CANDIDATE: verify". **Patch ready (20:32):** `/srv/team28/patches/wifi-xray-tile.patch` + `README-wifi-xray-tile.txt`. The app files belong to the `teammate` user (mode 644), so the teammate has to apply it. The running app is `camera_preview.py`, not `server.py`.
7. Open a PR for `wifi-xray/near-zone-presence` when it has real results (ask the user first).
