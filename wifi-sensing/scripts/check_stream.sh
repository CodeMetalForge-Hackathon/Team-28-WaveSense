#!/usr/bin/env bash
# Verify that real ESP32 CSI frames are reaching RuView, with sanitized output.
# Read-only: it queries HTTP endpoints and inspects the container, nothing more.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Separates three things that are easy to confuse:
  1. the CONFIGURED source  (the container's CSI_SOURCE setting)
  2. the EFFECTIVE source   (what /health says the server is receiving)
  3. VERIFIED frames        (each node's CSI sequence number advancing between
                             two samples of /api/v1/nodes)

Options:
  --url URL               RuView base URL, loopback only (default: \$RUVIEW_URL or http://127.0.0.1:3000)
  --interval SECONDS      time between the two samples, 1-60 (default: 5)
  --container NAME        container to inspect (default: \$RUVIEW_CONTAINER_NAME or ruview-live)
  --token-file PATH       read the API token from PATH (default: \$RUVIEW_API_TOKEN_FILE)
  --token-from-container  read the token from the container's environment (never printed)
  --no-docker             skip Docker inspection
  --no-sudo               never use 'sudo -n docker'
  -h, --help              show this help

The token is taken from \$RUVIEW_API_TOKEN if exported, else from the token file,
else (only with --token-from-container) from the container. It is never printed
and never placed on a command line.

Exit status: 0 = live CSI verified, 1 = not verified, 2 = usage error, 3 = token problem.
EOF
}

INTERVAL=5
URL_ARG=''
TOKEN_FILE_ARG=''
TOKEN_FROM_CONTAINER=0
DO_DOCKER=1
while (($#)); do
  case $1 in
    --url) URL_ARG=${2:-}; shift ;;
    --interval) INTERVAL=${2:-}; shift ;;
    --container) RUVIEW_CONTAINER_NAME=${2:-}; shift ;;
    --token-file) TOKEN_FILE_ARG=${2:-}; shift ;;
    --token-from-container) TOKEN_FROM_CONTAINER=1 ;;
    --no-docker) DO_DOCKER=0 ;;
    --no-sudo) ALLOW_SUDO=0 ;;
    -h | --help) usage; exit 0 ;;
    *) err "unknown option: $1"; usage >&2; exit 2 ;;
  esac
  shift
done

load_config
validate_config
if [[ -n $URL_ARG ]]; then RUVIEW_URL=$URL_ARG; fi
RUVIEW_URL=${RUVIEW_URL%/}
if [[ ! $INTERVAL =~ ^[0-9]+$ ]] || ((10#$INTERVAL < 1 || 10#$INTERVAL > 60)); then
  die "--interval must be a whole number of seconds from 1 to 60" 2
fi
# The bearer token is sent in clear over HTTP, so only ever to this machine.
[[ $RUVIEW_URL =~ ^http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?$ ]] ||
  die "--url must be http://127.0.0.1:PORT or http://localhost:PORT (the token must not leave this host)" 2

TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT

echo "RuView stream check, $(date '+%Y-%m-%d %H:%M:%S %Z'), server $RUVIEW_URL"

# ------------------------------------------------------------------ 1 ----
section "1. Configured source (container setting)"
CONFIGURED=unknown
if ((DO_DOCKER)); then
  if detect_docker; then
    state=$("${DOCKER[@]}" inspect "$RUVIEW_CONTAINER_NAME" --format '{{.State.Status}}' 2>/dev/null) || state=''
    if [[ -z $state ]]; then
      warn "container '$RUVIEW_CONTAINER_NAME' not found (set --container if it has another name)"
    else
      CONFIGURED=$(container_env_value "$RUVIEW_CONTAINER_NAME" CSI_SOURCE) || CONFIGURED='auto (image default)'
      info "container $RUVIEW_CONTAINER_NAME: state=$state CSI_SOURCE=$CONFIGURED"
      case $CONFIGURED in
        simulated | simulate) warn "this container is configured for SIMULATED data; nothing below reflects the ESP32" ;;
        esp32) ok "configured for real ESP32 input (this alone proves nothing about packets)" ;;
      esac
      [[ $state == running ]] || warn "container is $state, not running"
    fi
  else
    info "Docker not inspected: $(docker_access_help)"
  fi
else
  info "skipped (--no-docker)"
fi

# ------------------------------------------------------------------ 2 ----
section "2. Effective source (/health, no token needed)"
if ! api_get "$RUVIEW_URL/health"; then
  fail "RuView is not reachable at $RUVIEW_URL. Is the container running and is port 3000 published on 127.0.0.1?"
  exit 1
fi
if [[ $API_STATUS != 200 ]]; then
  fail "/health returned HTTP $API_STATUS"
  exit 1
