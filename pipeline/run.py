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
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from resolve import resolve_context, load_config
from sleeper import SleeperClient
from state import State
from generators.power_rankings import build as build_power

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


def build_slim_players(client: SleeperClient, teams: list[dict],
                       extras: set[str] | None = None) -> dict:
    """player_id -> {n: name, pos, t: team} for rostered + supplied extras.

    `extras` lets the caller include players that appear in transactions
    (waivers, trades, drops) but are no longer on any current roster.
    """
    needed: set[str] = set()
    for t in teams:
        needed.update(t["players"])
    if extras:
        needed.update(extras)
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


_SLOT_LABELS = [
    ("slots_qb", "QB"), ("slots_rb", "RB"), ("slots_wr", "WR"),
    ("slots_te", "TE"), ("slots_flex", "FLEX"), ("slots_super_flex", "SFLEX"),
    ("slots_k", "K"), ("slots_def", "DEF"), ("slots_bn", "BN"),
]


def build_draft(client: SleeperClient, ctx) -> dict:
    """2026 draft hub: format, roster needs, order, and the pick board.

    Pre-draft, order/start/picks are empty; this stays valid and the
    frontend shows what's known so far. Post-draft it fills in.
    """
    if not ctx.draft_id:
        return {"available": False}
    draft = client.get_draft(ctx.draft_id)
    if not draft:
        return {"available": False}

    s = draft.get("settings", {}) or {}
    teams, _ = build_team_directory(client, ctx.current_league_id)
    by_roster = {t["roster_id"]: t for t in teams}
    league = client.get_league(ctx.current_league_id) or {}
    lsettings = league.get("settings", {}) or {}

    def team_ref(rid):
        t = by_roster.get(rid)
        return {
            "roster_id": rid,
            "team_name": t["team_name"] if t else (f"Team {rid}" if rid else "TBD"),
            "avatar": t["avatar"] if t else None,
            "manager": t["manager"] if t else None,
        }

    slot_to_roster = draft.get("slot_to_roster_id") or {}
    order = [{"slot": int(slot), **team_ref(rid)}
             for slot, rid in sorted(slot_to_roster.items(), key=lambda kv: int(kv[0]))]

    roster_slots = [{"pos": label, "count": s.get(key)}
                    for key, label in _SLOT_LABELS if s.get(key)]

    raw_picks = client.get_draft_picks(ctx.draft_id)
    picks = []
    for p in raw_picks:
        meta = p.get("metadata") or {}
        name = " ".join(x for x in [meta.get("first_name"), meta.get("last_name")] if x).strip()
        picks.append({
            "round": p.get("round"),
            "pick_no": p.get("pick_no"),
            "roster_id": p.get("roster_id"),
            "team_name": team_ref(p.get("roster_id"))["team_name"],
            "player": {"n": name or meta.get("last_name") or p.get("player_id"),
                       "pos": meta.get("position") or "?",
                       "t": meta.get("team") or ""},
            "is_keeper": bool(p.get("is_keeper")),
        })

    return {
        "available": True,
        "draft_id": ctx.draft_id,
        "season": draft.get("season") or ctx.nfl_season,
        "status": draft.get("status"),
        "type": draft.get("type"),
        "rounds": s.get("rounds"),
        "teams": s.get("teams"),
        "scoring": (draft.get("metadata") or {}).get("scoring_type"),
        "start_time": draft.get("start_time"),
        "order_finalized": draft.get("draft_order") is not None,
        "order": order,
        "roster_slots": roster_slots,
        "keepers": {
            "max_keepers": lsettings.get("max_keepers"),
            "keeper_deadline": lsettings.get("keeper_deadline"),
        },
        "picks": picks,
        "picks_made": len(picks),
    }


