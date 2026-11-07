#!/usr/bin/env bash
# Fake system commands for the offline tests. run_tests.sh symlinks each mocked
# command name to this file; behaviour is chosen by basename "$0" and driven by
# fixture files in $MOCK_SCENARIO. Every call is appended to $MOCK_LOG so tests
# can assert what was (and was not) invoked.
set -u
cmd=$(basename "$0")
S=${MOCK_SCENARIO:?MOCK_SCENARIO not set}
LOG=${MOCK_LOG:?MOCK_LOG not set}

log() { printf '%s\n' "$*" >>"$LOG"; }
fixture() { [[ -f $S/$1 ]] && cat "$S/$1"; }

# A secret on a command line is visible to every local user via `ps`.
if [[ -n ${MOCK_TOKEN:-} ]]; then
  for a in "$@"; do
    if [[ $a == *"$MOCK_TOKEN"* ]]; then log "TOKEN_IN_ARGV $cmd"; fi
  done
fi
log "$cmd $*"

case $cmd in
  lsusb) fixture lsusb ;;

  ip)
    args="$*"
    last=${!#}
    case $args in
      "link show dev "*)
        [[ -f $S/iface_$last ]] && exit 0
        echo "Device \"$last\" does not exist." >&2
        exit 1
        ;;
      "-4 -o addr show dev "*)
        [[ -f $S/iface_$last ]] || exit 1
        fixture "ipv4_$last"
        exit 0
        ;;
      "route show default") fixture route_default; exit 0 ;;
      *) exit 0 ;;
    esac
    ;;

  iw)
    # iw dev IFACE info | iw phy PHY info
    case "${1:-} ${3:-}" in
      "dev info") fixture "iw_dev_$2" || exit 237 ;;
      "phy info") fixture "iw_phy_$2" || exit 1 ;;
      *) exit 0 ;;
    esac
    ;;

  nmcli)
    for a in "$@"; do
      case $a in -s | --show-secrets | *secret*) log "SECRETS_REQUESTED"; exit 99 ;; esac
    done
    case " $* " in
      *" up "* | *" down "* | *" modify "* | *" add "* | *" delete "*) log "MUTATION nmcli"; exit 0 ;;
    esac
    if [[ " $* " == *" --active "* ]]; then fixture nmcli_active; exit 0; fi
    name=${!#}
    fixture "nmcli_conn_$name" || { echo "Error: $name - no such connection profile." >&2; exit 10; }
    ;;

  ss)
    port=$(grep -oE 'sport = :[0-9]+' <<<"$*" | grep -oE '[0-9]+$')
    if [[ " $* " == *" -uln "* ]]; then f=ss_udp; else f=ss_tcp; fi
    if [[ -f $S/$f ]]; then grep -E ":${port}[[:space:]]" "$S/$f"; fi
    exit 0
    ;;

  sudo)
    if [[ -f $S/sudo_denied ]]; then echo "sudo: a password is required" >&2; exit 1; fi
    keep=''
    while [[ ${1:-} == -* ]]; do
      case $1 in --preserve-env=*) keep=${1#--preserve-env=} ;; esac
      shift
    done
    # Real sudo resets the environment unless a variable is preserved explicitly.
    [[ ",$keep," == *",RUVIEW_API_TOKEN,"* ]] || unset RUVIEW_API_TOKEN
    MOCK_UNDER_SUDO=1 exec "$@"
    ;;

  docker)
    access=$(fixture docker_access) || access=none
    if [[ $access == none || ($access == sudo && -z ${MOCK_UNDER_SUDO:-}) ]]; then
      echo "permission denied while trying to connect to the Docker daemon socket" >&2
      exit 1
    fi
    case ${1:-} in
      version) echo "26.1.5+dfsg1" ;;
      ps) fixture docker_ps ;;
      inspect)
        name=$2
        [[ -f $S/docker_env_$name ]] || { echo "Error: No such object: $name" >&2; exit 1; }
        case "$*" in
          *Config.Env*) cat "$S/docker_env_$name" ;;
          *State.Status*) fixture "docker_state_$name" || echo running ;;
        esac
        ;;
      network) fixture docker_gateway ;;
      image) [[ -f $S/docker_image_present ]] ;;
      logs) fixture docker_logs ;;
      run)
        log "RUN_TOKEN_ENV=$([[ -n ${RUVIEW_API_TOKEN:-} ]] && echo present || echo absent)"
        prev='' cname=''
        for a in "$@"; do
          if [[ $prev == --name ]]; then cname=$a; fi
          prev=$a
        done
        if [[ -n $cname ]]; then
          echo running >"$S/docker_state_$cname"
          echo 'CSI_SOURCE=esp32' >"$S/docker_env_$cname"
        fi
        echo "0123456789abcdef0123456789abcdef"
        ;;
      rm | stop | start | restart | kill | network-connect) log "MUTATION docker $1" ;;
      *) ;;
    esac
    ;;

  curl)
    url='' auth='' wfmt='' cfg=''
    while (($#)); do
      case $1 in
        -K)
          shift
          if [[ $1 == - ]]; then
            cfg=$(cat)
            auth=$(sed -n 's/^header = "Authorization: Bearer \(.*\)"$/\1/p' <<<"$cfg")
          fi
          ;;
        -w) shift; wfmt=$1 ;;
        --max-time) shift ;;
        --) shift; url=$1 ;;
        -*) ;;
        *) url=$1 ;;
      esac
      shift
    done
    path=/${url#*://*/}
    [[ -f $S/http_up ]] || { log "CURL $path refused"; [[ -n $wfmt ]] && printf '\n000'; exit 7; }
    code=200 body=''
    case $path in
      /health) body=$(fixture http_health) ;;
      /api/v1/nodes)
        if [[ -z $auth || $auth != "$(fixture valid_token)" ]]; then
          code=401 body='{"error":"unauthorized"}'
        else
          n=$(($(cat "$MOCK_STATE/nodes_calls" 2>/dev/null || echo 0) + 1))
          echo "$n" >"$MOCK_STATE/nodes_calls"
          body=$(fixture "http_nodes_$n") || body=$(fixture http_nodes_1)
        fi
        ;;
      *) code=404 body='{}' ;;
    esac
    log "CURL $path auth=$([[ -n $auth ]] && echo yes || echo no) code=$code"
    printf '%s' "$body"
    if [[ -n $wfmt ]]; then printf '\n%s' "$code"; fi
    exit 0
    ;;

  sleep) exit 0 ;;

  *) echo "mock: unknown command $cmd" >&2; exit 127 ;;
esac
