#!/usr/bin/env bash
# Trickle good games by top coaches into drills, slowly and politely.
#
# Runs `fumbblr-harvest` in a loop: a small batch, then a long sleep, over and
# over.  Safe to start and leave running for days -- the harvester's state file
# means every wake-up picks up where the last left off and never re-downloads a
# replay.  It stops itself for a while if FUMBBL ever rate-limits it.
#
#   scripts/harvest_overnight.sh                  # defaults: 3 games / 20 min
#   BATCH=2 INTERVAL=1800 scripts/harvest_overnight.sh   # 2 games / 30 min
#   MAX_BATCHES=40 scripts/harvest_overnight.sh          # stop after 40 batches
#
# Tunables (env vars):
#   BATCH        games per wake-up            (default 3)
#   INTERVAL     seconds to sleep between     (default 1200 = 20 min)
#   MAX_BATCHES  stop after this many (0=inf) (default 0)
#   FUMBBLR_DELAY  per-request politeness s   (default 5, honoured by fumbblr)
#
# Run detached so it survives your shell closing:
#   nohup scripts/harvest_overnight.sh > harvest.out 2>&1 &
set -euo pipefail

BATCH="${BATCH:-3}"
INTERVAL="${INTERVAL:-1200}"
MAX_BATCHES="${MAX_BATCHES:-0}"
export FUMBBLR_DELAY="${FUMBBLR_DELAY:-5}"

# Use the installed console script if present; otherwise run from source
# (no install / no pip needed) via the repo's src/ on PYTHONPATH.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
if command -v fumbblr-harvest >/dev/null 2>&1; then
  HARVEST() { fumbblr-harvest "$@"; }
else
  export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
  PY="$(command -v python3 || command -v python)"
  HARVEST() { "$PY" -m fumbblr.harvest_cli "$@"; }
fi

echo "harvest_overnight: BATCH=$BATCH INTERVAL=${INTERVAL}s MAX_BATCHES=$MAX_BATCHES FUMBBLR_DELAY=$FUMBBLR_DELAY"

i=0
while :; do
  i=$((i + 1))
  echo "=== batch $i @ $(date '+%Y-%m-%d %H:%M:%S') ==="
  # Never let one failing batch kill the loop.
  HARVEST -n "$BATCH" "$@" || echo "!! batch $i failed (continuing)"

  if [ "$MAX_BATCHES" -gt 0 ] && [ "$i" -ge "$MAX_BATCHES" ]; then
    echo "reached MAX_BATCHES=$MAX_BATCHES; stopping."
    break
  fi
  echo "sleeping ${INTERVAL}s..."
  sleep "$INTERVAL"
done
