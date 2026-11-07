# shellcheck shell=bash
# Shared helpers for the Wi-Fi CSI / RuView scripts. Source this file; don't run it.
#
# Rules every helper follows:
#   * Nothing here changes system state (no nmcli up/down, no docker run/stop).
#   * Secrets (API token, Wi-Fi password) are never printed or put on a command line.
#   * Output that may contain device data goes through `redact`.

# shellcheck disable=SC2034  # many variables here are consumed by the sourcing script

WIFI_SENSING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Keys a wifi-sensing/.env file may set. Anything else is ignored with a warning.
# RUVIEW_API_TOKEN is deliberately NOT here: keep the token in RUVIEW_API_TOKEN_FILE.
ENV_FILE_KEYS="CSI_IFACE HOTSPOT_CONN CSI_UDP_PORT RUVIEW_CONTAINER_NAME RUVIEW_IMAGE \
DOCKER_NETWORK RUVIEW_URL RUVIEW_API_TOKEN_FILE FIRMWARE_BUNDLE_DIR ESP_TOOLS_DIR ESP_BACKUP_DIR"

# Image observed working on 2026-11-07 (`ruvnet/wifi-densepose:latest` resolved to this digest).
RUVIEW_IMAGE_PINNED="ruvnet/wifi-densepose@sha256:73dd3f626aef369ea4542d353d4aeb227c156d8b07d034b6a4fe697e96bd57bc"

PANDA_USB_ID="0e8d:7961"   # Panda PAU0F AXE3000 (MediaTek)
ESP32_USB_ID="303a:1001"   # Espressif USB-Serial/JTAG (ESP32-C5 / C6 / S3 native USB)

WARN_COUNT=0
FAIL_COUNT=0
DOCKER=()
DOCKER_MODE=''

# ---------------------------------------------------------------- output ----

if [[ -t 1 && -z ${NO_COLOR:-} ]]; then
  C_OK=$'\e[32m' C_WARN=$'\e[33m' C_FAIL=$'\e[31m' C_DIM=$'\e[2m' C_RST=$'\e[0m'
else
  C_OK='' C_WARN='' C_FAIL='' C_DIM='' C_RST=''
fi

ok()      { printf '%s[ OK ]%s %s\n' "$C_OK" "$C_RST" "$*"; }
info()    { printf '%s[INFO]%s %s\n' "$C_DIM" "$C_RST" "$*"; }
warn()    { WARN_COUNT=$((WARN_COUNT + 1)); printf '%s[WARN]%s %s\n' "$C_WARN" "$C_RST" "$*"; }
fail()    { FAIL_COUNT=$((FAIL_COUNT + 1)); printf '%s[FAIL]%s %s\n' "$C_FAIL" "$C_RST" "$*"; }
section() { printf '\n== %s ==\n' "$*"; }
err()     { printf 'ERROR: %s\n' "$*" >&2; }
die()     { err "$1"; exit "${2:-1}"; }

