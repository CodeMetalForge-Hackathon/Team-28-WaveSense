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

Commits on the branch: `9a1edf8` false-"live" fix → `2080e0b` engine → `2e44410` tests and SNR fix → `eb817e5` receiver → `16761b8` `--udp-loopback` → `e45cda5` evaluate → `fc7e736` README.

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

## BLOCKER: the ESP32 can't see the hotspot

The ESP32 serial log says `WiFi disconnected, reason=201 rssi=-128` (`NO_AP_FOUND`) and retries every 30 s. The hotspot adapter comes up at **`txpower 3.00 dBm`** after every boot (`iw dev wlan1 info`); the regulatory limit allows 30 dBm. The user saw ForgeCSI from `wlan0`.

- **Fix, needs the user's sudo, every boot:** `sudo iw dev wlan1 set txpower fixed 2000`
- **Persistent fix, to propose to the user (root):** an executable NetworkManager dispatcher script `/etc/NetworkManager/dispatcher.d/90-forgecsi-txpower` that runs that command when `wlan1` comes up.
- You cannot `sudo`, and `nmcli up` / `wifi rescan` are "not authorized". Ask the user to run root commands in their own terminal.
- To read the ESP32 log safely (no reset): open `/dev/ttyACM0` with `os.open(O_RDONLY|O_NONBLOCK|O_NOCTTY)` and raw termios with HUPCL cleared, and never touch DTR/RTS. pyserial's open hung once. Mask MACs and any password-like fields.

## Other open problems

- **Power cuts:** 6 since 18:00 (18:07, ~18:45, 19:17, 19:41, 19:51, 19:58) on a 5 A wall USB-C supply. `rsts=0x1000` means real power-on resets. The 1 Hz log shows a healthy 5.13–5.16 V with no throttling right up to each cut. Suspects: an overheating charger, a loose connector, or a shared multi-port charger. The user said they will fix it later.
- **Persistent journal still off:** Raspberry Pi OS ships `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` with `Storage=volatile`. The user needs to run: `sudo mkdir -p /etc/systemd/journald.conf.d && printf '[Journal]\nStorage=persistent\n' | sudo tee /etc/systemd/journald.conf.d/99-persistent.conf && sudo systemctl restart systemd-journald`
- **Power logger at boot:** a crontab `@reboot` entry was blocked as unauthorized persistence. Only add it if the user explicitly asks.
- **Second ESP32 (same model, C5):** not identified or set up yet. It should become node 2 (`provision.py --node-id 2`). Flashing and provisioning need the user's explicit OK for that exact action (see `wifi-sensing/CLAUDE.md`). `esptool chip-id` / `flash-id` / `read-flash` are allowed.
- **Unplugged:** the thermal camera (to cut USB load).

## Next steps, in order

1. Once the ESP32 streams (`status.json` `link.csi_frames` rising, RuView `udp_datagrams` rising), record the packet rate, RSSI and subcarrier count as **measured** facts.
2. **Calibrate on site** with the near zone empty and the crowd present: `curl -X POST 127.0.0.1:3100/calibrate/start`, wait at least 90 s, then `/calibrate/finish`.
3. **Record labelled sessions with the two consenting teammates**, at least 3 min each: `empty-crowd-A`, `empty-crowd-B`, `one-seated-A`, `two-seated-A`, `two-seated-crowd-B`. Use `receiver.py --record ~/missions/csi --label NAME`, stopping the background receiver first. Recordings stay on the Pi and are never committed.
4. `python3 -m xray.evaluate --calibrate <empty-A> --save /tmp/cal.json` then `--cal /tmp/cal.json <the others>`. Tune the thresholds in `engine.py` (class constants) from that data only. Fill the README results table, labelling every number measured / app output / inferred.
5. Set up node 2, with the user's OK. Two links from different angles cut false candidates; fuse them by requiring agreement.
6. Show the status in the WaveSense Wi-Fi tile (`/srv/team28/wavesense-mobile`), coordinating with the teammate, who edits there too. Keep the wording: "CANDIDATE: verify".
7. Open a PR for `wifi-xray/near-zone-presence` when it has real results (ask the user first).
