#!/usr/bin/env bash
# Read-only health check for the Wi-Fi CSI / RuView pipeline.
# It never starts, stops or reconfigures anything.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--no-sudo] [--no-docker] [--no-http] [-h]

Read-only checks of the Wi-Fi CSI pipeline: USB adapters, ESP32 serial port,
the hotspot, UDP/HTTP ports, the RuView container and its /health endpoint.
Nothing is started, stopped or changed. Tokens, Wi-Fi passwords and MAC
addresses are never printed.

  --no-sudo    never try 'sudo -n docker' (by default it is used only if it
               works without a password prompt)
  --no-docker  skip the Docker checks
  --no-http    skip the RuView HTTP /health check
  -h, --help   show this help

Settings come from the environment or wifi-sensing/.env (see .env.example).
Exit status: 0 = no failures, 1 = at least one failure, 2 = usage error.
EOF
}

DO_DOCKER=1
DO_HTTP=1
while (($#)); do
  case $1 in
    --no-sudo) ALLOW_SUDO=0 ;;
    --no-docker) DO_DOCKER=0 ;;
    --no-http) DO_HTTP=0 ;;
    -h | --help) usage; exit 0 ;;
    *) err "unknown option: $1"; usage >&2; exit 2 ;;
  esac
  shift
done

load_config
validate_config
ROOT=${DIAG_ROOT:-} # test hook: prefix for /dev and /sys

check_host() {
  section "Host"
  local os
  os=$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null | tr -d '"')
  info "OS: ${os:-unknown}; kernel $(uname -r); arch $(uname -m)"
  [[ $(uname -m) == aarch64 ]] || warn "not aarch64; these scripts were only tested on Raspberry Pi OS 64-bit"
}

check_usb() {
  section "USB devices"
  if ! command -v lsusb >/dev/null 2>&1; then
    warn "lsusb not found (sudo apt install usbutils)"
    return
  fi
  local usb other
  usb=$(lsusb 2>/dev/null)
  if grep -qi "ID $PANDA_USB_ID" <<<"$usb"; then
    ok "Panda PAU0F Wi-Fi adapter present ($PANDA_USB_ID)"
  else
    fail "Panda PAU0F adapter ($PANDA_USB_ID) not found. Without it there is no $CSI_IFACE hotspot"
  fi
  if grep -qi "ID $ESP32_USB_ID" <<<"$usb"; then
    ok "Espressif USB-Serial/JTAG device present ($ESP32_USB_ID)"
  else
    warn "no Espressif $ESP32_USB_ID device on USB. Fine if the ESP32-C5 is on a separate USB power supply: it streams over Wi-Fi, not USB"
  fi
  other=$(grep -i 'ID 303a:' <<<"$usb" | grep -vi "ID $ESP32_USB_ID" | sed -E 's/^.*ID /ID /')
  if [[ -n $other ]]; then info "other Espressif devices: $other"; fi
  if grep -qiE 'ID (10c4:ea60|1a86:(7523|55d4))' <<<"$usb"; then
    info "USB-UART bridges present (CP210x/CH34x). Some ESP32 dev boards use these, but so does the RPLIDAR (CP2102N). Identify a board with 'esptool chip-id' before flashing anything"
  fi
}

