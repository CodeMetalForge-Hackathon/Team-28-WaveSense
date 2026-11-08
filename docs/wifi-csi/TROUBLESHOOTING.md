# Wi-Fi CSI troubleshooting

Every entry below happened to us on 2026-11-07, except where it says otherwise. Start with the read-only overview:

```bash
sudo -v && wifi-sensing/scripts/diagnose.sh
```

| # | Symptom | Section |
|---|---|---|
| 1 | Board sold or described as an ESP32-S3 | [Chip misidentified](#1-chip-misidentified-s3-vs-c5) |
| 2 | Serial monitor shows no CSI lines | [No CSI before provisioning](#2-no-csi-logs-before-provisioning) |
| 3 | `address already in use`, or 3001 conflict | [UDP/TCP port conflicts](#3-udp-port-already-in-use) |
| 4 | Dashboard moves, but the ESP32 is off | [Simulated vs real input](#4-simulated-vs-real-input) |
| 5 | Packets reach Docker but no node appears | [Allowlist drops frames](#5-docker-allowlist-drops-frames-disallowed-source) |
| 6 | HTTP 401 / dashboard "unauthorized" | [Missing or stale API token](#6-missing-or-stale-api-token) |
| 7 | `localhost:3000` doesn't load on the laptop | [Port forwarding](#7-dashboard-not-reachable-from-the-laptop) |
| 8 | Weak RSSI, noisy visualization | [Weak signal and noise](#8-weak-rssi-and-noisy-visualization) |
| 9 | "33.4% packet loss" | [Packet loss](#9-reported-packet-loss) |
| 10 | SSH or Tailscale drops when the hotspot starts | [Network isolation](#10-network-isolation-and-ssh-over-tailscale) |
| 11 | Scripts say they can't reach the Docker daemon | [sudo needs a password](#11-scripts-cannot-reach-docker) |
| 12 | `ruview-live` shows `Exited (255)` | [Container exited](#12-container-exited-255) |

---

## 1. Chip misidentified (S3 vs C5)

- **Symptom.** The board was described as an ESP32-S3.
- **Cause.** The label or listing was wrong. `esptool` read the chip itself: an **ESP32-C5** (revision v1.0), 8 MB flash, 8 MB PSRAM, USB `303a:1001`.
- **Fix.** Trust `esptool chip-id`, not the listing ([SETUP §4](SETUP.md#4-esp32-c5-identification)). Use only the **C5** bundle and `--variant c5`; the harness refuses a chip mismatch. **Never flash S3 firmware onto this board.** The C5's bootloader goes at `0x2000`, while the S3's goes at `0x0`.
- **Second board.** Its chip has not been identified. Run `chip-id` on it before choosing any firmware.

## 2. No CSI logs before provisioning

- **Symptom.** `cli.js monitor` showed console output but **no `CSI cb #N` lines**.
- **Cause.** The firmware was installed but had no Wi-Fi credentials. CSI callbacks only fire once the node has joined an AP and is receiving its frames.
- **Fix.** Provision the board ([SETUP §9](SETUP.md#9-wi-fi-provisioning)). Afterwards, a 60 s monitor showed 27 CSI log lines in 240 console lines.
- **Note.** The firmware logs callbacks #1 to #3 and then every 100th. A small line count therefore still means thousands of callbacks.

## 3. UDP port already in use

- **Symptoms.** A new container fails with a port conflict, as port 3001 did for us. A host-side UDP capture can't bind 5005.
- **Causes.**
  - Only one socket can own `10.42.0.1:5005`. When `ruview-live` is running, Docker's `docker-proxy` holds it.
  - The harness capture (`cli.js esp32 --udp-port 5005`) is a second receiver.
  - An old RuView container still held 3000 and 3001 (see the next section).
- **Fix.**

  ```bash
  ss -ulpn 'sport = :5005'; ss -tlpn '( sport = :3000 or sport = :3001 )'
  sudo docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  ```

  Stop only the specific container you have identified as obsolete. `start_ruview.sh` reports conflicting containers by name but never stops them. Run harness UDP captures only while the container is stopped.

## 4. Simulated vs real input

- **Symptom.** The dashboard showed moving data even though it wasn't reading the ESP32. Starting the real instance then failed with a **port 3001 conflict**.
- **Cause.** An earlier RuView container was still running with **`CSI_SOURCE=simulated`**, which produces synthetic data, and it still held the ports.
- **Fix.** Identify that container and stop **only that one**, then start the live instance.
- **How to tell them apart.** "Configured" is not the same as "receiving":

  | Question | Where to look |
  |---|---|
  | What was the container told? | `CSI_SOURCE` in its environment. `diagnose.sh` prints it; it never prints the token |
  | What is the server using right now? | `curl -s 127.0.0.1:3000/health` → `"source"`. `esp32` means a frame arrived in the last 5 s; `esp32:offline` means none has; `simulated` means synthetic |
  | Are frames really arriving? | `check_stream.sh`: each node's `csi_sequence` must **advance** between two samples |

## 5. Docker allowlist drops frames (disallowed source)

- **Symptom.** Packets reached Docker, but no node appeared. The RuView logs showed:

  ```
  Dropped UDP frame from disallowed source 172.17.0.1
  ```

- **Cause.** Inside the container, frames don't come from the ESP32's address (`10.42.0.x`). They come from **`172.17.0.1`, the Docker bridge gateway**. RuView's ADR-296 source guard (`RUVIEW_UDP_ALLOW`) only accepts listed sources, so it dropped them.
  - *Inferred:* Docker's userland proxy (`docker-proxy`) relays published UDP ports from its own socket, which rewrites the source address. Upstream documents this behavior for Docker Desktop on macOS; we saw the same thing on Linux with Debian's `docker.io` 26.1.5.
- **Fix that worked.**
  - Host UDP bind `10.42.0.1:5005` (the hotspot address only).
  - Allowlist `RUVIEW_UDP_ALLOW=172.17.0.1/32` (the gateway).
- **Don't hard-code either address.** Detect both:

  ```bash
  ip -4 -o addr show dev wlan1 | awk '{split($4,a,"/"); print a[1]}'
  sudo docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}'
  ```

  `start_ruview.sh` does this automatically. If your logs show a *different* disallowed source, confirm what that address is before allowing it, then use `start_ruview.sh --udp-allow <ip>/32`.
- **Security trade-off.** Allowing the gateway admits anything that can reach `10.42.0.1:5005`: every hotspot client, plus processes on the Pi itself. The UDP data plane is unauthenticated. So keep the hotspot dedicated, use a strong password (rotate it: [SETUP §9.3](SETUP.md#93-rotating-the-hotspot-password)), and never publish 5005 on `0.0.0.0`.
- **Side effect.** Without *any* allowlist, the entrypoint keeps the UDP receiver on `127.0.0.1` inside the container, so no node ever appears.

## 6. Missing or stale API token

- **Symptoms.** The container exits immediately (code 64) without `RUVIEW_API_TOKEN`. The dashboard or API returns **401** after a container restart or re-creation.
- **Cause.** Each container gets its token when it's created. The browser kept the **old** token in its settings.
- **Fix.**
  1. Get the current token without printing it into shared logs. Keep it in `~/.config/ruview/api_token` (mode 600) and `cat` it in your own terminal. For an existing container, see [SETUP §10.5](SETUP.md#105-restarting-the-old-container).
  2. Dashboard → **Settings → API Access** → paste. Authentication then succeeded.
- **Checks.** `check_stream.sh` exits with status 3 and prints "token rejected (HTTP 401)" when the token is stale. `curl` for `/health` needs no token; `/api/v1/*` does.

## 7. Dashboard not reachable from the laptop

- **Symptom.** `http://localhost:3000/ui/index.html` didn't load in the laptop's browser.
- **Cause.** HTTP and WebSocket are deliberately bound to the **Pi's** `127.0.0.1`. The laptop's `localhost` is a different machine.
- **Fix.** Forward **3000 and 3001** in VS Code Remote SSH: Ports panel → Forward a Port ([SETUP §11](SETUP.md#11-vs-code-port-forwarding)). Forwarding only 3000 loads the page, but the live stream on 3001 stays dead.

## 8. Weak RSSI and noisy visualization

- **Observed.** RSSI was **-65 dBm** during the 60 s UDP capture. Later, `/api/v1/nodes` reported **-81 dBm**; that value is the node's latest single sample, not an average. The dashboard visualization looked **noisy**. That is a qualitative impression; nothing was quantified.
- **Likely contributors** (not yet tested):
  - distance between the node and the Panda adapter, and their orientation;
  - metal or bodies in the path;
  - a moving or handled node;
  - 2.4 GHz interference;
  - RuView has no baseline or calibration for this room;
  - no node position is configured (`position_configured: false`), so any spatial view uses a placeholder position.
- **What to try.** Fix the node in place with a 1–3 m line of sight to the adapter, and note its distance and orientation. Check `iw dev wlan1 info` (channel and txpower). Record an empty-room baseline before judging any "motion" ([NEXT_STEPS](NEXT_STEPS.md)). Don't interpret the dashboard's people or pose graphics as detections.

## 9. Reported packet loss

- **Observed.** The harness UDP capture reported **33.4% loss**, while **2,166 of 2,166** received packets decoded. These are separate numbers: decoding covers only the packets that arrived. Loss is estimated from gaps in the node's sequence numbers, as `gaps / (CSI frames + gaps)`.
- **Inferred, not yet verified: much of the "loss" may be deliberate.** In the v0.8.17 firmware (`csi_collector.c`, tag commit `5728b90`):
  - Every CSI callback takes a sequence number in `csi_serialize_frame()`.
  - The UDP send is rate-limited to **at most one frame per 20 ms** (`CSI_MIN_SEND_INTERVAL_US`).
  - Skipped frames are counted in `s_rate_skip` but never sent, yet their sequence numbers are still used, so they show up as gaps.
- **Consistency check (inferred).** 30.9 Hz delivered ÷ (1 − 0.334) ≈ **46 Hz** implied callback rate. The serial monitor's 27 `CSI cb` lines in 60 s (callbacks #1–3, then every 100th) point to roughly **40–45 callbacks/s**. The two numbers agree, but they came from different runs.
- **Next.** Measure the serial callback rate and the UDP delivery rate at the same time, or read the firmware's send and skip counters, to separate real radio or network loss from rate limiting ([NEXT_STEPS §5](NEXT_STEPS.md#5-analyze-rssi-variation-and-reported-packet-loss)).

## 10. Network isolation and SSH over Tailscale

- **Setup.**
  - The Pi is headless. SSH runs over **Tailscale on `wlan0`**, the built-in Wi-Fi on the venue network.
  - The CSI hotspot runs on a **separate** adapter, `wlan1`.
- **Rules.**
  - Never bind the hotspot profile to `wlan0`. Never run `nmcli` commands against `wlan0`, `tailscale0` or `docker0` while working remotely: one mistake drops your session.
  - The default route must stay on the uplink. `diagnose.sh` fails if it ever points at the CSI interface.
  - HTTP and WebSocket stay on loopback. Reach them by SSH port forwarding, never by publishing on the Tailscale IP.
- **Proposed, not applied.** NetworkManager's `shared` mode NATs hotspot clients out through the uplink, so the ESP32 could reach the internet. The node doesn't need that. A firewall rule that drops forwarding from `wlan1` would isolate it, but it hasn't been tested, so don't apply it remotely without console access.

## 11. Scripts cannot reach Docker

- **Symptom.** `diagnose.sh` prints "cannot reach the Docker daemon, and 'sudo -n' needs a password".
- **Cause.** Docker needs root here. `sudo` on this Pi asks for a password and caches it for a few minutes. The scripts use only `sudo -n`, which never prompts.
- **Fix.** Run `sudo -v`, then re-run the script within a few minutes. Joining the `docker` group also works but grants root-equivalent access. Use `--no-sudo` to stop the scripts from even trying.

## 12. Container exited (255)

- **Observed.** On 2026-11-07, `ruview-live` ran from 12:41 to 12:50 EST and then showed `Exited (255)`. Its restart policy is `no`, so it stayed down.
- **Unverified cause.** Exit code 255 usually means the container was killed rather than shut down cleanly, for example by a reboot, a power cut or a Docker daemon restart. This hasn't been checked.
- **Check.**

  ```bash
  sudo docker inspect ruview-live --format '{{.State.Status}} {{.State.ExitCode}} {{.State.Error}} {{.State.FinishedAt}}'
  sudo docker logs --tail 50 ruview-live 2>&1
  journalctl -b -1 -u docker --no-pager | tail -20
  ```

  The last command shows the previous boot's Docker log, if the Pi rebooted.
