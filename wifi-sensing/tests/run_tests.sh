#!/usr/bin/env bash
# Offline tests for wifi-sensing/scripts. No hardware, Docker, network or root:
# every system command the scripts use is replaced by tests/mock_commands.sh,
# and each test gets a throwaway HOME so real tokens and configs are never read.
#
#   bash wifi-sensing/tests/run_tests.sh
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS=$(cd "$HERE/../scripts" && pwd)
WIFI_DIR=$(cd "$HERE/.." && pwd)
MOCKED=(lsusb ip iw nmcli ss sudo docker curl sleep)
# Obviously fake, but long enough to pass validation.
TOKEN='test-token-not-a-secret-0123456789abcdef'

PASSED=0
FAILED=0
NAME=''
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ----------------------------------------------------------- framework ----

it() { NAME=$1; new_env; }
pass() { PASSED=$((PASSED + 1)); printf 'ok    %s\n' "$NAME"; }
nope() {
  FAILED=$((FAILED + 1))
  printf 'FAIL  %s: %s\n' "$NAME" "$1"
  printf '%s\n' "$OUT" | head -60 | sed 's/^/      | /'
  return 1
}
expect_rc() { [[ $RC == "$1" ]] || nope "exit status $RC, expected $1"; }
expect_out() { grep -qF -- "$1" <<<"$OUT" || nope "output lacks: $1"; }
expect_no_out() { ! grep -qF -- "$1" <<<"$OUT" || nope "output must not contain: $1"; }
expect_call() { grep -qF -- "$1" "$T/calls.log" || nope "expected call not made: $1"; }
expect_no_call() { ! grep -qF -- "$1" "$T/calls.log" || nope "forbidden call made: $1"; }
# Applies to every test: no secret on a command line, no secret dump, no mutation.
expect_safe() {
  expect_no_call "TOKEN_IN_ARGV" && expect_no_call "SECRETS_REQUESTED" && expect_no_out "$TOKEN"
}

new_env() {
  T=$(mktemp -d "$WORK/t.XXXXXX")
  mkdir -p "$T/bin" "$T/home" "$T/s"
  local c
  for c in "${MOCKED[@]}"; do ln -s "$HERE/mock_commands.sh" "$T/bin/$c"; done
  : >"$T/calls.log"
  S=$T/s
  EXTRA_ENV=()
  scenario_healthy
}

# run SCRIPT [args...] -> OUT, RC. Clean environment; stdin is not a terminal.
run() {
  local script=$1
  shift
  OUT=$(cd "$T" && env -i \
    PATH="$T/bin:$PATH" HOME="$T/home" LANG=C.UTF-8 NO_COLOR=1 \
    MOCK_SCENARIO="$S" MOCK_LOG="$T/calls.log" MOCK_STATE="$T" MOCK_TOKEN="$TOKEN" \
    WIFI_SENSING_ENV_FILE="$T/dotenv" \
    "${EXTRA_ENV[@]}" \
    bash "$SCRIPTS/$script" "$@" </dev/null 2>&1)
  RC=$?
}

token_file() { # token_file MODE [CONTENT]
  mkdir -p "$T/home/.config/ruview"
  printf '%s\n' "${2:-$TOKEN}" >"$T/home/.config/ruview/api_token"
  chmod "$1" "$T/home/.config/ruview/api_token"
}

# ----------------------------------------------------------- scenarios ----

nodes_json() { # nodes_json SEQUENCE [CSI_STATUS]
  cat <<EOF
{"nodes":[{"node_id":1,"status":"active","last_seen_ms":40,"csi_status":"${2:-active}",
"csi_last_seen_ms":90,"csi_sequence":$1,"rssi_dbm":-81.0,"motion_level":"present_moving",
"person_count":1,"person_count_valid":true,"position_configured":false,
"mac":"aa:bb:cc:dd:ee:ff","device_id":"a9be7e5bb1644d3a"}],"total":1}
EOF
}

