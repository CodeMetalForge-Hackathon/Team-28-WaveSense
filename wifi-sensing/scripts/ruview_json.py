#!/usr/bin/env python3
"""Summarise RuView sensing-server JSON without leaking anything sensitive.

Only fields named in an explicit allowlist are printed, so fields that
upstream adds later (device IDs, MACs, tokens) are never echoed. Standard
library only, so it runs on a stock Raspberry Pi OS.

Usage:
  ruview_json.py health  < health.json          # GET /health
  ruview_json.py nodes   < nodes.json           # GET /api/v1/nodes
  ruview_json.py compare FIRST.json SECOND.json INTERVAL_SECONDS

Exit codes:
  0  ok (compare: at least one node's CSI is active and its sequence advanced)
  1  not verified (compare) or bad input
  2  usage error
"""

from __future__ import annotations

import json
import re
import sys

NODE_FIELDS = (
    "node_id",
    "status",
    "csi_status",
    "csi_last_seen_ms",
    "csi_sequence",
    "rssi_dbm",
    "motion_level",
    "person_count",
    "person_count_valid",
    "position_configured",
)

# Values must look like short identifiers; anything else is masked.
_SAFE_STR = re.compile(r"^[A-Za-z0-9_.:-]{0,40}$")


def fmt(value: object) -> str:
    """Render a JSON scalar safely for a key=value line."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and _SAFE_STR.match(value):
        return value
    return "<unexpected>"


def load(stream) -> object:
    try:
        return json.load(stream)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"response is not valid JSON ({exc.__class__.__name__})") from None


# A datagram younger than this counts as "live".
LIVE_MAX_AGE_MS = 5000

# Numeric UDP counters from /health processing.udp that are safe to print.
UDP_FIELDS = ("datagrams", "dropped_by_allowlist", "last_datagram_age_ms")


def classify_source(source: object) -> str:
    """Map /health `source` to configured | offline | simulated | other | unknown.

    `source` only says what the server is set to read. "esp32" is NOT evidence
    that frames arrive: on 2026-11-07 /health said source=esp32 while
    processing.udp.datagrams was 0. Use source_state() for liveness.
    """
    if not isinstance(source, str) or not source:
        return "unknown"
    s = source.lower()
    if "simulat" in s:
        return "simulated"
    if s.endswith(":offline"):
        return "offline"
    if s == "esp32":
        return "configured"
    return "other"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def udp_counters(doc: dict) -> dict | None:
    processing = doc.get("processing")
    udp = processing.get("udp") if isinstance(processing, dict) else None
    return udp if isinstance(udp, dict) else None


def source_state(doc: dict) -> str:
    """live | waiting | configured | offline | simulated | other | unknown.

    live       esp32 source and RuView received a datagram < LIVE_MAX_AGE_MS ago
    waiting    esp32 source, but no datagram yet or the last one is too old
    configured esp32 source, but this server build reports no UDP counters
    """
    state = classify_source(doc.get("source"))
    if state != "configured":
        return state
    udp = udp_counters(doc)
    if udp is None:
        return "configured"
    age = _number(udp.get("last_datagram_age_ms"))
    return "live" if age is not None and 0 <= age <= LIVE_MAX_AGE_MS else "waiting"


def summarise_health(doc: object) -> list[str]:
    if not isinstance(doc, dict):
        raise ValueError("health response is not a JSON object")
    lines = [
        f"status={fmt(doc.get('status'))}",
        f"source={fmt(doc.get('source'))}",
        f"source_state={source_state(doc)}",
    ]
    udp = udp_counters(doc)
    if udp is not None:
        lines += [f"udp_{k}={fmt(udp.get(k)) if _number(udp.get(k)) is not None else 'null'}" for k in UDP_FIELDS]
    return lines


def nodes_of(doc: object) -> list[dict]:
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), list):
        raise ValueError("nodes response has no 'nodes' list")
    return [n for n in doc["nodes"] if isinstance(n, dict)]


def summarise_nodes(doc: object) -> list[str]:
    nodes = nodes_of(doc)
    lines = [f"total={len(nodes)}"]
    for node in nodes:
        lines.append(" ".join(f"{k}={fmt(node.get(k))}" for k in NODE_FIELDS))
    return lines


def compare(first: object, second: object, interval: float) -> tuple[list[str], bool]:
    """Compare two /api/v1/nodes samples taken `interval` seconds apart."""
    before = {n.get("node_id"): n for n in nodes_of(first)}
    lines: list[str] = []
    verified = False
    for node in nodes_of(second):
        nid = node.get("node_id")
        seq1 = before.get(nid, {}).get("csi_sequence")
        seq2 = node.get("csi_sequence")
        csi_active = node.get("csi_status") == "active"
        if isinstance(seq1, int) and isinstance(seq2, int) and not isinstance(seq1, bool):
            delta = seq2 - seq1
            if delta > 0:
                state = "advancing"
            elif delta == 0:
                state = "stalled"
            else:
                state = "reset"  # firmware reboot or counter wrap
            rate = f"{delta / interval:.1f}" if interval > 0 and delta > 0 else "n/a"
        else:
            delta, state, rate = "n/a", "unknown", "n/a"
        ok = csi_active and state == "advancing"
        verified = verified or ok
        lines.append(
            f"node_id={fmt(nid)} csi_status={fmt(node.get('csi_status'))} "
            f"sequence={state} seq_delta={delta} seq_rate_hz={rate} "
            f"rssi_dbm={fmt(node.get('rssi_dbm'))} "
            f"position_configured={fmt(node.get('position_configured'))} "
            f"verified={'yes' if ok else 'no'}"
        )
    if not lines:
        lines.append("no nodes reported")
    return lines, verified


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cmd = argv[1]
    try:
        if cmd == "health" and len(argv) == 2:
            out = summarise_health(load(sys.stdin))
        elif cmd == "nodes" and len(argv) == 2:
            out = summarise_nodes(load(sys.stdin))
        elif cmd == "compare" and len(argv) == 5:
            try:
                interval = float(argv[4])
            except ValueError:
                print("interval must be a number", file=sys.stderr)
                return 2
            with open(argv[2], encoding="utf-8") as f1, open(argv[3], encoding="utf-8") as f2:
                out, verified = compare(load(f1), load(f2), interval)
            print("\n".join(out))
            return 0 if verified else 1
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
