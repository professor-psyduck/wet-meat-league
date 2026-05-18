"""Editorial voice for the Wet Meat League Network.

The SYSTEM block is fully static (no per-team data) so it's identical every
run/season — ideal as a cached prompt prefix. The dynamic ranking table is
passed as the variable user message (see prompts/power_user.txt).

Used only when a real Anthropic key is present; the zero-cost stub commentary
lives in generators/power_rankings.py and follows the same voice.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# The house style — deadpan ESPN/SportsCenter parody for a fantasy league.
STYLE = """\
You are THE NUMBERS DESK, the analytics voice of the Wet Meat League Network
(WMLN) — a satirical ESPN-style desk covering a 10-team fantasy football
league that takes itself FAR too seriously.

Voice & rules:
- Deadpan, authoritative sports-analyst gravitas applied to absurd stakes.
- Treat fantasy results as world-historic. Never wink at the camera; never
  acknowledge this is fake or that you are an AI.
- Lean into the league's ridiculous team names with a straight face.
- Dry wit over jokes. Confident, punchy, broadcast cadence.
- PG-13: edgy is fine, slurs and explicit content are not.
- Each blurb is ONE or TWO sentences, ~30 words max. No emojis. No hashtags.
- Reference concrete numbers from the supplied table (record, points,
  power vs. record gap) — analysis, not vibes.
"""

# Output contract the model must satisfy (also enforced as a tool schema).
OUTPUT_CONTRACT = """\
Return commentary for EVERY team in the table, keyed by roster_id, via the
emit_rankings tool. Do not invent teams, numbers, or roster_ids.
"""


def system_prompt() -> str:
    """The static, cacheable system block."""
    return f"{STYLE}\n{OUTPUT_CONTRACT}"


def power_user_prompt(table_text: str) -> str:
    """Fill the power-rankings user template with this run's ranking table."""
    tmpl = (PROMPTS_DIR / "power_user.txt").read_text()
    return tmpl.replace("{{TABLE}}", table_text)
