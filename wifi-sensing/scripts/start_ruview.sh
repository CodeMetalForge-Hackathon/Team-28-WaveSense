#!/usr/bin/env bash
# OPTIONAL, explicit-use helper: check prerequisites and start the RuView live
# receiver in Docker with restrictive bindings. Without --start it only prints
# the plan. It never stops or replaces other containers, and it never changes
# network interfaces or the hotspot.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--start] [--replace-existing] [--token-file PATH]
                       [--udp-allow CIDR] [--no-sudo] [-h]

Checks everything the RuView live receiver needs and prints the exact
'docker run' it would use (token never shown). Nothing starts without --start.

  --start               start the container once every check passes
  --replace-existing    if a container with the same name already exists, offer to
                        remove it first. You must type its name to confirm, so this
                        needs a terminal. Other containers are never touched.
  --token-file PATH     read the API token from PATH (default: \$RUVIEW_API_TOKEN_FILE,
                        i.e. ~/.config/ruview/api_token)
  --udp-allow CIDR      override the UDP source allowlist. Default: the detected Docker
                        bridge gateway /32. Private IPv4 only, prefix /8 to /32.
  --no-sudo             never use 'sudo -n docker'
  -h, --help            show this help

Bindings it always uses:
  HTTP 127.0.0.1:3000, WebSocket 127.0.0.1:3001  (loopback only; use port forwarding)
  UDP  <IPv4 of \$CSI_IFACE>:\$CSI_UDP_PORT          (never 0.0.0.0)

Create a token once (it is never committed or printed):
  install -d -m 700 ~/.config/ruview
  (umask 077; openssl rand -hex 32 > ~/.config/ruview/api_token)

Exit status: 0 = plan OK or started, 1 = a prerequisite failed,
             2 = usage or config error, 3 = token problem.
EOF
}

START=0
REPLACE=0
TOKEN_FILE_ARG=''
UDP_ALLOW_ARG=''
while (($#)); do
  case $1 in
    --start) START=1 ;;
    --replace-existing) REPLACE=1 ;;
    --token-file) TOKEN_FILE_ARG=${2:-}; [[ -n $TOKEN_FILE_ARG ]] || die "--token-file needs a path" 2; shift ;;
    --udp-allow) UDP_ALLOW_ARG=${2:-}; [[ -n $UDP_ALLOW_ARG ]] || die "--udp-allow needs a value" 2; shift ;;
    --no-sudo) ALLOW_SUDO=0 ;;
    -h | --help) usage; exit 0 ;;
    *) err "unknown option: $1"; usage >&2; exit 2 ;;
  esac
  shift
done

load_config
validate_config
CONTAINER_UDP_PORT=5005 # fixed inside the image

# --------------------------------------------------------------- image ----
section "Image"
info "image: $RUVIEW_IMAGE"
if [[ $RUVIEW_IMAGE != *@sha256:* ]]; then
  warn "image is not pinned by digest, so ':latest' may change between runs. Tested on 2026-11-07: $RUVIEW_IMAGE_PINNED"
fi

# --------------------------------------------------------------- token ----
section "API token"
if [[ -n ${RUVIEW_API_TOKEN:-} ]]; then
  token_src='the RUVIEW_API_TOKEN environment variable'
else
  token_file=${TOKEN_FILE_ARG:-$RUVIEW_API_TOKEN_FILE}
  read_token_file "$token_file" || die "no usable API token. See '$(basename "$0") --help' to create one" 3
  token_src=$token_file
fi
validate_token || exit 3
ok "API token loaded from $token_src (not shown)"

# -------------------------------------------------------------- docker ----
section "Docker"
detect_docker || die "$(docker_access_help)" 1
ok "docker access: $DOCKER_MODE"

# ------------------------------------------------------------- network ----
section "Network binding"
iface_exists "$CSI_IFACE" || die "$CSI_IFACE not found. Plug in the Panda adapter, or set CSI_IFACE" 1
UDP_HOST_IP=$(iface_ipv4 "$CSI_IFACE")
[[ -n $UDP_HOST_IP ]] ||
  die "$CSI_IFACE has no IPv4 address. Start the hotspot yourself first: nmcli connection up $HOTSPOT_CONN" 1