# Pipeline up: hotspot active, ruview-live running with real ESP32 frames.
scenario_healthy() {
  cat >"$S/lsusb" <<'EOF'
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 005: ID 10c4:ea60 Silicon Labs CP210x UART Bridge
Bus 002 Device 004: ID 0e8d:7961 MediaTek Inc. Wireless_Device
Bus 001 Device 007: ID 303a:1001 Espressif USB JTAG/serial debug unit
EOF
  touch "$S/iface_wlan0" "$S/iface_wlan1" "$S/iface_tailscale0"
  echo '5: wlan1    inet 10.42.0.1/24 brd 10.42.0.255 scope global noprefixroute wlan1\       valid_lft forever preferred_lft forever' >"$S/ipv4_wlan1"
  echo 'default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.50 metric 600' >"$S/route_default"
  printf 'Interface wlan1\n\tifindex 5\n\taddr 02:00:00:12:34:56\n\tssid ForgeCSI\n\ttype AP\n\twiphy 1\n\tchannel 6 (2437 MHz), width: 20 MHz\n' >"$S/iw_dev_wlan1"
  printf 'Wiphy phy1\n\tSupported interface modes:\n\t\t * IBSS\n\t\t * managed\n\t\t * AP\n\t\t * AP/VLAN\n\t\t * monitor\n\tBand 1:\n\t\tCapabilities: 0x1ff\n' >"$S/iw_phy_phy1"
  printf 'wlan1\nap\nbg\n6\nshared\n' >"$S/nmcli_conn_ForgeCSI"
  printf 'BASE-X Guest:wlan0\ntailscale0:tailscale0\nForgeCSI:wlan1\n' >"$S/nmcli_active"
  printf 'UNCONN 0      0           10.42.0.1:5005      0.0.0.0:*\n' >"$S/ss_udp"
  printf 'LISTEN 0      4096        127.0.0.1:3000      0.0.0.0:*\nLISTEN 0      4096        127.0.0.1:3001      0.0.0.0:*\n' >"$S/ss_tcp"
  echo sudo >"$S/docker_access"
  printf 'ruview-live\truvnet/wifi-densepose:latest\trunning\t1883/tcp, 8123/tcp, 127.0.0.1:3000-3001->3000-3001/tcp, 10.42.0.1:5005->5005/udp\n' >"$S/docker_ps"
  printf 'RUVIEW_API_TOKEN=%s\nCSI_SOURCE=esp32\nRUVIEW_UDP_ALLOW=172.17.0.1/32\nPATH=/usr/local/sbin:/usr/bin\n' "$TOKEN" >"$S/docker_env_ruview-live"
  echo running >"$S/docker_state_ruview-live"
  echo '172.17.0.1 ' >"$S/docker_gateway"
  touch "$S/docker_image_present" "$S/http_up"
  echo '{"status":"ok","source":"esp32","tick":5012,"clients":1,"processing":{"state":"ok","last_update_age_ms":40,"udp":{"datagrams":81234,"dropped_by_allowlist":0,"last_datagram_age_ms":12}}}' >"$S/http_health"
  nodes_json 58300 >"$S/http_nodes_1"
  nodes_json 58462 >"$S/http_nodes_2"
  echo "$TOKEN" >"$S/valid_token"
  : >"$S/docker_logs"
}

# Hotspot up, nothing running yet: what start_ruview.sh expects.
scenario_fresh() {
  : >"$S/docker_ps"
  : >"$S/ss_udp"
  : >"$S/ss_tcp"
  rm -f "$S/docker_env_ruview-live" "$S/docker_state_ruview-live"
}

