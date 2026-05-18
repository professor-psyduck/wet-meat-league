"""Persistent pipeline state (`state/state.json`, committed to the repo).

Phase B uses this lightly: record the resolved season context and the last run
time. The `seen` (event dedup) and `video_jobs` (async video queue) structures
are created now so later phases extend them without a migration.

Writes are atomic (temp file + os.replace) so a killed run can't corrupt state.

Smoke test:  python pipeline/state.py
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "state" / "state.json"

SCHEMA = 1


def _default_state() -> dict:
    return {
        "schema": SCHEMA,
        "resolved": {},      # last resolved Context (see resolve.py)
        "seen": {},          # event_key -> {generated_at, files:[...]}  (Phase D/E)
        "video_jobs": [],    # pending async video jobs                 (Phase F)
        "cursors": {},       # misc progress markers
        "last_run": None,    # ISO8601 of last successful run
    }


class State:
    """Load / mutate / atomically save the pipeline state file."""

    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        try:
            loaded = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return _default_state()
        # Merge onto defaults so new keys appear for old state files.
        merged = _default_state()
        merged.update({k: loaded[k] for k in loaded if k in merged})
        merged["schema"] = SCHEMA
        return merged

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---- convenience accessors ----------------------------------------
    def set_resolved(self, ctx: dict) -> None:
        self.data["resolved"] = ctx

    def touch_run(self) -> None:
        self.data["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ---- event dedup (used from Phase D onward) ------------------------
    def is_seen(self, key: str) -> bool:
        return key in self.data["seen"]

    def mark_seen(self, key: str, files: list[str] | None = None) -> None:
        self.data["seen"][key] = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files": files or [],
        }

    def get_cursor(self, name: str, default=None):
        return self.data["cursors"].get(name, default)

    def set_cursor(self, name: str, value) -> None:
        self.data["cursors"][name] = value


if __name__ == "__main__":
    s = State()
    print(f"state file: {s.path}")
    print(f"exists: {s.path.exists()}  schema: {s.data['schema']}  "
          f"last_run: {s.data['last_run']}  seen: {len(s.data['seen'])}")
    # Round-trip without clobbering meaningful data.
    s.touch_run()
    s.save()
    print(f"saved. last_run -> {s.data['last_run']}")
