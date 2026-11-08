"""Combine per-node near-zone reports into one zone summary.

One link sees motion anywhere in radio range, so background activity can raise a
false candidate on it. Two links seen from different angles rarely raise one at the
same moment from the same far-away cause, so the zone summary only says
CANDIDATE when at least two judging links agree. A candidate on a single link out
of several is still shown, as a WEAK candidate: hiding it could hide a real person.

The "near zone" of two links is roughly where both links' near zones overlap,
which depends on how the nodes are placed. That geometry is NOT measured here.

No result here is ever evidence that nobody is present.
"""
from __future__ import annotations

JUDGING = ("candidate_present", "no_candidate", "changed")
AGREE = 2           # links that must agree for a full CANDIDATE when two or more are judging

LABELS = {
    "candidate_present": "CANDIDATE: verify. Not a confirmed person; no location; no count.",
    "weak_candidate": "WEAK CANDIDATE (one link only, others disagree): verify.",
    "no_candidate": "No candidate. This is NOT evidence that nobody is there.",
    "not_judging": "Not judging: no calibrated node with a usable link.",
    "changed": "CHANGED since calibration on every judging link: a person, a moved object or drift. Verify.",
}


def fuse(reports: dict) -> dict:
    """reports: {node_id: NearZoneEngine.assess() dict}. Returns the zone summary."""
    judging = {n: r for n, r in reports.items() if r.get("state") in JUDGING}
    cands = sorted(n for n, r in judging.items() if r["state"] == "candidate_present")
    idle = {n: r.get("state", "unknown") for n, r in reports.items() if n not in judging}
    if not judging:
        state, basis = "not_judging", "no node is calibrated with a usable link"
    elif len(judging) == 1:
        state = next(iter(judging.values()))["state"]
        basis = "single link: background motion causes more false candidates than with two"
    elif len(cands) >= AGREE:
        state, basis = "candidate_present", f"{len(cands)} of {len(judging)} links agree"
    elif cands:
        state, basis = "weak_candidate", f"only 1 of {len(judging)} links (node {cands[0]})"
    elif all(r["state"] == "changed" for r in judging.values()):
        state, basis = "changed", f"{len(judging)} of {len(judging)} links changed, none with movement"
    else:
        state, basis = "no_candidate", f"0 of {len(judging)} links"
    return dict(state=state, basis=basis, label=LABELS[state], links_judging=sorted(judging),
                candidate_links=cands, links_not_judging=idle)


def combine(wifi_zone: dict, radar: dict | None) -> dict:
    """Overall answer from the Wi-Fi zone summary and the radar report (radar_service status).

    The radar sees still, breathing people and gives a range; Wi-Fi CSI only sees movement here
    (measured 2026-11-07). So a radar candidate is a CANDIDATE, Wi-Fi movement alone is a WEAK
    candidate, and when the radar is not judging the Wi-Fi summary stands alone.
    """
    rs = (radar or {}).get("state")
    wifi_cand = wifi_zone.get("state") in ("candidate_present", "weak_candidate")
    base = dict(wifi=wifi_zone.get("state"), radar=rs)
    if rs not in ("candidate_present", "no_candidate"):
        return dict(wifi_zone, sources=dict(base, note="radar not judging: Wi-Fi only"))
    if rs == "candidate_present":
        where = f"radar: {radar.get('kind') or 'candidate'} at ~{radar.get('range_m')} m"
        return dict(state="candidate_present", basis=where + ("; Wi-Fi sees movement too" if wifi_cand else ""),
                    label="CANDIDATE: verify. Not a confirmed person; range only; no count.",
                    range_m=radar.get("range_m"), sources=base)
    if wifi_cand:
        return dict(state="weak_candidate", basis="Wi-Fi movement only; the radar sees nobody in its view",
                    label=LABELS["weak_candidate"], sources=base)
    return dict(state="no_candidate", basis="radar and Wi-Fi: no candidate", label=LABELS["no_candidate"], sources=base)