# The state of the real Pi on 2026-11-07 after the pause: adapter and ESP32
# unplugged, hotspot down, container exited, nothing listening.
scenario_unplugged() {
  grep -vE '0e8d:7961|303a:' "$S/lsusb" >"$S/lsusb.new" && mv "$S/lsusb.new" "$S/lsusb"
  rm -f "$S/iface_wlan1" "$S/ipv4_wlan1" "$S/iw_dev_wlan1" "$S/http_up"
  printf 'BASE-X Guest:wlan0\ntailscale0:tailscale0\n' >"$S/nmcli_active"
  : >"$S/ss_udp"
  : >"$S/ss_tcp"
  printf 'ruview-live\truvnet/wifi-densepose:latest\texited\t\n' >"$S/docker_ps"
  echo exited >"$S/docker_state_ruview-live"
}

# ======================================================== start_ruview ====

it "start: --help exits 0"
run start_ruview.sh --help
expect_rc 0 && expect_out "Usage:" && expect_out "--start" && pass

it "start: unknown option is a usage error"
run start_ruview.sh --frobnicate
expect_rc 2 && expect_out "unknown option" && expect_no_call "docker run" && pass

it "start: refuses without an API token and starts nothing"
scenario_fresh
run start_ruview.sh --start
expect_rc 3 && expect_out "token file not found" && expect_no_call "docker run" && expect_safe && pass

it "start: refuses a short token"
scenario_fresh
token_file 600 "abc123"
run start_ruview.sh
expect_rc 3 && expect_out "too short" && expect_no_out "abc123" && pass

it "start: refuses a placeholder token"
scenario_fresh
token_file 600 "replace-with-output-of-openssl-rand-hex-32"
run start_ruview.sh
expect_rc 3 && expect_out "placeholder" && pass

it "start: refuses a token file readable by others"
scenario_fresh
token_file 644
run start_ruview.sh
expect_rc 3 && expect_out "chmod 600" && expect_safe && pass

it "start: refuses a symlinked token file"
scenario_fresh
token_file 600
ln -s "$T/home/.config/ruview/api_token" "$T/home/link_token"
run start_ruview.sh --token-file "$T/home/link_token"
expect_rc 3 && expect_out "symlink" && pass

it "start: plan mode prints restrictive bindings and starts nothing"
scenario_fresh
token_file 600
run start_ruview.sh
expect_rc 0 && expect_out "Plan only" &&
  expect_out "127.0.0.1:3000:3000" && expect_out "127.0.0.1:3001:3001" &&
  expect_out "10.42.0.1:5005:5005/udp" && expect_out "RUVIEW_UDP_ALLOW=172.17.0.1/32" &&
  expect_no_out "0.0.0.0:" && expect_no_call "docker run" && expect_no_call "MUTATION" &&
  expect_safe && pass

it "start: --udp-loopback publishes UDP only on loopback (wifi-xray front door) and starts nothing"
scenario_fresh
token_file 600
run start_ruview.sh --udp-loopback 5006
expect_rc 0 && expect_out "Plan only" && expect_out "127.0.0.1:5006:5005/udp" &&
  expect_no_out "10.42.0.1:5005:5005/udp" && expect_out "front door must listen on wlan1 (10.42.0.1:5005)" &&
  expect_out "RUVIEW_UDP_ALLOW=172.17.0.1/32" && expect_no_out "0.0.0.0:" && expect_no_call "docker run" &&
  expect_safe && pass

it "start: --udp-loopback rejects bad ports"
scenario_fresh
token_file 600
run start_ruview.sh --udp-loopback 80
expect_rc 2 && expect_out "1024 to 65535" && expect_no_call "docker run" &&
  { run start_ruview.sh --udp-loopback abc; expect_rc 2; } &&
  { run start_ruview.sh --udp-loopback 70000; expect_rc 2; } && pass

it "start: --udp-loopback is not blocked by the front door already holding the hotspot port"
scenario_fresh
token_file 600
printf 'UNCONN 0      0           10.42.0.1:5005      0.0.0.0:*\n' >"$S/ss_udp"
run start_ruview.sh --udp-loopback 5006
expect_rc 0 && expect_out "no port or name conflicts" &&
  { run start_ruview.sh; expect_rc 1; expect_out "UDP 5005 is already in use"; } && pass

