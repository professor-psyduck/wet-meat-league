#!/usr/bin/env bash
# Zero-spend local end-to-end: run the real pipeline against live (public)
# Sleeper data with the stub commentary (no API key, no cost), then serve
# the site exactly like GitHub Pages.
#
#   scripts/local_dryrun.sh          # build + serve on :8000
#   scripts/local_dryrun.sh --build  # build only
#
# Set ANTHROPIC_API_KEY and drop --fake-claude to test real AI commentary.
set -euo pipefail
cd "$(dirname "$0")/.."

echo ">> running pipeline (stub commentary, no API spend)"
python3 pipeline/run.py --fake-claude

if [ "${1:-}" = "--build" ]; then
  echo ">> build only; done."
  exit 0
fi

echo ">> serving at http://localhost:8000  (Ctrl-C to stop)"
exec python3 -m http.server 8000