# Mask anything that looks like a credential or hardware identifier.
# Also masks the exact value of $RUVIEW_API_TOKEN if it's loaded.
redact() {
  local line tok=${RUVIEW_API_TOKEN:-}
  sed -E \
    -e 's/([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}/<mac>/g' \
    -e 's/([Bb]earer)[[:space:]]+[^[:space:]"'"'"']+/\1 <redacted>/g' \
    -e 's/(([A-Za-z_]*(TOKEN|PASSWORD|PASSWD|PSK|SECRET|token|password|passwd|psk|secret))"?[[:space:]]*[=:][[:space:]]*"?)[^[:space:],}"]+/\1<redacted>/g' \
    -e 's/(^|[^:0-9A-Fa-f])[0-9A-Fa-f]{40,}/\1<redacted-hex>/g' |
    while IFS= read -r line || [[ -n $line ]]; do
      if [[ -n $tok ]]; then line=${line//"$tok"/<redacted>}; fi
      printf '%s\n' "$line"
    done
}

# ---------------------------------------------------------------- config ----

# Parse KEY=VALUE lines without executing the file (never `source` a .env).
# shellcheck disable=SC2094  # warn() writes to stdout, not to $file
load_env_file() {
  local file=$1 line key value n=0
  [[ -f $file ]] || return 0
  while IFS= read -r line || [[ -n $line ]]; do
    n=$((n + 1))
    line=${line%$'\r'}
    [[ $line =~ ^[[:space:]]*(#|$) ]] && continue
    if [[ ! $line =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      warn "$(basename "$file"):$n: ignoring a line that isn't KEY=VALUE"
      continue
    fi
    key=${BASH_REMATCH[2]}
    value=${BASH_REMATCH[3]}
    if [[ $value =~ ^\"(.*)\"$ || $value =~ ^\'(.*)\'$ ]]; then value=${BASH_REMATCH[1]}; fi
    if [[ $key == RUVIEW_API_TOKEN ]]; then
      warn "$(basename "$file"):$n: RUVIEW_API_TOKEN is not read from .env; put it in a chmod-600 file and set RUVIEW_API_TOKEN_FILE"
      continue
    fi
    case " $ENV_FILE_KEYS " in
      *" $key "*) ;;
      *) warn "$(basename "$file"):$n: ignoring unknown key $key"; continue ;;
    esac
    if [[ $value == \~/* ]]; then value="$HOME/${value#\~/}"; fi
    # Real environment variables win over the file.
    if [[ -z ${!key+x} ]]; then
      printf -v "$key" '%s' "$value"
      export "${key?}"
    fi
  done <"$file"
}

apply_defaults() {
  : "${CSI_IFACE:=wlan1}"
  : "${HOTSPOT_CONN:=ForgeCSI}"
  : "${CSI_UDP_PORT:=5005}"
  : "${RUVIEW_CONTAINER_NAME:=ruview-live}"
  : "${RUVIEW_IMAGE:=$RUVIEW_IMAGE_PINNED}"
  : "${DOCKER_NETWORK:=bridge}"
  : "${RUVIEW_URL:=http://127.0.0.1:3000}"
  : "${RUVIEW_API_TOKEN_FILE:=$HOME/.config/ruview/api_token}"
  : "${FIRMWARE_BUNDLE_DIR:=$HOME/ruview-firmware-c5/c5-bundle}"
  : "${ESP_TOOLS_DIR:=$HOME/esp-tools}"
  : "${ESP_BACKUP_DIR:=$HOME/esp-backups}"
}

load_config() {
  load_env_file "${WIFI_SENSING_ENV_FILE:-$WIFI_SENSING_DIR/.env}"
  apply_defaults
}

validate_config() {
  is_port "$CSI_UDP_PORT" || die "CSI_UDP_PORT must be 1-65535 (got '$CSI_UDP_PORT')" 2
  [[ $CSI_IFACE =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "CSI_IFACE is not a valid interface name: '$CSI_IFACE'" 2
  [[ $RUVIEW_CONTAINER_NAME =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] ||
    die "RUVIEW_CONTAINER_NAME has invalid characters: '$RUVIEW_CONTAINER_NAME'" 2
  [[ $DOCKER_NETWORK =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || die "DOCKER_NETWORK is invalid: '$DOCKER_NETWORK'" 2
  [[ -n $RUVIEW_IMAGE && ! $RUVIEW_IMAGE =~ [[:space:]] ]] || die "RUVIEW_IMAGE is empty or has spaces" 2
}

# ------------------------------------------------------------ validation ----

is_port() { [[ $1 =~ ^[0-9]{1,5}$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535)); }

is_ipv4() {
  local ip=$1 o
  [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
  local IFS=.
  for o in $ip; do ((10#$o <= 255)) || return 1; done
}

# RFC 1918 only. Loopback, 0.0.0.0, CGNAT/Tailscale (100.64/10) and public ranges are rejected.
is_private_ipv4() {
  is_ipv4 "$1" || return 1
  local a b
  IFS=. read -r a b _ _ <<<"$1"
  a=$((10#$a)) b=$((10#$b))
  ((a == 10)) && return 0
  ((a == 172 && b >= 16 && b <= 31)) && return 0
  ((a == 192 && b == 168)) && return 0
  return 1
}

# A private IPv4 host or CIDR with prefix /8../32. Never 0.0.0.0/0.
is_safe_allow_cidr() {
  local ip=${1%/*} prefix=32
  if [[ $1 == */* ]]; then prefix=${1#*/}; fi
  [[ $prefix =~ ^[0-9]{1,2}$ ]] || return 1
  ((10#$prefix >= 8 && 10#$prefix <= 32)) || return 1
  is_private_ipv4 "$ip"
}

ip_to_int() {
  local a b c d
  IFS=. read -r a b c d <<<"$1"
  echo $(((10#$a << 24) | (10#$b << 16) | (10#$c << 8) | 10#$d))
}

# ip_in_allowlist IP "CIDR[,CIDR...]" -> true if IP falls inside any entry.
ip_in_allowlist() {
  local ip=$1 entry net prefix mask
  is_ipv4 "$ip" || return 1
  for entry in ${2//,/ }; do
    net=${entry%/*} prefix=32
    if [[ $entry == */* ]]; then prefix=${entry#*/}; fi
    if ! is_ipv4 "$net" || [[ ! $prefix =~ ^[0-9]{1,2}$ ]] || ((10#$prefix > 32)); then continue; fi
    mask=$(((0xFFFFFFFF << (32 - 10#$prefix)) & 0xFFFFFFFF))
    if ((($(ip_to_int "$ip") & mask) == ($(ip_to_int "$net") & mask))); then return 0; fi
  done
  return 1
}

# Basic shape check for the RuView API token. Prints nothing secret.
validate_token() {
  local t=${RUVIEW_API_TOKEN:-} lower
  if [[ -z $t ]]; then err "RUVIEW_API_TOKEN is empty"; return 1; fi
  if ((${#t} < 32)); then
    err "API token is too short (${#t} characters, need at least 32). Generate one with: openssl rand -hex 32"
    return 1
  fi
  if [[ $t =~ [[:space:]\"\\] ]]; then err "API token contains whitespace, quotes or backslashes"; return 1; fi
  lower=${t,,}
  case $lower in
    *replace* | *changeme* | *example* | *placeholder* | *your_token* | *'<'* | *xxxx*)
      err "API token looks like a placeholder; generate a real one with: openssl rand -hex 32"
      return 1
      ;;
  esac
}

# Load the token from a file into RUVIEW_API_TOKEN (never printed).
# The file must be a regular file, owned by the current user and not readable by group/others.
read_token_file() {
  local f=$1 mode owner first=''
  if [[ ! -e $f ]]; then err "token file not found: $f"; return 1; fi
  if [[ -L $f || ! -f $f ]]; then err "token file must be a regular file (not a symlink): $f"; return 1; fi
  owner=$(stat -c '%u' -- "$f") mode=$(stat -c '%a' -- "$f")
  if [[ $owner != "$(id -u)" ]]; then err "token file must be owned by $(id -un): $f"; return 1; fi
  if (((8#$mode & 8#077) != 0)); then
    err "token file is readable by others (mode $mode); run: chmod 600 $f"
    return 1
  fi
  IFS= read -r first <"$f" || true
  RUVIEW_API_TOKEN=${first%$'\r'}
  export RUVIEW_API_TOKEN
}

# ----------------------------------------------------------- host / net ----

iface_exists() { ip link show dev "$1" >/dev/null 2>&1; }

# First IPv4 address on an interface (no prefix), or nothing.
iface_ipv4() { ip -4 -o addr show dev "$1" 2>/dev/null | awk '{ split($4, a, "/"); print a[1]; exit }'; }

# Listening sockets on a port: udp_listeners PORT / tcp_listeners PORT
udp_listeners() { ss -H -uln "sport = :$1" 2>/dev/null; }
tcp_listeners() { ss -H -tln "sport = :$1" 2>/dev/null; }

# Local address column of ss output (handles IPv6 brackets and '*').
ss_local_addrs() { awk '{ print $4 }'; }

# ---------------------------------------------------------------- docker ----

# Sets DOCKER to a working docker command array (direct, or `sudo -n` if allowed).
# DOCKER_MODE: direct | sudo | denied | missing
detect_docker() {
  DOCKER=()
  if ! command -v docker >/dev/null 2>&1; then DOCKER_MODE=missing; return 1; fi
  if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    DOCKER=(docker) DOCKER_MODE=direct
    return 0
  fi
  if [[ ${ALLOW_SUDO:-1} == 1 ]] && command -v sudo >/dev/null 2>&1 &&
    sudo -n docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    DOCKER=(sudo -n docker) DOCKER_MODE=sudo
    return 0
  fi
  DOCKER_MODE=denied
  return 1
}

docker_access_help() {
  case ${DOCKER_MODE:-} in
    missing) echo "docker is not installed (Raspberry Pi OS: sudo apt install docker.io)" ;;
    denied) echo "cannot reach the Docker daemon, and 'sudo -n' needs a password (these scripts never prompt). Run 'sudo -v' first to cache your sudo password for a few minutes, then re-run. (Adding yourself to the docker group also works, but that group is root-equivalent.)" ;;
    *) echo "docker unavailable" ;;
  esac
}

# Lines: name<TAB>image<TAB>state<TAB>ports   (all containers, running or not)
docker_containers() {
  "${DOCKER[@]}" ps -a --format '{{.Names}}{{"\t"}}{{.Image}}{{"\t"}}{{.State}}{{"\t"}}{{.Ports}}' 2>/dev/null
}

# Print the value of one environment variable of a container. Only call this
# for NON-secret variables; use container_has_env for secrets.
container_env_value() {
  local name=$1 key=$2 line
  while IFS= read -r line; do
    if [[ $line == "$key="* ]]; then printf '%s\n' "${line#"$key"=}"; return 0; fi
  done < <("${DOCKER[@]}" inspect "$name" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)
  return 1
}

# True if the container has KEY set to a non-empty value. Prints nothing.
container_has_env() {
  local name=$1 key=$2 line
  while IFS= read -r line; do
    if [[ $line == "$key="?* ]]; then return 0; fi
  done < <("${DOCKER[@]}" inspect "$name" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)
  return 1
}

# Load the running container's API token into RUVIEW_API_TOKEN without printing it.
token_from_container() {
  local name=$1 line
  while IFS= read -r line; do
    if [[ $line == RUVIEW_API_TOKEN=?* ]]; then
      RUVIEW_API_TOKEN=${line#RUVIEW_API_TOKEN=}
      export RUVIEW_API_TOKEN
      return 0
    fi
  done < <("${DOCKER[@]}" inspect "$name" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)
  return 1
}

# First IPv4 gateway of a Docker network (detected, never hard-coded).
docker_gateway() {
  local gw
  for gw in $("${DOCKER[@]}" network inspect "$1" --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' 2>/dev/null); do
    if is_ipv4 "$gw"; then printf '%s\n' "$gw"; return 0; fi
  done
  return 1
}

# ------------------------------------------------------------------ http ----

# api_get URL -> sets API_STATUS (HTTP code, 000 on connection failure) and API_BODY.
# The bearer token travels on curl's stdin config, never in argv (so not in `ps`).
api_get() {
  local url=$1 out t
  if [[ -n ${RUVIEW_API_TOKEN:-} ]]; then
    t=${RUVIEW_API_TOKEN//\\/\\\\}
    t=${t//\"/\\\"}
    out=$(printf 'header = "Authorization: Bearer %s"\n' "$t" |
      curl -sS --max-time "${HTTP_TIMEOUT:-5}" -K - -w '\n%{http_code}' -- "$url" 2>/dev/null)
  else
    out=$(curl -sS --max-time "${HTTP_TIMEOUT:-5}" -w '\n%{http_code}' -- "$url" 2>/dev/null)
  fi
  API_STATUS=${out##*$'\n'}
  if [[ $out == *$'\n'* ]]; then API_BODY=${out%$'\n'*}; else API_BODY=''; fi
  [[ $API_STATUS =~ ^[0-9]{3}$ ]] || API_STATUS=000
  [[ $API_STATUS != 000 ]]
}

json_tool() { python3 "$WIFI_SENSING_DIR/scripts/ruview_json.py" "$@"; }