it "start: allowlist follows the detected gateway (not hard-coded)"
scenario_fresh
token_file 600
echo 'fd00::1 172.18.0.1 ' >"$S/docker_gateway"
run start_ruview.sh
expect_rc 0 && expect_out "RUVIEW_UDP_ALLOW=172.18.0.1/32" && expect_no_out "172.17.0.1" && pass

it "start: UDP bind follows the interface address (not hard-coded)"
scenario_fresh
token_file 600
echo '5: wlan1    inet 192.168.50.1/24 brd 192.168.50.255 scope global wlan1' >"$S/ipv4_wlan1"
run start_ruview.sh
expect_rc 0 && expect_out "192.168.50.1:5005:5005/udp" && pass

it "start: fails clearly when the gateway cannot be read"
scenario_fresh
token_file 600
: >"$S/docker_gateway"
run start_ruview.sh
expect_rc 1 && expect_out "could not read the gateway" && pass

it "start: rejects a wildcard --udp-allow"
scenario_fresh
token_file 600
run start_ruview.sh --udp-allow 0.0.0.0/0
expect_rc 2 && expect_out "--udp-allow must be" && expect_no_call "docker run" && pass

it "start: rejects a public --udp-allow"
scenario_fresh
token_file 600
run start_ruview.sh --udp-allow 8.8.8.8/32
expect_rc 2 && pass

it "start: accepts a private --udp-allow override"
scenario_fresh
token_file 600
run start_ruview.sh --udp-allow 10.42.0.0/24
expect_rc 0 && expect_out "RUVIEW_UDP_ALLOW=10.42.0.0/24" && pass

it "start: missing CSI interface fails with advice"
scenario_fresh
token_file 600
scenario_unplugged
run start_ruview.sh
expect_rc 1 && expect_out "wlan1 not found" && expect_no_call "docker run" && pass

it "start: interface without IPv4 asks the user to start the hotspot (never does it)"
scenario_fresh
token_file 600
: >"$S/ipv4_wlan1"
run start_ruview.sh
expect_rc 1 && expect_out "nmcli connection up ForgeCSI" && expect_no_call "MUTATION" && pass

it "start: refuses a public address on the CSI interface"
scenario_fresh
token_file 600
echo '5: wlan1    inet 8.8.4.4/24 scope global wlan1' >"$S/ipv4_wlan1"
run start_ruview.sh
expect_rc 1 && expect_out "not private" && pass

it "start: docker unreachable is explained"
scenario_fresh
token_file 600
echo none >"$S/docker_access"
run start_ruview.sh
expect_rc 1 && expect_out "Docker daemon" && pass

it "start: --no-sudo does not escalate"
scenario_fresh
token_file 600
run start_ruview.sh --no-sudo
expect_rc 1 && expect_no_call "sudo" && pass

it "start: existing same-name container is reported, not removed"
token_file 600
scenario_unplugged
touch "$S/iface_wlan1"
echo '5: wlan1    inet 10.42.0.1/24 scope global wlan1' >"$S/ipv4_wlan1"
run start_ruview.sh --start
expect_rc 1 && expect_out "already exists (exited)" && expect_out "docker start ruview-live" &&
  expect_no_call "docker run" && expect_no_call "MUTATION" && pass

it "start: --replace-existing refuses without a terminal"
token_file 600
scenario_unplugged
touch "$S/iface_wlan1"
echo '5: wlan1    inet 10.42.0.1/24 scope global wlan1' >"$S/ipv4_wlan1"
run start_ruview.sh --replace-existing --start
expect_rc 1 && expect_out "interactive terminal" && expect_no_call "MUTATION" && expect_no_call "docker run" && pass

it "start: another container on the ports is named and left alone (old simulated instance)"
scenario_fresh
token_file 600
printf 'ruview-sim\truvnet/wifi-densepose:latest\trunning\t127.0.0.1:3000-3001->3000-3001/tcp\n' >"$S/docker_ps"
printf 'RUVIEW_API_TOKEN=%s\nCSI_SOURCE=simulated\n' "$TOKEN" >"$S/docker_env_ruview-sim"
run start_ruview.sh --start
expect_rc 1 && expect_out "ruview-sim" && expect_out "CSI_SOURCE=simulated" && expect_out "will not stop it" &&
  expect_no_call "MUTATION" && expect_no_call "docker run" && expect_safe && pass