is_private_ipv4 "$UDP_HOST_IP" ||
  die "$CSI_IFACE has address $UDP_HOST_IP, which is not private (RFC 1918). Refusing to expose the unauthenticated UDP port there" 1
ok "UDP $CSI_UDP_PORT will be published only on $CSI_IFACE ($UDP_HOST_IP)"
if ! nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep -Fxq "$HOTSPOT_CONN:$CSI_IFACE"; then
  warn "hotspot '$HOTSPOT_CONN' is not reported active on $CSI_IFACE (nodes may not be able to join)"
fi

GW=$(docker_gateway "$DOCKER_NETWORK") || die "could not read the gateway of Docker network '$DOCKER_NETWORK'" 1
is_private_ipv4 "$GW" || die "Docker '$DOCKER_NETWORK' gateway '$GW' is not a private IPv4 address" 1
if [[ -n $UDP_ALLOW_ARG ]]; then
  is_safe_allow_cidr "$UDP_ALLOW_ARG" ||
    die "--udp-allow must be a private IPv4 address or CIDR with prefix /8 to /32 (got '$UDP_ALLOW_ARG')" 2
  UDP_ALLOW=$UDP_ALLOW_ARG
else
  UDP_ALLOW="$GW/32"
fi
ok "UDP source allowlist: $UDP_ALLOW (Docker '$DOCKER_NETWORK' gateway: $GW)"
info "docker-proxy delivers frames from the gateway address, not the node's. See TROUBLESHOOTING.md, 'disallowed source'"

# ----------------------------------------------------------- conflicts ----
section "Conflicts"
problems=0
existing_state=''
existing_image=''

ports_conflict() { # ports_conflict "<docker ps .Ports string>"
  [[ $1 == *":3000->"* || $1 == *":3000-"* || $1 == *":3001->"* ||
    $1 == *":$CSI_UDP_PORT->"* || $1 == *":$CSI_UDP_PORT-"* ]]
}

while IFS=$'\t' read -r name image state ports; do
  [[ -n $name ]] || continue
  if [[ $name == "$RUVIEW_CONTAINER_NAME" ]]; then
    existing_state=$state existing_image=$image
    continue
  fi
  if [[ $state == running ]] && ports_conflict "$ports"; then
    src=$(container_env_value "$name" CSI_SOURCE) || src='unset'
    fail "container '$name' ($image, CSI_SOURCE=$src) already publishes 3000, 3001 or $CSI_UDP_PORT"
    info "   this script will not stop it. If it is an old RuView instance you no longer need: ${DOCKER[*]} stop $name"
    problems=1
  fi
done < <(docker_containers)

if [[ -n $existing_state ]]; then
  if ((REPLACE)); then
    [[ $existing_image == ruvnet/wifi-densepose* ]] ||
      die "existing '$RUVIEW_CONTAINER_NAME' runs '$existing_image', not RuView. Refusing to touch it" 1
    info "existing '$RUVIEW_CONTAINER_NAME' ($existing_state) would be removed after you confirm (only with --start)"
  else
    fail "a container named '$RUVIEW_CONTAINER_NAME' already exists ($existing_state)"
    info "   to start it again with its old settings and old token: ${DOCKER[*]} start $RUVIEW_CONTAINER_NAME"
    info "   to replace it: re-run with --replace-existing --start (asks you to confirm)"
    problems=1
  fi
fi

check_host_ports() {
  local l bad=0
  for p in 3000 3001; do
    l=$(tcp_listeners "$p")
    if [[ -n $l ]]; then
      fail "TCP $p is already in use on this host: $(ss_local_addrs <<<"$l" | tr '\n' ' ')"
      bad=1
    fi
  done
  l=$(udp_listeners "$CSI_UDP_PORT")
  if [[ -n $l ]]; then
    fail "UDP $CSI_UDP_PORT is already in use on this host: $(ss_local_addrs <<<"$l" | tr '\n' ' ')"
    bad=1
  fi
  return "$bad"
}
# A running same-name container explains its own listeners; check those after removal.
if ! { ((REPLACE)) && [[ $existing_state == running ]]; }; then
  check_host_ports || problems=1
fi
((problems)) || ok "no port or name conflicts"