def build_transactions(client: SleeperClient, league_id: str,
                       weeks_total: int,
                       by_roster: dict) -> tuple[list[dict], set[str]]:
    """Chronological feed of completed transactions across the season.

    Returns (rows, needed_pids). Rows are sorted newest-first. `needed_pids`
    is the set of player IDs referenced anywhere, so the caller can extend
    the slim player map.
    """
    rows: list[dict] = []
    needed: set[str] = set()
    for wk in range(1, max(weeks_total, 0) + 1):
        for t in client.get_transactions(league_id, wk):
            if t.get("status") != "complete":
                continue  # hide failed waivers from the public wire
            adds = t.get("adds") or {}
            drops = t.get("drops") or {}
            picks = t.get("draft_picks") or []
            wb = t.get("waiver_budget") or []
            needed.update(str(p) for p in adds.keys())
            needed.update(str(p) for p in drops.keys())

            row: dict = {
                "id": t.get("transaction_id"),
                "type": t.get("type"),
                "week": t.get("leg") or wk,
                "created": t.get("created"),  # ms epoch
                "roster_ids": t.get("roster_ids") or [],
            }
            if t.get("type") == "trade":
                sides = []
                for rid in row["roster_ids"]:
                    side = by_roster.get(rid, {})
                    sides.append({
                        "roster_id": rid,
                        "team_name": side.get("team_name") or f"Team {rid}",
                        "avatar": side.get("avatar"),
                        "manager": side.get("manager"),
                        "received": {
                            "players": [str(p) for p, r in adds.items() if r == rid],
                            "picks": [_fmt_pick(p) for p in picks if p.get("owner_id") == rid],
                            "waiver_budget": [
                                {"amount": w.get("amount"), "from_roster_id": w.get("sender")}
                                for w in wb if w.get("receiver") == rid
                            ],
                        },
                    })
                row["sides"] = sides
            else:
                rid = row["roster_ids"][0] if row["roster_ids"] else None
                side = by_roster.get(rid, {}) if rid is not None else {}
                row["roster_id"] = rid
                row["team_name"] = side.get("team_name") or (f"Team {rid}" if rid else "—")
                row["avatar"] = side.get("avatar")
                row["manager"] = side.get("manager")
                row["adds"] = [str(p) for p in adds.keys()]
                row["drops"] = [str(p) for p in drops.keys()]
                if t.get("type") == "waiver":
                    row["bid"] = (t.get("settings") or {}).get("waiver_bid")
            rows.append(row)
    rows.sort(key=lambda r: r["created"] or 0, reverse=True)
    return rows, needed


def _fmt_pick(p: dict) -> dict:
    return {
        "season": p.get("season"),
        "round": p.get("round"),
        "from_roster_id": p.get("previous_owner_id"),
    }


def build_weekly_points(client: SleeperClient, league_id: str,
                        regular_weeks: int) -> dict:
    """{roster_id: [pts_wk1, ...]} over the regular season (for recent form)."""
    wp: dict[int, list] = {}
    for wk in range(1, regular_weeks + 1):
        for m in client.get_matchups(league_id, wk):
            rid = m.get("roster_id")
            if rid is None:
                continue
            wp.setdefault(rid, []).append(round(m.get("points") or 0, 2))
    return wp