it "start: host UDP port already in use blocks the start"
scenario_fresh
token_file 600
printf 'UNCONN 0      0           0.0.0.0:5005      0.0.0.0:*\n' >"$S/ss_udp"
run start_ruview.sh --start
expect_rc 1 && expect_out "UDP 5005 is already in use" && expect_no_call "docker run" && pass

it "start: invalid CSI_UDP_PORT in .env is a config error"
scenario_fresh
token_file 600
echo 'CSI_UDP_PORT=abc' >"$T/dotenv"
run start_ruview.sh
expect_rc 2 && expect_out "CSI_UDP_PORT must be" && pass

it "start: a token in .env is ignored and never echoed"
scenario_fresh
printf 'RUVIEW_API_TOKEN=%s\n' "$TOKEN" >"$T/dotenv"
run start_ruview.sh
expect_rc 3 && expect_out "not read from .env" && expect_safe && pass

it "start: .env values are parsed, not executed"
scenario_fresh
token_file 600
# shellcheck disable=SC2016  # the $(...) must reach the file unexpanded
printf 'CSI_IFACE=wlan1\n$(touch %s/pwned)\nRUVIEW_CONTAINER_NAME=ruview-test\n' "$T" >"$T/dotenv"
run start_ruview.sh
if [[ -e $T/pwned ]]; then
  nope ".env was executed"
else
  expect_rc 0 && expect_out "--name ruview-test" && pass
fi

it "start: --start via sudo passes the token by name and preserves it"
scenario_fresh
token_file 600
run start_ruview.sh --start
expect_rc 0 && expect_out "started ruview-live" && expect_call "RUN_TOKEN_ENV=present" &&
  expect_call "-e RUVIEW_API_TOKEN -e CSI_SOURCE=esp32" && expect_call "--preserve-env=RUVIEW_API_TOKEN" &&
  expect_no_call "MUTATION" && expect_safe && pass

it "start: --start with direct docker access needs no sudo"
scenario_fresh
token_file 600
echo direct >"$S/docker_access"
run start_ruview.sh --start
expect_rc 0 && expect_call "RUN_TOKEN_ENV=present" && expect_no_call "sudo" && expect_safe && pass

it "start: token from the environment works and stays hidden"
scenario_fresh
EXTRA_ENV=(RUVIEW_API_TOKEN="$TOKEN")
run start_ruview.sh
expect_rc 0 && expect_out "environment variable" && expect_safe && pass

it "start: warns when the image is not pinned by digest"
scenario_fresh
token_file 600
echo 'RUVIEW_IMAGE=ruvnet/wifi-densepose:latest' >"$T/dotenv"
run start_ruview.sh
expect_rc 0 && expect_out "not pinned by digest" && pass

# ======================================================== check_stream ====

it "stream: --help exits 0"
run check_stream.sh --help
expect_rc 0 && expect_out "CONFIGURED source" && pass

it "stream: healthy pipeline is VERIFIED, output sanitized"
token_file 600
run check_stream.sh --interval 5
expect_rc 0 && expect_out "VERIFIED: real ESP32 CSI frames" && expect_out "CSI_SOURCE=esp32" &&
  expect_out "sequence=advancing seq_delta=162 seq_rate_hz=32.4" &&
  expect_out "position_configured=false" &&
  expect_no_out "aa:bb:cc" && expect_no_out "a9be7e5bb1644d3a" &&
  expect_call "CURL /api/v1/nodes auth=yes code=200" && expect_safe && pass

it "stream: token is read from the container only when asked"
run check_stream.sh
expect_rc 3 && expect_out "no API token" && expect_safe && pass