check_serial() {
  section "Serial ports"
  local p l found=0
  for p in "$ROOT"/dev/ttyACM*; do
    [[ -e $p ]] || continue
    found=1
    info "serial port: ${p#"$ROOT"}"
  done
  ((found)) || info "no /dev/ttyACM* ports (ESP32-C5 not connected over USB)"
  for l in "$ROOT"/dev/serial/by-id/*; do
    [[ -e $l ]] || continue
    printf '       by-id: %s -> %s\n' "$(basename "$l")" "$(basename "$(readlink "$l")")" | redact
  done
}

check_wifi() {
  section "Wi-Fi interfaces"
  if ! iface_exists "$CSI_IFACE"; then
    fail "$CSI_IFACE does not exist (Panda adapter unplugged? set CSI_IFACE if it was renamed)"
    return
  fi
  local devinfo phy type driver ip4
  devinfo=$(iw dev "$CSI_IFACE" info 2>/dev/null)
  phy=$(awk '$1 == "wiphy" { print "phy" $2; exit }' <<<"$devinfo")
  type=$(awk '$1 == "type" { print $2; exit }' <<<"$devinfo")
  ok "$CSI_IFACE exists (${phy:-phy unknown}, type ${type:-unknown})"
  if [[ -n $phy ]]; then
    if iw phy "$phy" info 2>/dev/null |
      awk '/Supported interface modes:/ { f = 1; next } f && /^[[:space:]]+\* / { print $2; next } f { exit }' |
      grep -qx AP; then
      ok "$phy supports AP mode"
    else
      fail "$phy does not list AP under 'Supported interface modes'"
    fi
  fi
  driver=$(readlink "$ROOT/sys/class/net/$CSI_IFACE/device/driver" 2>/dev/null)
  if [[ -n $driver ]]; then info "$CSI_IFACE driver: $(basename "$driver")"; fi
  ip4=$(iface_ipv4 "$CSI_IFACE")
  if [[ -n $ip4 ]]; then
    ok "$CSI_IFACE IPv4 $ip4 (nodes must be provisioned with --target-ip $ip4)"
  else
    warn "$CSI_IFACE has no IPv4 address; the hotspot is probably down"
  fi
}

check_hotspot() {
  section "Hotspot ($HOTSPOT_CONN)"
  if ! command -v nmcli >/dev/null 2>&1; then
    warn "nmcli not found; is NetworkManager installed?"
    return
  fi
  local fields ifname mode band channel method
  # Non-secret fields only. Never add --show-secrets here.
  if ! fields=$(nmcli -t -g connection.interface-name,802-11-wireless.mode,802-11-wireless.band,802-11-wireless.channel,ipv4.method \
    connection show "$HOTSPOT_CONN" 2>/dev/null); then
    fail "NetworkManager profile '$HOTSPOT_CONN' not found (docs/wifi-csi/SETUP.md, section 3)"
    return
  fi
  { read -r ifname; read -r mode; read -r band; read -r channel; read -r method; } <<<"$fields"
  info "profile: interface=${ifname:-any} mode=${mode:-?} band=${band:-?} channel=${channel:-auto} ipv4.method=${method:-?}"
  [[ $mode == ap ]] || fail "'$HOTSPOT_CONN' is not an AP profile (mode=${mode:-unset})"
  [[ $ifname == "$CSI_IFACE" ]] ||
    warn "'$HOTSPOT_CONN' is bound to '${ifname:-any interface}', expected $CSI_IFACE. A hotspot on wlan0 would take down the uplink and SSH"
  [[ $band == bg ]] || warn "band is '${band:-auto}'; the ESP32-C5 firmware profile associates on 2.4 GHz only (band bg)"
  if nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep -Fxq "$HOTSPOT_CONN:$CSI_IFACE"; then
    ok "'$HOTSPOT_CONN' is active on $CSI_IFACE"
  else
    warn "'$HOTSPOT_CONN' is not active. Bring it up yourself when needed: nmcli connection up $HOTSPOT_CONN"
  fi
}

check_uplink() {
  section "Uplink and remote access"
  local dev
  dev=$(ip route show default 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }')
  if [[ -z $dev ]]; then
    warn "no default route; Tailscale and internet access need one"
  elif [[ $dev == "$CSI_IFACE" ]]; then
    fail "the default route goes through $CSI_IFACE (the CSI hotspot); it should be wlan0 or eth0"
  else
    ok "default route via $dev, not the CSI interface"
  fi
  if iface_exists tailscale0; then ok "tailscale0 present (remote SSH path)"; else info "tailscale0 not present"; fi
}

check_ports() {
  section "Listening ports"
  local l addr p
  l=$(udp_listeners "$CSI_UDP_PORT")
  if [[ -z $l ]]; then
    info "nothing listening on UDP $CSI_UDP_PORT (RuView receiver not running)"
  else
    while read -r addr; do
      case $addr in
        0.0.0.0:* | '*:'* | '[::]:'* | ':::'*)
          warn "UDP $addr listens on ALL interfaces; publish it on the $CSI_IFACE address only (start_ruview.sh does)" ;;
        *) ok "UDP listener on $addr" ;;
      esac
    done < <(ss_local_addrs <<<"$l")
  fi
  for p in 3000 3001; do
    l=$(tcp_listeners "$p")
    if [[ -z $l ]]; then
      info "nothing listening on TCP $p"
      continue
    fi
    while read -r addr; do
      case $addr in
        127.0.0.1:* | '[::1]:'*) ok "TCP $addr (loopback only)" ;;
        *) warn "TCP $addr is reachable from the network; RuView HTTP/WebSocket should be bound to 127.0.0.1" ;;
      esac
    done < <(ss_local_addrs <<<"$l")
  done
}

check_docker() {
  section "Docker / RuView containers"
  if ! detect_docker; then
    warn "$(docker_access_help)"
    return
  fi
  info "docker access: $DOCKER_MODE"
  local gw found=0 name image state ports src allow
  gw=$(docker_gateway "$DOCKER_NETWORK") || gw=''
  if [[ -n $gw ]]; then
    info "Docker '$DOCKER_NETWORK' network gateway: $gw"
  else
    warn "could not read the Docker '$DOCKER_NETWORK' network gateway"
  fi
  while IFS=$'\t' read -r name image state ports; do
    [[ -n $name ]] || continue
    [[ $name =~ ruview|densepose || $image =~ ruview|densepose ]] || continue
    found=1
    printf '       %s  image=%s  state=%s\n' "$name" "$image" "$state"
    if [[ -n $ports ]]; then printf '       ports: %s\n' "$ports"; fi
    src=$(container_env_value "$name" CSI_SOURCE) || src="unset (image default: auto)"
    allow=$(container_env_value "$name" RUVIEW_UDP_ALLOW) || allow=''
    case $src in
      esp32) ok "$name: CSI_SOURCE=esp32 (real ESP32 input configured)" ;;
      simulated | simulate) warn "$name: CSI_SOURCE=$src. This container produces SYNTHETIC data, not ESP32 data" ;;
      *) info "$name: CSI_SOURCE=$src" ;;
    esac
    if container_has_env "$name" RUVIEW_API_TOKEN; then
      info "$name: RUVIEW_API_TOKEN is set (value not shown)"
    else
      warn "$name: RUVIEW_API_TOKEN is not set"
    fi
    if [[ -n $allow ]]; then
      info "$name: RUVIEW_UDP_ALLOW=$allow"
      if [[ -n $gw ]] && ! ip_in_allowlist "$gw" "$allow"; then
        warn "$name: the allowlist does not cover the Docker gateway $gw. If docker-proxy rewrites the source address, every ESP32 frame is dropped ('disallowed source' in docker logs)"
      fi
    elif [[ $src == esp32 ]]; then
      warn "$name: no RUVIEW_UDP_ALLOW. Inside Docker the receiver then stays on 127.0.0.1 and no ESP32 frame arrives"
    fi
    if [[ $ports == *"0.0.0.0:$CSI_UDP_PORT->"* || $ports == *":::$CSI_UDP_PORT->"* ]]; then
      warn "$name publishes UDP $CSI_UDP_PORT on all interfaces; publish it on the $CSI_IFACE address only"
    fi
    if [[ $ports == *"0.0.0.0:300"[01]* || $ports == *":::300"[01]* ]]; then
      warn "$name publishes HTTP/WebSocket on all interfaces; use 127.0.0.1:3000 and 127.0.0.1:3001"
    fi
  done < <(docker_containers)
  ((found)) || info "no RuView containers"
}

check_http() {
  section "RuView HTTP"
  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found"
    return
  fi
  if ! api_get "$RUVIEW_URL/health"; then
    info "RuView is not reachable at $RUVIEW_URL (not running, or on another port)"
    return
  fi
  if [[ $API_STATUS != 200 ]]; then
    warn "/health returned HTTP $API_STATUS"
    return
  fi
  local summary
  if ! summary=$(json_tool health <<<"$API_BODY"); then
    warn "/health did not return valid JSON"
    return
  fi
  info "/health: $(tr '\n' ' ' <<<"$summary")"
  case $summary in
    *source_state=live*) ok "server source is esp32 and a frame arrived in the last 5 s" ;;
    *source_state=offline*) warn "server is set to esp32 but has had no frame for over 5 s (esp32:offline)" ;;
    *source_state=simulated*) warn "server is running on SIMULATED data" ;;
    *) info "server source is not esp32" ;;
  esac
  info "run check_stream.sh to confirm that CSI sequence numbers are advancing"
}

check_firmware_and_tools() {
  section "Firmware bundle and tools"
  local version esptool='' t
  if [[ -f $FIRMWARE_BUNDLE_DIR/SHA256SUMS.txt ]]; then
    version=$(head -c 32 "$FIRMWARE_BUNDLE_DIR/version.txt" 2>/dev/null | tr -cd '0-9A-Za-z.-')
    if (cd "$FIRMWARE_BUNDLE_DIR" && sha256sum --quiet -c SHA256SUMS.txt >/dev/null 2>&1); then
      ok "firmware bundle checksums match ($FIRMWARE_BUNDLE_DIR, version ${version:-?})"
    else
      fail "firmware bundle checksum mismatch in $FIRMWARE_BUNDLE_DIR. Do not flash it"
    fi
  else
    info "no firmware bundle at $FIRMWARE_BUNDLE_DIR (only needed for flashing)"
  fi
  if [[ -x $ESP_TOOLS_DIR/bin/esptool ]]; then
    esptool=$ESP_TOOLS_DIR/bin/esptool
  elif command -v esptool >/dev/null 2>&1; then
    esptool=$(command -v esptool)
  fi
  if [[ -n $esptool ]]; then info "esptool: $esptool"; else info "esptool not found (only needed for chip ID, backup and flashing)"; fi
  for t in python3 curl nmcli iw docker node; do
    command -v "$t" >/dev/null 2>&1 || warn "$t not found"
  done
  if command -v node >/dev/null 2>&1; then info "node $(node --version 2>/dev/null) (the RuView harness needs >= 20)"; fi
}

check_local_secrets() {
  section "Local secrets (existence and permissions only; contents are never read)"
  local d m top tracked n
  d=${XDG_CONFIG_HOME:-$HOME/.config}/wifi-densepose/esp32-provision-state
  if [[ -d $d ]]; then
    m=$(stat -c %a "$d")
    if (((8#$m & 8#077) == 0)); then
      ok "ESP32 provisioning state is owner-only ($d). It holds the Wi-Fi password: never copy it into the repo"
    else
      warn "ESP32 provisioning state $d is mode $m; run: chmod 700 $d"
    fi
  fi
  if [[ -f $RUVIEW_API_TOKEN_FILE ]]; then
    m=$(stat -c %a "$RUVIEW_API_TOKEN_FILE")
    if (((8#$m & 8#077) == 0)); then
      ok "API token file is owner-only ($RUVIEW_API_TOKEN_FILE)"
    else
      warn "API token file $RUVIEW_API_TOKEN_FILE is mode $m; run: chmod 600 $RUVIEW_API_TOKEN_FILE"
    fi
  else
    info "no API token file at $RUVIEW_API_TOKEN_FILE"
  fi
  if [[ -d $ESP_BACKUP_DIR ]]; then
    n=$(find "$ESP_BACKUP_DIR" -maxdepth 1 -type f -name '*.bin' -perm /044 2>/dev/null | wc -l)
    if ((n > 0)); then
      warn "$n flash backup(s) in $ESP_BACKUP_DIR are readable by other users. They may hold Wi-Fi credentials: chmod 600 $ESP_BACKUP_DIR/*.bin"
    else
      ok "flash backups in $ESP_BACKUP_DIR are owner-only (keep them out of git)"
    fi
  fi
  if top=$(git -C "$WIFI_SENSING_DIR" rev-parse --show-toplevel 2>/dev/null); then
    tracked=$(git -C "$top" ls-files -- '.env' '*/.env' '*.bin' '*api_token*' '*provision-state*' 2>/dev/null)
    if [[ -n $tracked ]]; then
      fail "possible secret or firmware files are tracked by git: $(tr '\n' ' ' <<<"$tracked")"
    else
      ok "git tracks no .env, token, provisioning-state or .bin files"
    fi
  fi
}

echo "Wi-Fi CSI diagnostics (read-only), $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "CSI interface: $CSI_IFACE | hotspot profile: $HOTSPOT_CONN | UDP port: $CSI_UDP_PORT"

check_host
check_usb
check_serial
check_wifi
check_hotspot
check_uplink
check_ports
if ((DO_DOCKER)); then check_docker; fi
if ((DO_HTTP)); then check_http; fi
check_firmware_and_tools
check_local_secrets

section "Summary"
echo "$FAIL_COUNT failure(s), $WARN_COUNT warning(s). Nothing was changed."
((FAIL_COUNT == 0))
