# Wi-Fi CSI subsystem: notes for Claude Code

Read `docs/wifi-csi/README.md` first, then `RESULTS.md` and `NEXT_STEPS.md`.

## Hardware facts that are easy to get wrong

- The CSI node is an **ESP32-C5** (rev v1.0, 8 MB flash, 8 MB PSRAM, USB `303a:1001`), **not an ESP32-S3**. Never use S3 firmware, S3 pin maps or the S3 bootloader offset.
- The hotspot runs on the Panda adapter, `wlan1` (`mt7921u`). `wlan0` is the only uplink and carries SSH over Tailscale.
- The second team ESP32 has not been identified.
- Docker needs root. `sudo` asks for a password. Scripts use `sudo -n` only, so the human runs `sudo -v`.

## Never do these unless the human explicitly asks, in this session, for that exact action

- Flash, erase or `write-flash` any ESP32. Run `provision.py`. Use `esptool` for anything other than `chip-id`, `flash-id` or `read-flash`.
- Run `nmcli connection up/down/modify/add/delete`, or change `wlan0`, `wlan1`, `tailscale0` or `docker0`.
- Run `docker run/stop/rm/restart/kill`, or `start_ruview.sh --start` / `--replace-existing`.
- Print or commit secrets: the API token, the Wi-Fi password, `~/.config/wifi-densepose/esp32-provision-state/`, `~/.config/ruview/api_token` or `~/esp-backups/*.bin`. Never pass `--show-secrets` or `--password`. Never run `docker inspect` without a `--format` that leaves out env values.
- Capture RF data from people who haven't consented.

Read-only commands are fine: `diagnose.sh`, `check_stream.sh`, `start_ruview.sh` without `--start`, `ss`, `ip`, `iw`, `lsusb`, `nmcli -g` without `-s`, and `docker ps/logs` / `inspect --format`.

## When changing code here

- Keep the scripts runnable on stock Raspberry Pi OS: bash, coreutils, iproute2 and Python 3 standard library. No `jq`.
- Run `bash wifi-sensing/tests/run_tests.sh` and add tests for new behavior, using the mock fixtures. Run ShellCheck if it's available.
- Never hard-code `10.42.0.1` or `172.17.0.1`. Detect them, as `lib.sh` does.

## When writing results

- Label every number **measured / app output / inferred / unverified**.
- A healthy API, a moving dashboard, or RuView's `person_count` / `person_count_valid` is **not** evidence of accurate detection.
- Don't claim through-wall sensing, localization, pose or vital signs.
- Never fabricate data. Synthetic arrays are for unit tests only.