it "stream: --token-from-container works without printing the token"
run check_stream.sh --token-from-container
expect_rc 0 && expect_out "container environment" && expect_safe && pass

it "stream: stale/wrong token gives the dashboard hint"
token_file 600 "a-different-token-0123456789abcdef0123"
run check_stream.sh
expect_rc 3 && expect_out "token rejected (HTTP 401)" && expect_out "API Access" && expect_no_out "a-different-token" && pass

it "stream: esp32 configured but zero datagrams is NOT VERIFIED and never claims a frame (2026-11-07 regression)"
token_file 600
echo '{"clients":0,"processing":{"last_update_age_ms":null,"state":"idle","udp":{"datagrams":0,"dropped_by_allowlist":0,"last_datagram_age_ms":null}},"source":"esp32","status":"ok","tick":0}' >"$S/http_health"
printf '{"total":0,"nodes":[]}' >"$S/http_nodes_1"
printf '{"total":0,"nodes":[]}' >"$S/http_nodes_2"
run check_stream.sh
expect_rc 1 && expect_out "source_state=waiting" && expect_out "received no CSI datagram" &&
  expect_out "udp_datagrams=0" && expect_out "NOT VERIFIED" && expect_no_out "accepted a frame within" && pass

it "stream: esp32 configured but offline is NOT VERIFIED"
token_file 600
echo '{"status":"ok","source":"esp32:offline","tick":1}' >"$S/http_health"
nodes_json 58300 stale >"$S/http_nodes_1"
nodes_json 58300 stale >"$S/http_nodes_2"
run check_stream.sh
expect_rc 1 && expect_out "esp32:offline" && expect_out "NOT VERIFIED" && pass

it "stream: simulated source is flagged and never VERIFIED"
token_file 600
printf 'RUVIEW_API_TOKEN=%s\nCSI_SOURCE=simulated\n' "$TOKEN" >"$S/docker_env_ruview-live"
echo '{"status":"ok","source":"simulated","tick":1}' >"$S/http_health"
run check_stream.sh
expect_rc 1 && expect_out "SIMULATED" && expect_out "NOT VERIFIED" && pass

it "stream: stalled sequence is NOT VERIFIED"
token_file 600
nodes_json 58300 >"$S/http_nodes_2"
run check_stream.sh
expect_rc 1 && expect_out "sequence=stalled" && expect_out "NOT VERIFIED" && pass

it "stream: unreachable server fails cleanly"
token_file 600
rm -f "$S/http_up"
run check_stream.sh
expect_rc 1 && expect_out "not reachable" && pass

it "stream: refuses to send the token to a non-loopback URL"
token_file 600
run check_stream.sh --url http://10.42.0.1:3000
expect_rc 2 && expect_out "must not leave this host" && expect_no_call "curl" && pass

it "stream: bad --interval is a usage error"
run check_stream.sh --interval 0
expect_rc 2 && pass

it "stream: allowlist drops in the log are surfaced"
token_file 600
printf '2026-11-07T17:42:01Z WARN Dropped UDP frame from disallowed source 172.17.0.1 (allowlist active; total dropped=1)\n' >"$S/docker_logs"
run check_stream.sh
expect_out "disallowed source" && expect_out "172.17.0.1" && pass

# ============================================================ diagnose ====

it "diagnose: --help exits 0"
run diagnose.sh --help
expect_rc 0 && expect_out "Read-only" && pass

it "diagnose: healthy pipeline has no failures"
run diagnose.sh
expect_rc 0 && expect_out "0 failure(s)" && expect_out "Panda PAU0F Wi-Fi adapter present" &&
  expect_out "phy1 supports AP mode" && expect_out "'ForgeCSI' is active on wlan1" &&
  expect_out "CSI_SOURCE=esp32" && expect_out "RUVIEW_API_TOKEN is set (value not shown)" &&
  expect_out "source_state=live" && expect_out "default route via wlan0" && pass

