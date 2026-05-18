"""Anthropic SDK wrapper — AI power-rankings commentary.

Only used when ANTHROPIC_API_KEY is set. The `anthropic` package is imported
lazily so the rest of the pipeline (and local zero-spend runs) need no install.

Design (per the claude-api skill):
- Forced single tool `emit_rankings` with a strict schema → guaranteed JSON,
  no prose parsing.
- The static persona/style is sent as a `system` block with
  `cache_control: ephemeral`. NOTE: the persona is small and runs are far
  apart, so this rarely actually caches (below the min cacheable prefix and
  past the 5-min TTL) — it's structured correctly but caching is a no-op here
  in practice; that's fine, it's one call per run.
- Thinking disabled: this is a simple forced-tool extraction, not reasoning.
- Model is configurable; defaults to Claude Sonnet 4.6 per the project plan
  (routine satirical copy — Opus is reserved for later flagship generators).
"""

from __future__ import annotations

import os

DEFAULT_MODEL = "claude-sonnet-4-6"

_EMIT_TOOL = {
    "name": "emit_rankings",
    "description": "Return the commentary blurb for every team in the table.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "commentary": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "roster_id": {"type": "integer"},
                        "blurb": {"type": "string"},
                    },
                    "required": ["roster_id", "blurb"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["commentary"],
        "additionalProperties": False,
    },
}


def is_available() -> bool:
    """True iff a key is set AND the anthropic SDK can be imported."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def generate_commentary(
    system_text: str,
    user_text: str,
    model: str = DEFAULT_MODEL,
) -> dict[str, str]:
    """Call Claude once; return {roster_id(str): blurb}.

    Raises on any failure so the caller can fall back to the stub.
    """
    import anthropic  # lazy

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        thinking={"type": "disabled"},
        system=[{
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_text}],
        tools=[_EMIT_TOOL],
        tool_choice={"type": "tool", "name": "emit_rankings"},
    )

    tool_block = next(
        (b for b in resp.content if b.type == "tool_use" and b.name == "emit_rankings"),
        None,
    )
    if tool_block is None:
        raise RuntimeError(f"Claude did not call emit_rankings (stop={resp.stop_reason})")

    out: dict[str, str] = {}
    for row in tool_block.input.get("commentary", []):
        rid = row.get("roster_id")
        blurb = (row.get("blurb") or "").strip()
        if rid is not None and blurb:
            out[str(rid)] = blurb
    if not out:
        raise RuntimeError("emit_rankings returned no usable commentary")
    return out
