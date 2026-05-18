"""Season-aware resolution: decide which league powers "current" content.

The configured `current_league_id` is the live 2026 Sleeper league. While it's
pre-draft / the NFL is in the offseason, the site shows the most recent
*completed* season (walk `previous_league_id`) as the "current" dataset, and
exposes the 2026 league + draft separately for the Draft section. Once the
2026 season is live, "current" automatically switches to it.

All season/mode logic lives here so the rest of the pipeline stays dumb.

Smoke test:  python pipeline/resolve.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from sleeper import SleeperClient

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "league.config.json"

_MAX_CHAIN = 12  # safety cap when following previous_league_id
_IN_SEASON_TYPES = {"regular", "post"}


def load_config() -> dict:
    """Read config/league.config.json (with a safe default for the league id)."""
    default = {"current_league_id": "1357559174707281920"}
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return {**default, **cfg}
    except (OSError, json.JSONDecodeError):
        return default


@dataclass
class Context:
    """Resolved season context for one pipeline run."""

    mode: str                 # "offseason" | "in_season"
    nfl_season: str           # e.g. "2026"
    nfl_week: int             # 0 in the offseason
    season_type: str          # "off" | "pre" | "regular" | "post"

    current_league_id: str    # the live 2026 league (config)
    draft_id: str | None      # 2026 draft id (for the Draft section)
    draft_status: str | None  # 2026 league status, e.g. "pre_draft"

    data_league_id: str       # league powering standings/rosters NOW
    data_season: str          # season of data_league, e.g. "2025"
    data_status: str          # status of data_league, e.g. "complete"
    league_name: str          # display name

    chain: list[str]          # data_league back through previous_league_id

    def to_json(self) -> dict:
        return asdict(self)


def _build_chain(client: SleeperClient, start_id: str) -> list[tuple[str, dict]]:
    """[(league_id, league_obj), ...] from start back through previous_league_id."""
    out: list[tuple[str, dict]] = []
    lid: str | None = start_id
    seen: set[str] = set()
    while lid and lid not in seen and len(out) < _MAX_CHAIN:
        seen.add(lid)
        lg = client.get_league(lid)
        if not lg:
            break
        out.append((lid, lg))
        lid = lg.get("previous_league_id")
    return out


def resolve_context(client: SleeperClient, config: dict | None = None) -> Context:
    """Determine the mode and which league powers 'current' content."""
    config = config or load_config()
    current_id = str(config["current_league_id"])

    state = client.get_state() or {}
    nfl_season = str(state.get("season") or "")
    season_type = str(state.get("season_type") or "off")
    nfl_week = int(state.get("week") or 0)

    chain = _build_chain(client, current_id)
    if not chain:
        raise RuntimeError(f"Could not load current league {current_id} from Sleeper")

    current_lg = chain[0][1]
    draft_id = current_lg.get("draft_id")
    draft_status = current_lg.get("status")

    # IN-SEASON: the configured league's own season is live and it has begun.
    current_live = (
        season_type in _IN_SEASON_TYPES
        and str(current_lg.get("season")) == nfl_season
        and current_lg.get("status") in {"in_season", "complete"}
    )

    if current_live:
        mode = "in_season"
        data_id, data_lg = chain[0]
    else:
        mode = "offseason"
        # First completed league walking back (skip the not-yet-started 2026).
        completed = [(lid, lg) for lid, lg in chain if lg.get("status") == "complete"]
        data_id, data_lg = completed[0] if completed else chain[0]

    return Context(
        mode=mode,
        nfl_season=nfl_season,
        nfl_week=nfl_week,
        season_type=season_type,
        current_league_id=current_id,
        draft_id=draft_id,
        draft_status=draft_status,
        data_league_id=data_id,
        data_season=str(data_lg.get("season") or ""),
        data_status=str(data_lg.get("status") or ""),
        league_name=str(data_lg.get("name") or "League"),
        chain=[lid for lid, _ in chain],
    )


if __name__ == "__main__":
    ctx = resolve_context(SleeperClient())
    print(json.dumps(ctx.to_json(), indent=2))