it "diagnose: never prints tokens or MACs, never asks nmcli for secrets, changes nothing"
mkdir -p "$T/root/dev/serial/by-id"
touch "$T/root/dev/ttyACM0"
ln -s ../../ttyACM0 "$T/root/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_AA:BB:CC:11:22:33-if00"
EXTRA_ENV=(DIAG_ROOT="$T/root")
run diagnose.sh
expect_safe && expect_no_out "02:00:00:12" && expect_no_out "AA:BB:CC:11:22:33" &&
  expect_out "<mac>" && expect_out "serial port: /dev/ttyACM0" && expect_no_call "MUTATION" && pass

it "diagnose: unplugged hardware (today's real state) fails without crashing"
scenario_unplugged
run diagnose.sh
expect_rc 1 && expect_out "Panda PAU0F adapter (0e8d:7961) not found" && expect_out "wlan1 does not exist" &&
  expect_out "ruview-live  image=ruvnet/wifi-densepose:latest  state=exited" &&
  expect_out "not reachable" && expect_out "Summary" && expect_no_call "MUTATION" && pass

it "diagnose: wildcard UDP and network-exposed HTTP are warned about"
printf 'UNCONN 0      0           0.0.0.0:5005      0.0.0.0:*\n' >"$S/ss_udp"
printf 'LISTEN 0      4096        0.0.0.0:3000      0.0.0.0:*\n' >"$S/ss_tcp"
run diagnose.sh
expect_out "listens on ALL interfaces" && expect_out "reachable from the network" && pass

it "diagnose: simulated container and allowlist mismatch are flagged"
printf 'RUVIEW_API_TOKEN=%s\nCSI_SOURCE=simulated\nRUVIEW_UDP_ALLOW=10.42.0.0/24\n' "$TOKEN" >"$S/docker_env_ruview-live"
run diagnose.sh
expect_out "SYNTHETIC data" && expect_out "does not cover the Docker gateway 172.17.0.1" && expect_safe && pass

it "diagnose: allowlist CIDR that covers the gateway is accepted"
printf 'CSI_SOURCE=esp32\nRUVIEW_UDP_ALLOW=172.17.0.0/16\n' >"$S/docker_env_ruview-live"
run diagnose.sh
expect_no_out "does not cover" && expect_out "RUVIEW_API_TOKEN is not set" && pass

it "diagnose: hotspot on the wrong interface or band is warned about"
printf 'wlan0\nap\na\n36\nshared\n' >"$S/nmcli_conn_ForgeCSI"
run diagnose.sh
expect_out "bound to 'wlan0'" && expect_out "2.4 GHz only" && pass

it "diagnose: default route through the hotspot is a failure"
echo 'default via 10.42.0.1 dev wlan1 proto static' >"$S/route_default"
run diagnose.sh
expect_rc 1 && expect_out "default route goes through wlan1" && pass

it "diagnose: firmware bundle checksums are verified read-only"
mkdir -p "$T/home/ruview-firmware-c5/c5-bundle"
(cd "$T/home/ruview-firmware-c5/c5-bundle" && printf 'fake' >app.img && echo 0.8.17 >version.txt &&
  sha256sum app.img version.txt >SHA256SUMS.txt)
run diagnose.sh
expect_out "firmware bundle checksums match" && expect_out "version 0.8.17" && pass

it "diagnose: tampered firmware bundle is a failure"
mkdir -p "$T/home/ruview-firmware-c5/c5-bundle"
(cd "$T/home/ruview-firmware-c5/c5-bundle" && printf 'fake' >app.img && sha256sum app.img >SHA256SUMS.txt && printf 'evil' >app.img)
run diagnose.sh
expect_rc 1 && expect_out "checksum mismatch" && pass

it "diagnose: world-readable flash backups are warned about (contents not read)"
mkdir -p "$T/home/esp-backups"
printf 'x' >"$T/home/esp-backups/esp32c5-original-8mb.bin"
chmod 664 "$T/home/esp-backups/esp32c5-original-8mb.bin"
run diagnose.sh
expect_out "readable by other users" && pass

