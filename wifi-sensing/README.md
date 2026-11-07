# wifi-sensing: RuView / ESP32 CSI tooling

Small, dependency-light helpers for the Wi-Fi CSI pipeline. The docs live in [`docs/wifi-csi/`](../docs/wifi-csi/README.md).

They need bash, coreutils, `iproute2`, `iw`, `nmcli`, `curl` and Python 3 (standard library only), all of which ship with Raspberry Pi OS 64-bit. Docker needs root on our Pi, so run `sudo -v` first: the scripts use `sudo -n` and never prompt.

| Script | What it does | Changes anything? |
|---|---|---|
| [`scripts/diagnose.sh`](scripts/diagnose.sh) | Checks USB adapters, serial ports, `wlan1` and AP mode, the hotspot profile, the default route, ports 5005/3000/3001, RuView containers (source, allowlist and gateway), `/health`, firmware checksums, and permissions on local secrets | **No.** Read-only |
| [`scripts/check_stream.sh`](scripts/check_stream.sh) | Separates the **configured** source (container env), the **effective** source (`/health`) and **verified** frames (`csi_sequence` advancing between two `/api/v1/nodes` samples). Surfaces `disallowed source` drops | **No.** Read-only |
| [`scripts/start_ruview.sh`](scripts/start_ruview.sh) | Optional. Validates the token, interface address, Docker gateway, port and name conflicts, then prints the `docker run` plan. **Starts the container only with `--start`** | Only with `--start`. It never stops or replaces other containers. `--replace-existing` removes only a RuView container with the same name, after you type its name |
| [`scripts/lib.sh`](scripts/lib.sh) | Shared helpers: safe `.env` parsing, redaction, IP and CIDR checks, Docker detection, token handling | Nothing on its own |
| [`scripts/ruview_json.py`](scripts/ruview_json.py) | Summarizes `/health` and `/api/v1/nodes` through a field **allowlist** and compares two samples | No |

```bash
cp wifi-sensing/.env.example wifi-sensing/.env     # optional; only if your names or addresses differ
wifi-sensing/scripts/diagnose.sh
wifi-sensing/scripts/check_stream.sh --interval 5
wifi-sensing/scripts/start_ruview.sh               # plan only
```

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | OK / verified / plan OK |
| 1 | Failure or not verified |
| 2 | Usage or config error |
| 3 | Token problem (`check_stream.sh`, `start_ruview.sh`) |

## Safety properties

The tests check every one of these:

- **Tokens.** The API token is never printed and never placed on a command line. `curl` reads it from stdin, and `docker run` gets `-e RUVIEW_API_TOKEN` by name. Token files must be regular, owner-only files; short or placeholder tokens are rejected. Tokens are never read from `.env`.
- **Network secrets.** `nmcli` is never asked for secrets. MAC addresses, bearer tokens and long hex strings are masked in any output that echoes device data.
- **Bindings.** HTTP and WebSocket bind to `127.0.0.1` only. UDP binds only to the IPv4 address of `CSI_IFACE`, which must be RFC 1918 and is never `0.0.0.0`. Allowlists must be private CIDRs.
- **Nothing hard-coded.** The Docker gateway and the hotspot address are detected at run time.
- **Safe config parsing.** `.env` is parsed as `KEY=VALUE` data, never executed, and only known keys are accepted.
- **Hands off.** The scripts never run `nmcli up/down/modify` and never stop other containers.

## Tests

```bash
bash wifi-sensing/tests/run_tests.sh
```

The suite runs completely offline. [`tests/mock_commands.sh`](tests/mock_commands.sh) stands in for `docker`, `sudo`, `ip`, `iw`, `nmcli`, `ss`, `lsusb`, `curl` and `sleep`, using fixtures built from real outputs on our Pi. Each test gets a throwaway `HOME` and an empty environment, so your real token and `.env` are never read. [`tests/test_ruview_json.py`](tests/test_ruview_json.py) runs the Python unit tests.

If you change the scripts, also run [ShellCheck](https://www.shellcheck.net/) if you have it: `shellcheck -x -P wifi-sensing/scripts wifi-sensing/scripts/*.sh wifi-sensing/tests/*.sh`.

## Validation status (2026-11-07)

| Script | Mocked tests | Real Pi |
|---|---|---|
| `diagnose.sh` | Pass | **Run on the real Pi.** Adapter plugged in, hotspot down, ESP32 not on USB. Results matched the hardware. The Docker section read the real (exited) `ruview-live`: gateway detected as `172.17.0.1`, `CSI_SOURCE=esp32`, allowlist covering the gateway, token reported as set but not printed |
| `check_stream.sh` | Pass, including the healthy path | Only the **"server not reachable"** path ran, because no container was running. **The healthy path has not been exercised against a live RuView instance yet** |
| `start_ruview.sh` | Pass, including `--start` | Only the **"no token file"** path ran. **It has never started a real container**, by design: the PR left the Pi's Docker state untouched |

The first time someone runs `start_ruview.sh --start` or the healthy path of `check_stream.sh` on the real pipeline, record the result in `docs/wifi-csi/RESULTS.md`.
