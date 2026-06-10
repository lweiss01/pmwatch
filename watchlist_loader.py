"""
Watchlist loading with MNPI actor structure (v2) and legacy risk-string support.
"""

from __future__ import annotations

import json
from pathlib import Path

WATCHLIST_PATH = Path(__file__).parent / "watchmarket_watchlist.json"

# Default actor profiles by series when JSON omits an explicit actors array.
SERIES_DEFAULT_ACTORS: dict[str, list[dict]] = {
    "KXCABOUT": [
        {"role": "White House chief of staff", "clearance_tier": 3},
        {"role": "Cabinet member", "clearance_tier": 3},
    ],
    "KXNEXTAG": [
        {"role": "White House personnel office", "clearance_tier": 3},
        {"role": "Attorney General nominee staff", "clearance_tier": 2},
    ],
    "KXNEXTDEF": [
        {"role": "White House personnel office", "clearance_tier": 3},
        {"role": "Pentagon political appointee", "clearance_tier": 2},
    ],
    "KXNEXTSTATE": [
        {"role": "White House personnel office", "clearance_tier": 3},
        {"role": "State Department political appointee", "clearance_tier": 2},
    ],
    "KXNEXTODNI": [
        {"role": "Director of National Intelligence staff", "clearance_tier": 3},
        {"role": "Senate Intelligence Committee", "clearance_tier": 2},
    ],
    "KXTRUMPPARDONS": [
        {"role": "White House counsel", "clearance_tier": 3},
        {"role": "Pardon recipient liaison", "clearance_tier": 1},
    ],
    "KXTRUMPPARDONFAMILY": [
        {"role": "White House counsel", "clearance_tier": 3},
    ],
    "KXINSURRECTION": [
        {"role": "DoD leadership", "clearance_tier": 3},
        {"role": "DHS leadership", "clearance_tier": 3},
    ],
    "KXMARTIAL": [
        {"role": "DoD leadership", "clearance_tier": 3},
        {"role": "DHS leadership", "clearance_tier": 3},
    ],
    "KXHABEAS": [
        {"role": "DOJ leadership", "clearance_tier": 3},
    ],
    "KXWITHDRAW": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXDOED": [
        {"role": "White House policy staff", "clearance_tier": 2},
        {"role": "OMB", "clearance_tier": 2},
    ],
    "KXAGENCYELIM": [
        {"role": "White House policy staff", "clearance_tier": 2},
        {"role": "DOGE staff", "clearance_tier": 2},
    ],
    "KXEOTRUMPTERM": [
        {"role": "White House counsel", "clearance_tier": 3},
    ],
    "KXAMEND25": [
        {"role": "Cabinet member", "clearance_tier": 3},
        {"role": "White House physician", "clearance_tier": 2},
    ],
    "KXTRUMPRESIGN": [
        {"role": "White House inner circle", "clearance_tier": 3},
    ],
    "KXFED": [
        {"role": "FOMC member", "clearance_tier": 3},
        {"role": "Treasury Secretary", "clearance_tier": 3},
        {"role": "Fed Board staff", "clearance_tier": 2},
    ],
    "KXCPI": [
        {"role": "BLS staff", "clearance_tier": 3},
        {"role": "Council of Economic Advisers", "clearance_tier": 2},
    ],
    "KXGDP": [
        {"role": "BEA staff", "clearance_tier": 3},
        {"role": "Treasury staff", "clearance_tier": 2},
    ],
    "KXGREENTERRITORY": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXCANTERRITORY": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXCANAL": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "DoD policy staff", "clearance_tier": 2},
    ],
    "KXRECOGROC": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXZELENSKYPUTIN": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXUSAKIM": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXABRAHAMSA": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXABRAHAMSY": [
        {"role": "National Security Council", "clearance_tier": 3},
        {"role": "State Department leadership", "clearance_tier": 2},
    ],
    "KXHOUSE": [
        {"role": "House leadership", "clearance_tier": 2},
    ],
    "KXSENATE": [
        {"role": "Senate leadership", "clearance_tier": 2},
    ],
    "KXIMPEACH": [
        {"role": "House Judiciary Committee", "clearance_tier": 2},
    ],
    "KXVETOOVERRIDE": [
        {"role": "Congressional whip", "clearance_tier": 2},
    ],
    "KXGOVSHUT": [
        {"role": "Budget committee member", "clearance_tier": 2},
        {"role": "Appropriations committee member", "clearance_tier": 2},
    ],
    "KXCR": [
        {"role": "Appropriations committee member", "clearance_tier": 2},
    ],
    "KXSCOTUSRESIGN": [
        {"role": "Supreme Court justice", "clearance_tier": 3},
        {"role": "Supreme Court clerk", "clearance_tier": 2},
    ],
    "KXSCOTUSCHANGE": [
        {"role": "Senate Judiciary Committee", "clearance_tier": 2},
    ],
    "KXSCOURT": [
        {"role": "White House counsel", "clearance_tier": 3},
        {"role": "Senate Judiciary Committee", "clearance_tier": 2},
    ],
    "KXTARIFFS": [
        {"role": "Supreme Court justice", "clearance_tier": 3},
        {"role": "Supreme Court clerk", "clearance_tier": 2},
    ],
    "KXSCOTUSPOWER": [
        {"role": "White House counsel", "clearance_tier": 2},
        {"role": "Senate leadership", "clearance_tier": 2},
    ],
}


def infer_actors_from_risk(risk: str) -> list[dict]:
    """Fallback: split legacy risk string into tier-1 actor roles."""
    parts = [part.strip() for part in risk.split(",") if part.strip()]
    if not parts:
        return [{"role": "unknown actor", "clearance_tier": 1}]
    return [{"role": part, "clearance_tier": 1} for part in parts]


def resolve_actors(entry: dict) -> list[dict]:
    series = entry.get("series", "")
    if entry.get("actors"):
        return list(entry["actors"])
    if series in SERIES_DEFAULT_ACTORS:
        return list(SERIES_DEFAULT_ACTORS[series])
    return infer_actors_from_risk(entry.get("risk", ""))


def max_clearance_tier(actors: list[dict]) -> int:
    tiers = [int(actor.get("clearance_tier", 1)) for actor in actors]
    return max(tiers) if tiers else 1


def normalize_watchlist_entry(entry: dict, category: str) -> dict:
    risk = entry.get("risk", "")
    actors = resolve_actors(entry)
    clearance_tier = max_clearance_tier(actors)
    return {
        "series": entry["series"],
        "name": entry["name"],
        "category": category,
        "risk": risk,
        "mnpi_actors": risk,
        "actors": actors,
        "clearance_tier": clearance_tier,
        "actors_json": json.dumps(actors, separators=(",", ":")),
    }


def load_watchlist(path: Path | None = None) -> list[dict]:
    """Load watchlist entries with normalized actor metadata."""
    watchlist_path = path or WATCHLIST_PATH
    with open(watchlist_path, "r", encoding="utf-8-sig") as f:
        raw = json.load(f)

    markets: list[dict] = []
    for category, entries in raw.items():
        for entry in entries:
            markets.append(normalize_watchlist_entry(entry, category))
    return markets