# =========================================================== redaction ====

it "lib: redact masks MACs, bearer tokens, key=value secrets and long hex"
# shellcheck disable=SC2016  # the inner script is expanded by the child bash
OUT=$(cd "$T" && env -i PATH="$PATH" HOME="$T/home" RUVIEW_API_TOKEN="$TOKEN" bash -c '
  . "$1/lib.sh"
  printf "%s\n" "mac 02:00:00:12:34:56" "Authorization: Bearer abc.def-123" \
    "psk=hunter2hunter2" "\"password\": \"s3cret\"" "RUVIEW_API_TOKEN=xyz" \
    "id 0123456789abcdef0123456789abcdef01234567" "digest sha256:0123456789abcdef0123456789abcdef01234567" \
    "token value $2 inline" | redact' _ "$SCRIPTS" "$TOKEN")
expect_no_out "02:00:00" && expect_no_out "abc.def-123" && expect_no_out "hunter2" && expect_no_out "s3cret" &&
  expect_no_out "xyz" && expect_out "id <redacted-hex>" && expect_out "sha256:0123456789abcdef" &&
  expect_no_out "$TOKEN" && pass

it "lib: IPv4 / allowlist helpers"
# shellcheck disable=SC2016  # the inner script is expanded by the child bash
OUT=$(bash -c '
  . "$1/lib.sh"
  for c in "is_private_ipv4 10.42.0.1" "is_private_ipv4 172.17.0.1" "! is_private_ipv4 172.32.0.1" \
           "! is_private_ipv4 100.64.0.1" "! is_private_ipv4 127.0.0.1" "! is_private_ipv4 0.0.0.0" \
           "! is_ipv4 10.42.0.256" "is_safe_allow_cidr 172.17.0.1/32" "! is_safe_allow_cidr 0.0.0.0/0" \
           "! is_safe_allow_cidr 10.0.0.0/4" "ip_in_allowlist 172.17.0.1 10.0.0.0/8,172.17.0.0/16" \
           "! ip_in_allowlist 172.18.0.1 172.17.0.0/16" "is_port 5005" "! is_port 70000"; do
    eval "$c" || echo "BROKEN: $c"
  done' _ "$SCRIPTS")
expect_no_out "BROKEN" && pass

# ========================================================= repo hygiene ====

if git -C "$WIFI_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  it "repo: secrets, backups and captures are ignored; sources are not"
  OUT=''
  bad=''
  for p in .env .env.local api_token esp32-provision-state/x.json nvs_creds.bin backups/esp32c5-original-8mb.bin \
    captures/run1.jsonl logs/ruview.log recordings/a.csv .venv/bin/python; do
    git -C "$WIFI_DIR" check-ignore -q "$p" || bad+=" should-ignore:$p"
  done
  for p in .env.example scripts/diagnose.sh scripts/check_stream.sh scripts/start_ruview.sh scripts/lib.sh \
    scripts/ruview_json.py tests/run_tests.sh README.md; do
    ! git -C "$WIFI_DIR" check-ignore -q "$p" || bad+=" must-not-ignore:$p"
  done
  OUT=$bad
  if [[ -z $bad ]]; then pass; else nope "ignore rules wrong:$bad"; fi

  it "repo: .env.example holds no secrets"
  OUT=$(grep -nE '^[[:space:]]*(RUVIEW_API_TOKEN|[A-Z_]*(PASSWORD|PSK|SECRET))=' "$WIFI_DIR/.env.example")
  if [[ -z $OUT ]]; then pass; else nope ".env.example sets a secret-looking key"; fi
fi

# ============================================================== python ====

it "python: unit tests (ruview_json; wifi-xray, synthetic CSI, skipped without numpy)"
OUT=$(PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$HERE" -p 'test_*.py' 2>&1)
RC=$?
expect_rc 0 && pass

printf '\n%d passed, %d failed\n' "$PASSED" "$FAILED"
((FAILED == 0))
