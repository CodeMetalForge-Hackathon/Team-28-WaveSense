# Wi-Fi CSI setup: Raspberry Pi 5 + Panda PAU0F + ESP32-C5 + RuView

How to rebuild the pipeline described in [README.md](README.md). Every command was used on our Pi on 2026-11-07 unless a step says **not run by us**. Addresses such as `10.42.0.1`, and names such as `wlan1` and `ForgeCSI`, are what we observed, **not constants**: check yours at each step.

**Before you start:**

- The working ESP32-C5 does **not** need flashing. Sections 6 to 8 are for a **new** board.
- Only test with informed, consenting people in radio range.
- Never paste tokens, Wi-Fi passwords or `esptool` output (which includes the MAC) into chat, issues or screenshots.

## Contents

1. [Raspberry Pi requirements](#1-raspberry-pi-requirements)
2. [USB Wi-Fi adapter detection](#2-usb-wi-fi-adapter-detection)
3. [NetworkManager hotspot](#3-networkmanager-hotspot)
4. [ESP32-C5 identification](#4-esp32-c5-identification)
5. [Firmware release and checksums](#5-firmware-release-and-checksums)
6. [Flash backup](#6-flash-backup)
7. [Flash-plan verification](#7-flash-plan-verification-dry-run)
8. [Optional: guarded flashing](#8-optional-guarded-flashing-new-boards-only)
9. [Wi-Fi provisioning](#9-wi-fi-provisioning)
10. [Docker live receiver](#10-docker-live-receiver)
11. [VS Code port forwarding](#11-vs-code-port-forwarding)
12. [Dashboard authentication](#12-dashboard-authentication)
13. [Verifying the stream](#13-verifying-the-stream)
14. [Common commands](#14-common-commands)

---

## 1. Raspberry Pi requirements

| What | Version we used |
|---|---|
| Board | Raspberry Pi 5, 16 GB |
| OS | Raspberry Pi OS 64-bit (Debian 13 "trixie"), kernel 6.18.50+rpt-rpi-2712, `aarch64` |
| Docker | `docker.io` 26.1.5+dfsg1 (Debian package) |
| Node.js | v20.19.2 (the RuView harness needs >= 20 and has **no npm dependencies**) |
| Python | 3.13.5, with a venv at `~/esp-tools` |
| esptool | 5.6.0 (upstream requires >= 5.3 for the C5) |
| esp-idf-nvs-partition-gen | 0.3.0 (used by `provision.py`) |
| RuView source | `~/ruview-source`, commit `1ec7f3a5e009915dc05456553c0d2f6f89ea33be` (main, 2026-11-06) |

```bash
sudo apt install -y docker.io iw usbutils curl git nodejs python3-venv openssl
python3 -m venv ~/esp-tools
~/esp-tools/bin/pip install 'esptool>=5.3' esp-idf-nvs-partition-gen
git clone https://github.com/ruvnet/RuView.git ~/ruview-source
git -C ~/ruview-source checkout 1ec7f3a5e009915dc05456553c0d2f6f89ea33be   # the commit we used
```

- The **harness CLI** (`node harness/ruview/bin/cli.js ...`) and `provision.py` come from that source checkout. The **firmware** comes from a separate release; see section 5.
- **Docker needs root on this Pi.** Our user is not in the `docker` group, and `sudo` asks for a password. The scripts in `wifi-sensing/` only use `sudo -n` and never prompt, so run `sudo -v` first. Adding yourself to the `docker` group also works, but that group is root-equivalent.
- Don't vendor RuView into this repo, and don't commit its firmware binaries. Link to the release and record checksums instead.

## 2. USB Wi-Fi adapter detection

```bash
lsusb | grep 0e8d:7961                          # Panda PAU0F (MediaTek)
iw dev                                          # the new interface; ours was wlan1
readlink /sys/class/net/wlan1/device/driver     # ...mt7921u
PHY=$(iw dev wlan1 info | awk '$1=="wiphy"{print "phy"$2}')
iw phy "$PHY" info | sed -n '/Supported interface modes/,/Band/p'   # must list "* AP"
```

If the adapter enumerates under another name, set `CSI_IFACE` in `wifi-sensing/.env` (see `.env.example`).

## 3. NetworkManager hotspot

Profile as observed on our Pi (`nmcli -g ... connection show ForgeCSI`, secrets not requested):

| Setting | Value |
|---|---|
| `connection.interface-name` | `wlan1` (**never** `wlan0`: that would drop the uplink and your SSH session) |
| `802-11-wireless.ssid` | `ForgeCSI` |
| `802-11-wireless.mode` | `ap` |
| `802-11-wireless.band` / `channel` | `bg` / `6` (the C5 firmware profile associates on 2.4 GHz only) |
| `ipv4.method` | `shared` (NetworkManager runs DHCP and NAT; it gave `10.42.0.1/24`) |
| `802-11-wireless-security.key-mgmt` | `wpa-psk` |
| `connection.autoconnect` | `no` (the hotspot only runs when someone starts it) |

To create the same profile on another Pi (**not run by us** in this form, and it may need `sudo`):

```bash
nmcli connection add type wifi ifname wlan1 con-name ForgeCSI autoconnect no ssid ForgeCSI \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
  ipv4.method shared wifi-sec.key-mgmt wpa-psk
read -rsp 'Hotspot password (8-63 characters): ' PSK; echo
nmcli connection modify ForgeCSI wifi-sec.psk "$PSK"; unset PSK
nmcli connection up ForgeCSI
ip -4 addr show wlan1          # note this address: nodes must be provisioned with it
```

`read -s` keeps the password out of the terminal and out of shell history. It still appears briefly in `nmcli`'s arguments, where other local users could see it in `ps`, which is acceptable on a single-user Pi.

## 4. ESP32-C5 identification

Identify every board electronically before flashing anything. A label or listing is not enough: our "ESP32-S3" was a C5.

```bash
lsusb | grep 303a:1001                                  # Espressif native USB-Serial/JTAG
ls -l /dev/serial/by-id/ | grep -i espressif            # → ../../ttyACM0
~/esp-tools/bin/esptool --port /dev/ttyACM0 chip-id     # must say ESP32-C5 (ours: revision v1.0)
~/esp-tools/bin/esptool --port /dev/ttyACM0 flash-id    # ours: 8 MB
```

`chip-id` prints the board's MAC address, so don't share that output. Boards behind a CP210x or CH34x USB-UART bridge show up as `/dev/ttyUSB*`. The RPLIDAR on this robot also uses a CP2102N, so don't assume a `ttyUSB` port is an ESP32.

## 5. Firmware release and checksums

| | |
|---|---|
| Release | [`v0.8.17-esp32`](https://github.com/ruvnet/RuView/releases/tag/v0.8.17-esp32), "ESP32 firmware v0.8.17 — experimental C5 support", published 2026-10-29 |
| Tag commit | `5728b90949e60e3f908d996997d453147c1794e0` |
| Asset | `esp32-csi-node-firmware-c5-4mb.tar.gz`, 698,446 bytes |
| Asset SHA-256 | `6050ea1cf804ffad4a15db58561ac7178c3ec041345bf228009b702bb28b49bb` |
| Layout | C5-specific **4 MB** partition layout, used on our **8 MB** C5. Upstream documents the 4 MB OTA layout as the C5 profile |

```bash
mkdir -p ~/ruview-firmware-c5 && cd ~/ruview-firmware-c5
base=https://github.com/ruvnet/RuView/releases/download/v0.8.17-esp32
curl -fL -O "$base/esp32-csi-node-firmware-c5-4mb.tar.gz" -O "$base/SHA256SUMS.txt"
grep ' esp32-csi-node-firmware-c5-4mb.tar.gz$' SHA256SUMS.txt | sha256sum -c -
echo '6050ea1cf804ffad4a15db58561ac7178c3ec041345bf228009b702bb28b49bb  esp32-csi-node-firmware-c5-4mb.tar.gz' | sha256sum -c -
mkdir -p c5-bundle && tar -xzf esp32-csi-node-firmware-c5-4mb.tar.gz -C c5-bundle   # flat archive
(cd c5-bundle && sha256sum -c SHA256SUMS.txt)                                       # all must say OK
```

Contents of `c5-bundle/SHA256SUMS.txt`, re-verified on 2026-11-07:

| File | Bytes | SHA-256 |
|---|---|---|
| `bootloader.bin` | 19,792 | `9fe21d0e53f64a8f88fd4611be7b542a11092b83f1b9f73c03af1a8e9cf441c8` |
| `partition-table-c5.bin` | 3,072 | `4c2cc4ffd52641e23b779bd57b3908014083ac3c1aab395756478c89e70d81f0` |
| `ota_data_initial.bin` | 8,192 | `7d2c7ac4888bfd75cd5f56e8d61f69595121183afc81556c876732fd3782c62f` |
| `esp32-csi-node-c5.bin` | 1,101,424 | `8f112479d8f4164afa7201ff5511b353e5b79ec8958bf9a0e8dae25b169fbc66` |
| `version.txt` (`0.8.17`) | 7 | `a8c64c5df539331e33b8b4b5c1534d12f6238dbbbd313e7cebbf1cff1df0fe87` |

`wifi-sensing/scripts/diagnose.sh` re-checks these hashes every time it runs.

## 6. Flash backup

Always take a full backup of a board before its first flash. The original flash may hold the factory image or credentials, so it **must never go into git** and should be readable only by you.

```bash
install -d -m 700 ~/esp-backups
~/esp-tools/bin/esptool --port /dev/ttyACM0 read-flash 0 0x800000 ~/esp-backups/BOARD-original-8mb.bin
# Verify: read it a second time and compare, before flashing anything.
~/esp-tools/bin/esptool --port /dev/ttyACM0 read-flash 0 0x800000 ~/esp-backups/BOARD-reread.bin
cmp ~/esp-backups/BOARD-original-8mb.bin ~/esp-backups/BOARD-reread.bin && echo "backup verified"
chmod 600 ~/esp-backups/*.bin
```

**Our board.** `~/esp-backups/esp32c5-original-8mb.bin` is 8,388,608 bytes. A separate 64 KiB re-read (`verify-first-64k.bin`) matches its first 64 KiB byte for byte (`cmp`, 2026-11-07). The full 8 MB image was **never compared against a second read**, and since the board has been flashed, that comparison is no longer possible. Treat the backup as plausible, not proven.

## 7. Flash-plan verification (dry run)

Run from `~/ruview-source`. This writes nothing: it resolves the bundle, checks every image against `SHA256SUMS.txt` and prints the esptool plan.

```bash
cd ~/ruview-source
node harness/ruview/bin/cli.js flash-plan \
  --port /dev/ttyACM0 \
  --bundle ~/ruview-firmware-c5/c5-bundle \
  --variant c5
```

Result on our board: **all 4 image integrity checks passed**, with this plan:

| Offset | Image |
|---|---|
| `0x2000` | Bootloader (C5-specific offset; S3 uses `0x0`) |
| `0x8000` | Partition table |
| `0xF000` | OTA data |
| `0x20000` | Application |

## 8. Optional: guarded flashing (new boards only)

**Manual, deliberate, one board at a time. Never script this, and never repeat it on the working C5.**

Before you write flash, check all of these:

- [ ] `esptool chip-id` shows the chip the bundle was built for (C5 bundle → ESP32-C5 only).
- [ ] A full backup exists, was verified with `cmp` and is mode 600 (section 6).
- [ ] `flash-plan` passed every integrity check (section 7).
- [ ] You accept that the board's current firmware will be overwritten.

```bash
cd ~/ruview-source
# Without --confirm this is a dry run that prints the plan and writes nothing.
node harness/ruview/bin/cli.js flash --port /dev/ttyACM0 --bundle ~/ruview-firmware-c5/c5-bundle --variant c5
# The write. The harness detects the chip and refuses on a mismatch.
node harness/ruview/bin/cli.js flash --port /dev/ttyACM0 --bundle ~/ruview-firmware-c5/c5-bundle --variant c5 --confirm
```

The exact write command used for our board on 2026-11-07 was not recorded. The steps above are the guarded path in the harness we used (`harness/ruview/src/firmware.js`): it is dry-run unless `--confirm` is given, and it refuses a chip mismatch.

## 9. Wi-Fi provisioning

Firmware alone does nothing. **Until it's provisioned, the node logs no CSI** (we saw exactly that). Provisioning writes the hotspot SSID, the password and the receiver address into the board's NVS partition.

### 9.1 Your own board, with an interactive password prompt

```bash
ip -4 addr show wlan1      # find YOUR receiver address (ours: 10.42.0.1)
cd ~/ruview-source/firmware/esp32-csi-node
~/esp-tools/bin/python provision.py --port /dev/ttyACM0 --chip esp32c5 \
  --ssid ForgeCSI --target-ip 10.42.0.1 --target-port 5005 --node-id 1
# provision.py asks:  WiFi password for ForgeCSI:   (typed blind, not saved in shell history)
```

- **Use the venv's Python** (`~/esp-tools/bin/python`). `provision.py` runs esptool and the NVS generator through its own interpreter.
- `--target-ip` must be the IPv4 address of the hotspot interface on the receiving Pi. `10.42.0.1` is only NetworkManager's usual default.
- Give every board its own `--node-id` (the default is 1).
- **Never pass `--password` on the command line**: it lands in shell history and in `ps`. Don't use `--show-secrets` either. To see what would be written, with secrets hidden, run `provision.py --port /dev/ttyACM0 --state`.
- The password prompt only appears on a terminal, and only when no password is cached for that SSID.

### 9.2 What our run did

These are the settings we used. **NVS partition written, hash verified, state saved.**

| Setting | Value |
|---|---|
| SSID | `ForgeCSI` |
| Target IP | `10.42.0.1` |
| CSI UDP port | `5005` |
| OTA | **Disabled.** No OTA key was provisioned, so the firmware refuses OTA updates; update over USB instead |
| Local state | `~/.config/wifi-densepose/esp32-provision-state/` (0700, files 0600). **It contains the Wi-Fi password in clear.** Never copy it into the repo |

### 9.3 Rotating the hotspot password

The current password appeared in an earlier screenshot, so **rotate it before anyone else uses this network.**

```bash
read -rsp 'New hotspot password: ' PSK; echo
nmcli connection modify ForgeCSI wifi-sec.psk "$PSK"; unset PSK
# --reset makes provision.py forget the cached old password and prompt again.
# It forgets the other cached settings too, so pass them all.
cd ~/ruview-source/firmware/esp32-csi-node
~/esp-tools/bin/python provision.py --reset --port /dev/ttyACM0 --chip esp32c5 \
  --ssid ForgeCSI --target-ip 10.42.0.1 --target-port 5005 --node-id 1
nmcli connection up ForgeCSI       # restart the hotspot with the new password
```

Without `--reset`, `provision.py` silently **reuses the cached old password**, because the SSID hasn't changed.

## 10. Docker live receiver

### 10.1 One-time token

The container refuses to start without `RUVIEW_API_TOKEN` (upstream entrypoint, exit code 64).

```bash
install -d -m 700 ~/.config/ruview
(umask 077; openssl rand -hex 32 > ~/.config/ruview/api_token)
```

### 10.2 Start (helper)

```bash
nmcli connection up ForgeCSI                    # you bring the hotspot up; no script does it
sudo -v                                         # cache sudo for the 'sudo -n docker' calls
wifi-sensing/scripts/start_ruview.sh            # checks everything, prints the plan, starts nothing
wifi-sensing/scripts/start_ruview.sh --start    # actually starts it
```

The helper finds the `wlan1` address and the Docker bridge gateway itself (neither is hard-coded). It refuses to run if any of these is true:

- a token is missing, weak, or in a file others can read;
- another container or process holds 3000, 3001 or 5005;
- a container named `ruview-live` already exists.

It never stops other containers. See `start_ruview.sh --help`.

### 10.3 Start (manual equivalent)

This is the command the helper builds, and matches what ran on 2026-11-07:

```bash
export RUVIEW_API_TOKEN="$(cat ~/.config/ruview/api_token)"
CSI_IP=$(ip -4 -o addr show dev wlan1 | awk '{split($4,a,"/"); print a[1]}')
GW=$(sudo docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' | awk '{print $1}')
sudo --preserve-env=RUVIEW_API_TOKEN docker run -d --name ruview-live --restart no \
  -p 127.0.0.1:3000:3000 -p 127.0.0.1:3001:3001 -p "$CSI_IP:5005:5005/udp" \
  -e RUVIEW_API_TOKEN -e CSI_SOURCE=esp32 -e RUVIEW_UDP_ALLOW="$GW/32" \
  ruvnet/wifi-densepose@sha256:73dd3f626aef369ea4542d353d4aeb227c156d8b07d034b6a4fe697e96bd57bc
```

- `-e RUVIEW_API_TOKEN` with no `=value` passes the token **by name**, so it never shows up in `ps`. Under `sudo` it needs `--preserve-env=RUVIEW_API_TOKEN`, because `sudo` resets the environment.
- **UDP is bound to the hotspot address only**, never `0.0.0.0`. The UDP data plane has no authentication.
- **HTTP and WebSocket are bound to loopback only.** Reach them through port forwarding (section 11).
- **Why the allowlist names the Docker gateway:** see [TROUBLESHOOTING.md, "disallowed source"](TROUBLESHOOTING.md#5-docker-allowlist-drops-frames-disallowed-source).

### 10.4 The container as it was configured

Read on 2026-11-07 with `docker inspect`; environment values other than these two were not printed.

| Setting | Value |
|---|---|
| Image | `ruvnet/wifi-densepose:latest`, which then resolved to digest `sha256:73dd3f626aef369ea4542d353d4aeb227c156d8b07d034b6a4fe697e96bd57bc` (image ID `12ccd3c65626`, built 2026-11-06) |
| Port bindings | `127.0.0.1:3000→3000/tcp`, `127.0.0.1:3001→3001/tcp`, `10.42.0.1:5005→5005/udp` |
| Environment | `CSI_SOURCE=esp32`, `RUVIEW_UDP_ALLOW=172.17.0.1/32`, `RUVIEW_API_TOKEN` set |
| Network / restart | `bridge` / `no` |
| Last run | Started 12:41 EST, exited (255) 12:50 EST on 2026-11-07 |

`:latest` is a moving tag, so these docs pin the digest. To check what you would get: `sudo docker pull ruvnet/wifi-densepose@sha256:73dd3f62...`.

### 10.5 Restarting the old container

`sudo docker start ruview-live` reuses the container's **old** token. Two ways to use it:

- **Recommended:** `wifi-sensing/scripts/check_stream.sh --token-from-container`. The script reads the token without printing it.
- **Only in a private terminal, never in a shared screen or log:**
  `sudo docker inspect ruview-live --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^RUVIEW_API_TOKEN=//p'`

## 11. VS Code port forwarding

The dashboard only listens on the Pi's loopback, so forward it to your laptop:

- **VS Code Remote SSH:** Ports panel → **Forward a Port** → `3000`, then again for `3001`. The second one carries the dashboard's WebSocket.
- **Plain SSH:** `ssh -L 3000:127.0.0.1:3000 -L 3001:127.0.0.1:3001 adritforge@<pi-tailscale-name>`

Then open **http://localhost:3000/ui/index.html** on the laptop. Don't "fix" access by publishing 3000 or 3001 on `0.0.0.0` or on the Tailscale address.

## 12. Dashboard authentication

1. In your own terminal on the Pi, run `cat ~/.config/ruview/api_token` and copy the value. Avoid screen shares and screenshots while you do it.
2. Dashboard → **Settings → API Access** → paste → save.
3. **Every time the container is recreated, it has a new token.** The browser keeps the old one and gets HTTP 401, so paste the new one.

`/health` and the UI files load without a token. Everything under `/api/v1/` and the WebSocket streams needs one.

## 13. Verifying the stream

```bash
wifi-sensing/scripts/check_stream.sh            # configured vs effective source, then two /api/v1/nodes samples
```

Upstream harness checks, run from `~/ruview-source`:

```bash
node harness/ruview/bin/cli.js monitor --port /dev/ttyACM0 --seconds 60   # serial console: counts "CSI cb #N" lines
node harness/ruview/bin/cli.js esp32 --udp-port 5005 --seconds 60        # host UDP capture: packets, rate, loss, RSSI
```

The harness `esp32` capture and the container **can't both own UDP 5005**. Run captures only while `ruview-live` is stopped ([TROUBLESHOOTING §3](TROUBLESHOOTING.md#3-udp-port-already-in-use)).

## 14. Common commands

```bash
wifi-sensing/scripts/diagnose.sh                       # read-only overview, sanitized
nmcli -t -f NAME,DEVICE connection show --active       # is ForgeCSI up on wlan1?
ip -4 addr show wlan1; ip route show default           # receiver address; uplink must not be wlan1
ss -ulpn 'sport = :5005'; ss -tlpn '( sport = :3000 or sport = :3001 )'
sudo docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
sudo docker logs --since 10m ruview-live 2>&1 | grep -iE 'disallowed|error|warn' | tail
curl -s http://127.0.0.1:3000/health                   # no token needed
```

Never run `docker inspect` without `--format` in a shared terminal: the default output includes the API token.
