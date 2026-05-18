"""Pipeline orchestrator.

Phase B: fetch the resolved "current" league from Sleeper and write the small
JSON files the static site reads:

  content/manifest.json   index + run/context summary (frontend loads first)
  content/league.json     network meta, resolved context, team directory,
                          and a SLIM player map (rostered players only)
  content/standings.json  computed standings table
  content/rosters.json    starters/bench (player ids) per team

No AI here. Idempotent: writing identical bytes leaves the git tree unchanged,
so the deploy Action only commits on real changes.

Usage:
  python pipeline/run.py [--seed-league <id>] [--content-dir DIR]
  (unknown flags from later phases, e.g. --fake-claude, are ignored)
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from resolve import resolve_context, load_config
from sleeper import SleeperClient
from state import State

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = REPO_ROOT / "content"


def write_json(path: Path, obj) -> None:
    """Atomically write pretty JSON (stable key order -> stable git diffs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _team_name(user: dict | None, roster_id: int) -> str:
    if user:
        meta = user.get("metadata") or {}
        return meta.get("team_name") or user.get("display_name") or f"Team {roster_id}"
    return f"Team {roster_id}"


def _team_avatar(client: SleeperClient, user: dict | None) -> str | None:
    if not user:
        return None
    meta = user.get("metadata") or {}
    custom = meta.get("avatar")
    if custom and str(custom).startswith("http"):
        return custom
    return client.avatar_url(user.get("avatar"), thumb=True)


def _points(settings: dict, base: str) -> float:
    """Sleeper splits points into integer + hundredths fields."""
    return round(settings.get(base, 0) + settings.get(f"{base}_decimal", 0) / 100.0, 2)


def build_team_directory(client: SleeperClient, league_id: str) -> tuple[list[dict], dict]:
    """Return (teams, users_by_id). Teams join rosters + users."""
    users = client.get_league_users(league_id)
    rosters = client.get_league_rosters(league_id)
    by_id = {u["user_id"]: u for u in users}

    teams = []
    for r in rosters:
        rid = r.get("roster_id")
        user = by_id.get(r.get("owner_id"))
        s = r.get("settings", {}) or {}
        teams.append({
            "roster_id": rid,
            "owner_id": r.get("owner_id"),
            "manager": (user or {}).get("display_name") if user else None,
            "team_name": _team_name(user, rid),
            "avatar": _team_avatar(client, user),
            "wins": s.get("wins", 0),
            "losses": s.get("losses", 0),
            "ties": s.get("ties", 0),
            "fpts": _points(s, "fpts"),
            "fpts_against": _points(s, "fpts_against"),
            "starters": [p for p in (r.get("starters") or []) if p and p != "0"],
            "players": [p for p in (r.get("players") or []) if p and p != "0"],
        })
    teams.sort(key=lambda t: t["roster_id"] or 0)
    return teams, by_id


def build_slim_players(client: SleeperClient, teams: list[dict]) -> dict:
    """player_id -> {n: name, pos, t: team} for ROSTERED players only."""
    needed: set[str] = set()
    for t in teams:
        needed.update(t["players"])
    if not needed:
        return {}
    allp = client.get_players()
    slim = {}
    for pid in needed:
        p = allp.get(pid)
        if not p:
            # Team defenses come through as the team abbrev (e.g. "DET").
            slim[pid] = {"n": pid, "pos": "DEF" if pid.isalpha() else "?", "t": pid if pid.isalpha() else ""}
            continue
        name = " ".join(x for x in [p.get("first_name"), p.get("last_name")] if x).strip()
        slim[pid] = {
            "n": name or p.get("last_name") or pid,
            "pos": p.get("position") or "?",
            "t": p.get("team") or "",
        }
    return slim


def build_standings(teams: list[dict]) -> list[dict]:
    """Rank by wins, then points-for (Sleeper's usual tiebreak)."""
    ranked = sorted(teams, key=lambda t: (t["wins"], t["fpts"]), reverse=True)
    out = []
    for i, t in enumerate(ranked, 1):
        gp = t["wins"] + t["losses"] + t["ties"]
        out.append({
            "rank": i,
            "roster_id": t["roster_id"],
            "team_name": t["team_name"],
            "manager": t["manager"],
            "avatar": t["avatar"],
            "wins": t["wins"],
            "losses": t["losses"],
            "ties": t["ties"],
            "pct": round(t["wins"] / gp, 3) if gp else 0.0,
            "fpts": t["fpts"],
            "fpts_against": t["fpts_against"],
        })
    return out


def build_rosters(teams: list[dict]) -> list[dict]:
    return [{
        "roster_id": t["roster_id"],
        "team_name": t["team_name"],
        "manager": t["manager"],
        "avatar": t["avatar"],
        "record": {"wins": t["wins"], "losses": t["losses"], "ties": t["ties"]},
        "starters": t["starters"],
        "players": t["players"],
    } for t in teams]


def build_playoffs(client: SleeperClient, league_id: str, teams: list[dict]) -> dict:
    """Winners-bracket playoffs with per-game scores and final placements.

    Sleeper bracket games carry r(ound), m(atch), t1/t2 (roster ids), w/l
    (winner/loser), and p (final placement: 1=title, 3=3rd, 5=5th). With
    single-week rounds, round r is played in week playoff_week_start+(r-1),
    so we can attach each team's points from that week's matchups.
    """
    bracket = client.get_winners_bracket(league_id)
    if not bracket:
        return {"available": False}

    by_roster = {t["roster_id"]: t for t in teams}
    settings = (client.get_league(league_id) or {}).get("settings", {}) or {}
    pws = settings.get("playoff_week_start") or 0
    single_week = settings.get("playoff_round_type", 0) == 0
    rounds_present = sorted({g["r"] for g in bracket})
    max_r = max(rounds_present) if rounds_present else 0

    week_points: dict[int, dict] = {}
    if single_week and pws:
        for r in rounds_present:
            mus = client.get_matchups(league_id, pws + (r - 1))
            week_points[r] = {m["roster_id"]: round(m.get("points") or 0, 2)
                              for m in mus}

    def team_ref(rid, r):
        t = by_roster.get(rid)
        return {
            "roster_id": rid,
            "team_name": t["team_name"] if t else ("TBD" if not rid else f"Team {rid}"),
            "avatar": t["avatar"] if t else None,
            "manager": t["manager"] if t else None,
            "score": week_points.get(r, {}).get(rid),
        }

    rounds = []
    for r in rounds_present:
        games = []
        for g in (x for x in bracket if x["r"] == r):
            games.append({
                "m": g["m"],
                "placement": g.get("p"),
                "t1": team_ref(g.get("t1"), r),
                "t2": team_ref(g.get("t2"), r),
                "winner_roster_id": g.get("w"),
                "loser_roster_id": g.get("l"),
            })
        games.sort(key=lambda x: (x["placement"] or 99, x["m"]))
        rounds.append({
            "round": r,
            "week": (pws + (r - 1)) if (single_week and pws) else None,
            "name": "Championship" if r == max_r else (
                "Semifinals" if r == max_r - 1 else f"Round {r}"),
            "games": games,
        })

    champion = runner_up = third = None
    for g in bracket:
        if g.get("p") == 1:
            champion = team_ref(g.get("w"), g["r"])
            runner_up = team_ref(g.get("l"), g["r"])
        elif g.get("p") == 3:
            third = team_ref(g.get("w"), g["r"])

    return {
        "available": True,
        "champion": champion,
        "runner_up": runner_up,
        "third": third,
        "rounds": rounds,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-league", help="override the data league id (testing)")
    ap.add_argument("--content-dir", default=str(CONTENT_DIR))
    args, _ignored = ap.parse_known_args()

    content_dir = Path(args.content_dir)
    client = SleeperClient()
    config = load_config()
    ctx = resolve_context(client, config)

    data_league_id = args.seed_league or ctx.data_league_id
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[run] mode={ctx.mode} data_league={data_league_id} "
          f"({ctx.data_season}, {ctx.data_status})")

    teams, _ = build_team_directory(client, data_league_id)
    players = build_slim_players(client, teams)
    standings = build_standings(teams)
    rosters = build_rosters(teams)
    playoffs = build_playoffs(client, data_league_id, teams)

    league_json = {
        "generated_at": now,
        "network": {
            "name": config.get("network_name", "Wet Meat League Network"),
            "abbr": config.get("network_abbr", "WMLN"),
        },
        "context": ctx.to_json(),
        "teams": [{k: t[k] for k in
                   ("roster_id", "owner_id", "manager", "team_name", "avatar",
                    "wins", "losses", "ties", "fpts", "fpts_against")}
                  for t in teams],
        "players": players,
    }

    write_json(content_dir / "league.json", league_json)
    write_json(content_dir / "standings.json",
               {"generated_at": now, "season": ctx.data_season,
                "league_name": ctx.league_name, "standings": standings})
    write_json(content_dir / "rosters.json",
               {"generated_at": now, "season": ctx.data_season, "rosters": rosters})
    write_json(content_dir / "playoffs.json",
               {"generated_at": now, "season": ctx.data_season,
                "league_name": ctx.league_name, **playoffs})

    champ = (playoffs.get("champion") or {}).get("team_name") if playoffs.get("available") else None
    manifest = {
        "generated_at": now,
        "mode": ctx.mode,
        "data_season": ctx.data_season,
        "data_status": ctx.data_status,
        "league_name": ctx.league_name,
        "champion": champ,
        "draft": {"draft_id": ctx.draft_id, "status": ctx.draft_status,
                  "season": ctx.nfl_season},
        "files": {
            "league": "content/league.json",
            "standings": "content/standings.json",
            "rosters": "content/rosters.json",
            "playoffs": "content/playoffs.json",
        },
    }
    write_json(content_dir / "manifest.json", manifest)

    st = State()
    st.set_resolved(ctx.to_json())
    st.touch_run()
    st.save()

    print(f"[run] wrote {len(teams)} teams, {len(players)} players, "
          f"standings + rosters -> {content_dir}")
    if playoffs.get("available"):
        print(f"[run] {ctx.data_season} champion: {champ}")


if __name__ == "__main__":
    main()
