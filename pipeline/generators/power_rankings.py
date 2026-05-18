"""Power rankings: a transparent computed score + commentary.

The SCORE is pure math (no AI) so it's explainable and idempotent:

    power = 0.45*win%  + 0.30*PF  + 0.10*(1 - PA)  + 0.15*recent_form

each component min-max normalized across the league (ties -> 0.5). `trend`
is (standings_rank - power_rank): positive means the numbers rate a team
higher than its W-L does (overperforming schedule / underrated).

COMMENTARY uses Claude when ANTHROPIC_API_KEY is set; otherwise a
deterministic in-world stub in the same "Numbers Desk" voice (zero cost,
stable bytes -> idempotent).

Pure module (no Sleeper calls) so it's unit-testable.

Smoke test:  python pipeline/generators/power_rankings.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # import siblings

import claude_client  # noqa: E402
import personas  # noqa: E402

_W_WINPCT, _W_PF, _W_PA, _W_RECENT = 0.45, 0.30, 0.10, 0.15


def _norm(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def compute(teams: list[dict], weekly_points: dict | None = None) -> list[dict]:
    """Return teams ranked by computed power score (desc)."""
    weekly_points = weekly_points or {}
    rows = []
    for t in teams:
        g = t["wins"] + t["losses"] + t["ties"]
        wp = (t["wins"] + 0.5 * t["ties"]) / g if g else 0.0
        wk = weekly_points.get(t["roster_id"]) or []
        recent = sum(wk[-3:]) / len(wk[-3:]) if wk else (t["fpts"] / g if g else 0.0)
        rows.append({"t": t, "wp": wp, "pf": t["fpts"],
                     "pa": t["fpts_against"], "recent": recent})

    wp_n = _norm([r["wp"] for r in rows])
    pf_n = _norm([r["pf"] for r in rows])
    pa_n = _norm([r["pa"] for r in rows])
    rc_n = _norm([r["recent"] for r in rows])

    for i, r in enumerate(rows):
        r["power"] = (_W_WINPCT * wp_n[i] + _W_PF * pf_n[i]
                      + _W_PA * (1 - pa_n[i]) + _W_RECENT * rc_n[i])

    # Standings order (wins, then PF) — to compute trend vs the record.
    standings_order = sorted(
        range(len(rows)),
        key=lambda i: (rows[i]["t"]["wins"], rows[i]["pf"]), reverse=True)
    standings_rank = {idx: rk for rk, idx in enumerate(standings_order, 1)}

    ranked = sorted(range(len(rows)), key=lambda i: rows[i]["power"], reverse=True)
    out = []
    for rank, i in enumerate(ranked, 1):
        r, t = rows[i], rows[i]["t"]
        out.append({
            "rank": rank,
            "roster_id": t["roster_id"],
            "team_name": t["team_name"],
            "manager": t["manager"],
            "avatar": t["avatar"],
            "record": {"wins": t["wins"], "losses": t["losses"], "ties": t["ties"]},
            "fpts": t["fpts"],
            "fpts_against": t["fpts_against"],
            "power": round(r["power"] * 100, 1),
            "standings_rank": standings_rank[i],
            "trend": standings_rank[i] - rank,  # + => power > record
        })
    return out


def _rec(r: dict) -> str:
    w, l, ti = r["record"]["wins"], r["record"]["losses"], r["record"]["ties"]
    return f"{w}-{l}-{ti}" if ti else f"{w}-{l}"


def stub_commentary(ranked: list[dict]) -> dict[str, str]:
    """Deterministic in-world blurbs (no AI, stable bytes)."""
    n = len(ranked)
    out = {}
    for r in ranked:
        name, rec, pf = r["team_name"], _rec(r), r["fpts"]
        tr, rk = r["trend"], r["rank"]
        if rk == 1:
            blurb = (f"{name} sit atop the Wet Meat League at {rec}. "
                     f"{pf} points for — everyone else is, by definition, wet meat.")
        elif rk == n:
            blurb = (f"Dead last. {name} at {rec}, {pf} PF. "
                     f"The desk reviewed the tape and now regrets it.")
        elif tr >= 3:
            blurb = (f"The numbers love {name} more than the standings do — "
                     f"#{rk} by power, #{r['standings_rank']} by record. Buy in.")
        elif tr <= -3:
            blurb = (f"{name}'s {rec} is doing heavy lifting the tape doesn't "
                     f"support. The desk has them at #{rk}. Sell.")
        elif rk <= n // 3:
            blurb = (f"{name}: {rec}, {pf} PF. A legitimate contender, and "
                     f"insufferable about it.")
        else:
            blurb = (f"{name} ({rec}, {pf} PF) — competent, unspectacular, "
                     f"the team that quietly ruins your week.")
        out[str(r["roster_id"])] = blurb
    return out


def _table_text(ranked: list[dict]) -> str:
    lines = []
    for r in ranked:
        lines.append(
            f"power#{r['rank']} roster_id={r['roster_id']} \"{r['team_name']}\" "
            f"rec={_rec(r)} PF={r['fpts']} PA={r['fpts_against']} "
            f"recordRank#{r['standings_rank']} trend={r['trend']:+d}")
    return "\n".join(lines)


def build(teams: list[dict], weekly_points: dict, season: str,
          use_ai: bool = True, model: str | None = None,
          precomputed_blurbs: dict | None = None) -> dict:
    """Compute rankings + attach commentary.

    `precomputed_blurbs` (roster_id str -> blurb) lets the caller reuse AI
    commentary it already paid for, so re-runs make zero API calls.
    """
    ranked = compute(teams, weekly_points)
    method = "computed"
    used_model = None

    if (precomputed_blurbs
            and all(str(r["roster_id"]) in precomputed_blurbs for r in ranked)):
        blurbs = precomputed_blurbs
        method = "ai"  # reused cached AI commentary
    elif use_ai and claude_client.is_available():
        try:
            mdl = model or claude_client.DEFAULT_MODEL
            blurbs = claude_client.generate_commentary(
                personas.system_prompt(),
                personas.power_user_prompt(_table_text(ranked)),
                model=mdl,
            )
            # Require coverage; otherwise fall back rather than ship gaps.
            if all(str(r["roster_id"]) in blurbs for r in ranked):
                method, used_model = "ai", mdl
            else:
                blurbs = stub_commentary(ranked)
        except Exception as e:  # any SDK/parse/coverage failure -> stub
            print(f"[power] AI commentary failed ({e}); using stub")
            blurbs = stub_commentary(ranked)
    else:
        blurbs = stub_commentary(ranked)

    for r in ranked:
        r["blurb"] = blurbs.get(str(r["roster_id"]), "")
    return {"season": season, "method": method, "model": used_model,
            "rankings": ranked}


if __name__ == "__main__":
    import json
    demo = [
        {"roster_id": 1, "team_name": "Fully Torqued", "manager": "a", "avatar": None,
         "wins": 10, "losses": 4, "ties": 0, "fpts": 1887.8, "fpts_against": 1600.0},
        {"roster_id": 8, "team_name": "Brave Mujahideen Fighters", "manager": "b",
         "avatar": None, "wins": 9, "losses": 5, "ties": 0, "fpts": 1700.0,
         "fpts_against": 1500.0},
        {"roster_id": 5, "team_name": "Amon-Ra Doggin ", "manager": "c", "avatar": None,
         "wins": 4, "losses": 10, "ties": 0, "fpts": 1400.0, "fpts_against": 1850.0},
    ]
    res = build(demo, {}, "2025", use_ai=False)
    print("method:", res["method"])
    for r in res["rankings"]:
        print(f"  #{r['rank']} {r['team_name']:<26} pow={r['power']:>5} "
              f"trend={r['trend']:+d}  {r['blurb']}")