# ---------------------------------------------------------------- plan ----
RUN_ARGS=(run -d --name "$RUVIEW_CONTAINER_NAME" --restart no
  -p "127.0.0.1:3000:3000"
  -p "127.0.0.1:3001:3001"
  -p "$UDP_HOST_IP:$CSI_UDP_PORT:$CONTAINER_UDP_PORT/udp"
  -e RUVIEW_API_TOKEN
  -e CSI_SOURCE=esp32
  -e "RUVIEW_UDP_ALLOW=$UDP_ALLOW"
  "$RUVIEW_IMAGE")
if [[ $DOCKER_MODE == sudo ]]; then
  RUN_CMD=(sudo -n --preserve-env=RUVIEW_API_TOKEN docker)
else
  RUN_CMD=(docker)
fi

# Last line of defence: the token must not appear in argv, and nothing may bind 0.0.0.0.
for a in "${RUN_ARGS[@]}"; do
  [[ $a != *"$RUVIEW_API_TOKEN"* ]] || die "internal error: token would appear on the command line" 1
  [[ $a != 0.0.0.0:* && $a != *"::"* ]] || die "internal error: wildcard bind in '$a'" 1
done

section "Plan"
printf '  %s' "${RUN_CMD[@]}"
printf ' %q' "${RUN_ARGS[@]}"
printf '\n'
info "RUVIEW_API_TOKEN is passed by name from your environment; its value is not on the command line"

if ((problems)); then
  section "Result"
  echo "Fix the failures above. Nothing was started or changed."
  exit 1
fi
if ((!START)); then
  section "Result"
  echo "Plan only. Nothing was started. Re-run with --start to launch it."
  exit 0
fi

# --------------------------------------------------------------- start ----
section "Start"
if [[ -n $existing_state ]]; then
  # REPLACE is set here; anything else exited above.
  if [[ ! -t 0 ]] || ! { : </dev/tty; } 2>/dev/null; then
    die "--replace-existing needs an interactive terminal to confirm. Nothing was changed" 1
  fi
  printf "Type the container name '%s' to remove it (anything else cancels): " "$RUVIEW_CONTAINER_NAME" >/dev/tty
  IFS= read -r answer </dev/tty
  [[ $answer == "$RUVIEW_CONTAINER_NAME" ]] || die "not confirmed. Nothing was changed" 1
  "${DOCKER[@]}" rm -f "$RUVIEW_CONTAINER_NAME" >/dev/null || die "docker rm failed" 1
  ok "removed the old '$RUVIEW_CONTAINER_NAME'"
  check_host_ports || die "ports are still in use after removing '$RUVIEW_CONTAINER_NAME'. Not starting" 1
fi

cid=$("${RUN_CMD[@]}" "${RUN_ARGS[@]}") || die "docker run failed (see the message above)" 1
ok "started $RUVIEW_CONTAINER_NAME (${cid:0:12})"

healthy=0
for _ in $(seq 1 15); do
  if api_get "$RUVIEW_URL/health" && [[ $API_STATUS == 200 ]]; then healthy=1; break; fi
  sleep 1
done
state=$("${DOCKER[@]}" inspect "$RUVIEW_CONTAINER_NAME" --format '{{.State.Status}}' 2>/dev/null) || state=unknown
if [[ $state != running ]]; then
  fail "container is '$state', not running. Look at: ${DOCKER[*]} logs $RUVIEW_CONTAINER_NAME"
  exit 1
fi
if ((healthy)); then
  ok "/health answers: $(json_tool health <<<"$API_BODY" 2>/dev/null | tr '\n' ' ')"
else
  warn "/health did not answer within 15 s. Look at: ${DOCKER[*]} logs $RUVIEW_CONTAINER_NAME"
fi

if [[ $token_src == 'the RUVIEW_API_TOKEN environment variable' ]]; then
  token_hint='use the token you exported as RUVIEW_API_TOKEN'
else
  token_hint="read it in your own terminal only: cat $token_src"
fi
cat <<EOF

Next:
  1. In VS Code (Remote SSH), forward ports 3000 and 3001 (Ports panel > Forward a Port).
  2. Open http://localhost:3000/ui/index.html
  3. Settings > API Access: paste this container's token. The browser may still hold
     an older one; $token_hint
  4. Verify that real frames arrive:  wifi-sensing/scripts/check_stream.sh
EOF
