# Wet Meat League — Fantasy League Site

A satirical ESPN-style site for the private Sleeper fantasy football league
**"Wet Meat League 2.0"**. The current priority is the site with real data
(standings, rosters, power rankings). AI journalism and AI video are later layers.

## Architecture

Two independent halves, joined only by committed JSON in `content/`:

1. **Static frontend** (repo root) — `index.html` (single page) + `app.js`
   (hash router) + `styles.css`. No build step, no framework. The browser
   `fetch()`es `content/*.json` at runtime. Everything works as plain static
   files on GitHub Pages.
2. **Python pipeline** (`pipeline/`) — fetches the Sleeper API, computes
   standings/rosters, calls Claude for power-rankings commentary, and writes
   `content/*.json`. Runs in a scheduled GitHub Action; it cannot run in the
   browser because the Anthropic key must stay server-side.

The frontend never calls Claude and never downloads large API payloads — the
pipeline pre-digests everything into small JSON files.

## Key facts

- **Sleeper API**: public, read-only, no auth, CORS-enabled, ~1000 req/min.
  Base: `https://api.sleeper.app/v1`. Avatars: `https://sleepercdn.com/avatars/<id>`
  (and `/thumbs/<id>`).
- **Current league (2026)**: `1357559174707281920` — `pre_draft`, draft
  `1357559174719885312`. Empty rosters / 0-0 until it drafts.
- **2025 league**: `1180170315099144192` — `complete`, full real data. This is
  the "current" dataset shown now. Leagues chain via `previous_league_id`.
- `resolve.py` owns season logic: 2025 is "current" until the 2026 season goes
  live (`/v1/state/nfl` `season_type` → `regular`), then it auto-switches. The
  2026 league/draft always powers a separate Draft section.

## Conventions

- **This is its own isolated git repo** (`wet-meat-league-website/.git`),
  independent of the parent `/home/ducky/clawd` workspace. The parent is an
  accidental no-commit repo containing identity/memory/client files — **never
  add this project to it or push the parent**. Run all git from this folder.
  This matches the per-project-repo convention used by the sibling projects.
- **Hosting**: GitHub repo **`professor-psyduck/wet-meat-league`** (personal
  account), **public** (GitHub Pages requires a public repo on the free plan;
  the league data is already public via Sleeper). Live site:
  `https://professor-psyduck.github.io/wet-meat-league/`. Note the local folder
  is `wet-meat-league-website` but the GitHub repo is `wet-meat-league`.
- **Default branch is `main`** (the ported GitHub Pages workflow deploys the
  whole repo on push to `main`).
- The pipeline is **idempotent**: a run with no new data makes no Claude call
  and leaves the git tree unchanged. The Action commits only if
  `git status --porcelain` is non-empty.
- AI spend is minimized: one batched, prompt-cached power-rankings call per
  week, deduped by key `power:<season>:w<week>` in `state/state.json`. With no
  key the pipeline uses a zero-cost deterministic stub; set the
  **`ANTHROPIC_API_KEY`** repo secret (Settings → Secrets → Actions) to switch
  power-rankings commentary to real Claude — no code change needed.
- The pipeline is idempotent via a content hash in `state.json`: unchanged
  Sleeper data ⇒ no file writes ⇒ the scheduled Action skips the commit ⇒ no
  redeploy. Never add a per-run timestamp outside the `generated_at` field.
- Generated content lives in `content/`. Pipeline state lives in
  `state/state.json`. Tunables live in `config/league.config.json`. None of
  these are hand-edited.
- When touching Anthropic SDK code, use the `claude-api` skill.

## Run locally

```bash
python -m http.server          # serve the site like Pages (repo root)
scripts/local_dryrun.sh        # run pipeline against real 2025 data, no API spend
```

`local_dryrun.sh` uses `--fake-claude` so the full site is buildable with zero
API spend. Set `ANTHROPIC_API_KEY` only to test real commentary.

## Build phases (see /home/ducky/.claude/plans/i-have-a-fantasty-unified-treasure.md)

- **A** static shell → **B** real standings/rosters → **C** 2026 draft section
  → **D** AI power-rankings + automation (**v1 complete**).
- **E** journalism layer (trade wire, recaps, previews) and **F** AI video
  (pluggable provider, default HeyGen) are deferred and must not block v1.