fi
if ! health=$(json_tool health <<<"$API_BODY"); then
  fail "/health did not return valid JSON"
  exit 1
fi
info "$(tr '\n' ' ' <<<"$health")"
SOURCE_STATE=$(sed -n 's/^source_state=//p' <<<"$health")
case $SOURCE_STATE in
  live) ok "server source is esp32 and it accepted a frame within the last 5 s" ;;
  offline) fail "server is set to esp32 but has accepted no ESP32 frame for over 5 s (esp32:offline)" ;;
  simulated) fail "server is producing SIMULATED data" ;;
  *) warn "server source is not esp32" ;;
esac

# ------------------------------------------------------------------ 3 ----
section "3. Incoming CSI frames (two samples, ${INTERVAL}s apart)"
token_src=''
if [[ -n ${RUVIEW_API_TOKEN:-} ]]; then
  token_src='environment'
elif [[ -n $TOKEN_FILE_ARG ]]; then
  read_token_file "$TOKEN_FILE_ARG" || exit 3
  token_src='token file'
elif [[ -f $RUVIEW_API_TOKEN_FILE ]]; then
  read_token_file "$RUVIEW_API_TOKEN_FILE" || exit 3
  token_src='token file'
elif ((TOKEN_FROM_CONTAINER)) && ((${#DOCKER[@]} > 0)) && token_from_container "$RUVIEW_CONTAINER_NAME"; then
  token_src='container environment'
fi
if [[ -z $token_src ]]; then
  fail "no API token. Export RUVIEW_API_TOKEN, create $RUVIEW_API_TOKEN_FILE (chmod 600), or pass --token-from-container"
  exit 3
fi
info "API token loaded from the $token_src (not shown)"

fetch_nodes() { # fetch_nodes OUTFILE
  api_get "$RUVIEW_URL/api/v1/nodes" || { fail "lost contact with $RUVIEW_URL"; exit 1; }
  case $API_STATUS in
    200) ;;
    401 | 403)
      fail "token rejected (HTTP $API_STATUS). A recreated container has a NEW token: update your token file and the dashboard's Settings > API Access"
      exit 3
      ;;
    *) fail "/api/v1/nodes returned HTTP $API_STATUS"; exit 1 ;;
  esac
  printf '%s' "$API_BODY" >"$1"
}

fetch_nodes "$TMPD/nodes1.json"
if ! json_tool nodes <"$TMPD/nodes1.json" | sed 's/^/       /'; then
  fail "/api/v1/nodes did not return the expected JSON"
  exit 1
fi
sleep "$INTERVAL"
fetch_nodes "$TMPD/nodes2.json"
compare_out=$(json_tool compare "$TMPD/nodes1.json" "$TMPD/nodes2.json" "$INTERVAL")
compare_rc=$?
# shellcheck disable=SC2001  # indent every line; sed is clearer than ${var//}
printf '%s\n' "$compare_out" | sed 's/^/       /'

# ------------------------------------------------------------------ 4 ----
if ((${#DOCKER[@]} > 0)); then
  section "4. UDP source-guard drops (container log, last 10 min)"
  drops=$("${DOCKER[@]}" logs --since 10m "$RUVIEW_CONTAINER_NAME" 2>&1 | grep -F 'disallowed source')
  if [[ -n $drops ]]; then
    warn "$(wc -l <<<"$drops") 'disallowed source' line(s). RUVIEW_UDP_ALLOW is rejecting frames. Most recent:"
    tail -n 1 <<<"$drops" | cut -c1-200 | redact | sed 's/^/       /'
    info "the address after 'source' is what Docker delivers frames from; see TROUBLESHOOTING.md"
  else
    ok "no 'disallowed source' drops logged"
  fi
fi

# ------------------------------------------------------------- verdict ----
section "Verdict"
if ((compare_rc == 0)) && [[ $SOURCE_STATE == live ]]; then
  ok "VERIFIED: real ESP32 CSI frames are arriving and being parsed"
  verdict=0
else
  fail "NOT VERIFIED: no node had csi_status=active with an advancing sequence on a live esp32 source"
  verdict=1
fi
cat <<'EOF'
What this does and does not show:
  - It shows transport only: frames from a node reach RuView and are parsed.
  - seq_rate_hz counts firmware sequence numbers. The firmware numbers every CSI
    callback but sends at most one UDP frame per 20 ms, so this rate can exceed
    the delivered packet rate.
  - motion_level and person_count are RuView's own classifications, and
    person_count_valid is a flag reported by the node's firmware. Neither one is
    a validated detection.
  - position_configured=false means no --node-positions entry, so positions on
    the dashboard are placeholders.
EOF
exit "$verdict"
