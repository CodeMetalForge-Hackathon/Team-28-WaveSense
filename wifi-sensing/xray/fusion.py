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

JUDGING = ("candidate_present", "no_candidate")
AGREE = 2           # links that must agree for a full CANDIDATE when two or more are judging

LABELS = {
    "candidate_present": "CANDIDATE: verify. Not a confirmed person; no location; no count.",
    "weak_candidate": "WEAK CANDIDATE (one link only, others disagree): verify.",
    "no_candidate": "No candidate. This is NOT evidence that nobody is there.",
    "not_judging": "Not judging: no calibrated node with a usable link.",
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
    else:
        state, basis = "no_candidate", f"0 of {len(judging)} links"
    return dict(state=state, basis=basis, label=LABELS[state], links_judging=sorted(judging),
                candidate_links=cands, links_not_judging=idle)
