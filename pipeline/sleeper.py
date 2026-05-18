"""Read-only client for the public Sleeper API (https://docs.sleeper.com/).

No auth, all GET, CORS-enabled, ~1000 req/min limit. Stdlib only so the core
site pipeline runs with zero `pip install`. Within a run, responses are cached
in memory; the large /players/nfl dump (~5MB) is cached on disk under
`.cache/` (gitignored) so dev iterations don't re-download it.

Smoke test:  python pipeline/sleeper.py
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.sleeper.app/v1"
AVATAR_BASE = "https://sleepercdn.com/avatars"

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / ".cache"

_USER_AGENT = "wet-meat-league-network/1.0 (+https://professor-psyduck.github.io/wet-meat-league/)"
_TIMEOUT = 20
_MAX_RETRIES = 4
_POLITE_DELAY = 0.05  # seconds between network calls; well under the rate limit


class SleeperError(RuntimeError):
    """Raised when the Sleeper API cannot be reached or returns bad data."""


class SleeperClient:
    """Thin, cached, read-only wrapper over the Sleeper v1 API."""

    def __init__(self) -> None:
        self._cache: dict[str, object] = {}

    # ---- core HTTP -----------------------------------------------------
    def _get(self, path: str):
        """GET `${BASE}/<path>`, parse JSON, with retry/backoff and per-run cache."""
        if path in self._cache:
            return self._cache[path]

        url = f"{BASE}/{path.lstrip('/')}"
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                time.sleep(_POLITE_DELAY)
                req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    raw = resp.read()
                data = json.loads(raw) if raw else None
                self._cache[path] = data
                return data
            except urllib.error.HTTPError as e:
                last_err = e
                # 404 -> a real "not found"; don't retry, return None.
                if e.code == 404:
                    self._cache[path] = None
                    return None
                # 429 / 5xx -> back off and retry.
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2 ** attempt)

        raise SleeperError(f"GET {url} failed after {_MAX_RETRIES} tries: {last_err}")

    # ---- NFL state -----------------------------------------------------
    def get_state(self) -> dict:
        """Current season/week: {season, week, season_type, ...}."""
        return self._get("state/nfl") or {}

    # ---- league --------------------------------------------------------
    def get_league(self, league_id: str) -> dict | None:
        """Single league object, or None if the id is unknown."""
        return self._get(f"league/{league_id}")

    def get_league_users(self, league_id: str) -> list[dict]:
        """League members (owner_id, display_name, metadata.team_name, avatar)."""
        return self._get(f"league/{league_id}/users") or []

    def get_league_rosters(self, league_id: str) -> list[dict]:
        """Rosters incl. settings (wins/losses/ties, fpts, fpts_against)."""
        return self._get(f"league/{league_id}/rosters") or []

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        """Per-roster matchup entries for a week (matchup_id, points)."""
        return self._get(f"league/{league_id}/matchups/{week}") or []

    def get_winners_bracket(self, league_id: str) -> list[dict]:
        return self._get(f"league/{league_id}/winners_bracket") or []

    # ---- draft (used in Phase C) --------------------------------------
    def get_draft(self, draft_id: str) -> dict | None:
        return self._get(f"draft/{draft_id}")

    def get_draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"draft/{draft_id}/picks") or []

    # ---- players (big; disk-cached) -----------------------------------
    def get_players(self, max_age_seconds: int = 86_400, force: bool = False) -> dict:
        """Full NFL player map (player_id -> {first_name,last_name,position,team,...}).

        ~5MB. Cached on disk under `.cache/players_nfl.json`; only re-downloaded
        when missing, older than `max_age_seconds`, or `force=True`. The pipeline
        slims this into content/ — the browser never downloads the full map.
        """
        cache_file = CACHE_DIR / "players_nfl.json"
        if not force and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < max_age_seconds:
                try:
                    return json.loads(cache_file.read_text())
                except (OSError, json.JSONDecodeError):
                    pass  # fall through and re-fetch

        data = self._get("players/nfl") or {}
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))
        except OSError:
            pass  # cache is best-effort
        return data

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def avatar_url(avatar_id: str | None, thumb: bool = False) -> str | None:
        """Sleeper avatar CDN URL, or None if no avatar."""
        if not avatar_id:
            return None
        return f"{AVATAR_BASE}/{'thumbs/' if thumb else ''}{avatar_id}"


if __name__ == "__main__":
    # Quick smoke test against the real, public API.
    c = SleeperClient()
    state = c.get_state()
    print(f"NFL state: season={state.get('season')} "
          f"week={state.get('week')} type={state.get('season_type')}")

    LID_2025 = "1180170315099144192"  # completed "Wet Meat League 2.0"
    lg = c.get_league(LID_2025)
    print(f"League: {lg.get('name')} | season={lg.get('season')} "
          f"| status={lg.get('status')} | prev={lg.get('previous_league_id')}")

    users = c.get_league_users(LID_2025)
    rosters = c.get_league_rosters(LID_2025)
    print(f"users={len(users)} rosters={len(rosters)}")
    by_owner = {u["user_id"]: u for u in users}
    top = sorted(
        rosters,
        key=lambda r: (r.get("settings", {}).get("wins", 0),
                       r.get("settings", {}).get("fpts", 0)),
        reverse=True,
    )[:3]
    for r in top:
        u = by_owner.get(r.get("owner_id"), {})
        s = r.get("settings", {})
        name = (u.get("metadata") or {}).get("team_name") or u.get("display_name") or "?"
        print(f"  #{r['roster_id']:>2} {name:<28} "
              f"{s.get('wins',0)}-{s.get('losses',0)}  PF={s.get('fpts',0)}")

    print(f"avatar example: {c.avatar_url(lg.get('avatar'))}")