def _stable_hash(obj) -> str:
    """Order-independent content hash (excludes generated_at -> idempotent)."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-league", help="override the data league id (testing)")
    ap.add_argument("--content-dir", default=str(CONTENT_DIR))
    ap.add_argument("--fake-claude", action="store_true",
                    help="force the zero-cost stub commentary (no API call)")
    args, _ignored = ap.parse_known_args()

    content_dir = Path(args.content_dir)
    client = SleeperClient()
    config = load_config()
    ctx = resolve_context(client, config)

    data_league_id = args.seed_league or ctx.data_league_id
    print(f"[run] mode={ctx.mode} data_league={data_league_id} "
          f"({ctx.data_season}, {ctx.data_status})")

    teams, _ = build_team_directory(client, data_league_id)
    by_roster = {t["roster_id"]: t for t in teams}

    if ctx.data_status == "complete":
        weeks_total = 18
    elif ctx.mode == "in_season":
        weeks_total = max(ctx.nfl_week, 0)
    else:
        weeks_total = 0
    transactions, tx_pids = build_transactions(
        client, data_league_id, weeks_total, by_roster)

    players = build_slim_players(client, teams, extras=tx_pids)
    standings = build_standings(teams)
    rosters = build_rosters(teams)
    playoffs = build_playoffs(client, data_league_id, teams)
    draft = build_draft(client, ctx)

    # Regular-season weekly points feed "recent form" in the power model.
    settings = (client.get_league(data_league_id) or {}).get("settings", {}) or {}
    pws = settings.get("playoff_week_start") or 0
    if ctx.data_status == "complete":
        regular_weeks = max(pws - 1, 0)
    elif ctx.mode == "in_season":
        regular_weeks = max(ctx.nfl_week - 1, 0)
    else:
        regular_weeks = 0
    weekly_points = (build_weekly_points(client, data_league_id, regular_weeks)
                     if regular_weeks else {})
    power_key = f"power:{ctx.data_season}:w{regular_weeks}"

    st = State()

    # Reuse AI commentary we already paid for, keyed by power_key.
    prev_power = None
    ppath = content_dir / "power.json"
    if ppath.exists():
        try:
            prev_power = json.loads(ppath.read_text())
        except (OSError, json.JSONDecodeError):
            prev_power = None
    precomputed = None
    if (not args.fake_claude and st.is_seen(power_key)
            and prev_power and prev_power.get("method") == "ai"):
        precomputed = {str(r["roster_id"]): r.get("blurb", "")
                       for r in prev_power.get("rankings", [])}

    power = build_power(teams, weekly_points, ctx.data_season,
                        use_ai=not args.fake_claude,
                        precomputed_blurbs=precomputed)

    champ = ((playoffs.get("champion") or {}).get("team_name")
             if playoffs.get("available") else None)

    # Payloads WITHOUT generated_at so the content hash is stable.
    league_payload = {
        "network": {"name": config.get("network_name", "Wet Meat League Network"),
                    "abbr": config.get("network_abbr", "WMLN")},
        "context": ctx.to_json(),
        "teams": [{k: t[k] for k in
                   ("roster_id", "owner_id", "manager", "team_name", "avatar",
                    "wins", "losses", "ties", "fpts", "fpts_against")}
                  for t in teams],
        "players": players,
    }
    standings_payload = {"season": ctx.data_season,
                         "league_name": ctx.league_name, "standings": standings}
    rosters_payload = {"season": ctx.data_season, "rosters": rosters}
    playoffs_payload = {"season": ctx.data_season,
                        "league_name": ctx.league_name, **playoffs}
    draft_payload = dict(draft)
    power_payload = power
    transactions_payload = {"season": ctx.data_season,
                            "league_name": ctx.league_name,
                            "transactions": transactions}

    n_trades = sum(1 for t in transactions if t["type"] == "trade")
    manifest_core = {
        "mode": ctx.mode,
        "data_season": ctx.data_season,
        "data_status": ctx.data_status,
        "league_name": ctx.league_name,
        "champion": champ,
        "power": {"week": regular_weeks, "method": power.get("method")},
        "transactions": {"count": len(transactions), "trades": n_trades},
        "draft": {"draft_id": ctx.draft_id,
                  "status": draft.get("status") if draft.get("available")
                  else ctx.draft_status,
                  "season": ctx.nfl_season,
                  "picks_made": draft.get("picks_made", 0)},
        "files": {
            "league": "content/league.json",
            "standings": "content/standings.json",
            "rosters": "content/rosters.json",
            "playoffs": "content/playoffs.json",
            "draft": "content/draft.json",
            "power": "content/power.json",
            "transactions": "content/transactions.json",
        },
    }

    bundle = {"league": league_payload, "standings": standings_payload,
              "rosters": rosters_payload, "playoffs": playoffs_payload,
              "draft": draft_payload, "power": power_payload,
              "transactions": transactions_payload,
              "manifest": manifest_core}
    content_hash = _stable_hash(bundle)

    if st.get_cursor("content_hash") == content_hash:
        print("[run] no content changes — nothing written (idempotent)")
        return

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(content_dir / "league.json", {"generated_at": now, **league_payload})
    write_json(content_dir / "standings.json", {"generated_at": now, **standings_payload})
    write_json(content_dir / "rosters.json", {"generated_at": now, **rosters_payload})
    write_json(content_dir / "playoffs.json", {"generated_at": now, **playoffs_payload})
    write_json(content_dir / "draft.json", {"generated_at": now, **draft_payload})
    write_json(content_dir / "transactions.json",
               {"generated_at": now, **transactions_payload})
    write_json(content_dir / "power.json", {"generated_at": now, **power_payload})
    write_json(content_dir / "power-rankings" / f"{ctx.data_season}-w{regular_weeks}.json",
               {"generated_at": now, **power_payload})
    write_json(content_dir / "manifest.json", {"generated_at": now, **manifest_core})

    st.set_resolved(ctx.to_json())
    st.set_cursor("content_hash", content_hash)
    if power.get("method") == "ai" and not st.is_seen(power_key):
        st.mark_seen(power_key, ["content/power.json"])
    st.touch_run()
    st.save()

    print(f"[run] wrote {len(teams)} teams, {len(players)} players, "
          f"{len(transactions)} transactions ({n_trades} trades); "
          f"power={power.get('method')} ({power_key})")
    if playoffs.get("available"):
        print(f"[run] {ctx.data_season} champion: {champ}")


if __name__ == "__main__":
    main()
