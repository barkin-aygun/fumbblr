#!/usr/bin/env bash
# One-shot: retry the widened coach-roster refresh until it succeeds (FUMBBL
# outages / rate limits happen), then hand off to the normal overnight loop with
# the same widened args. Start detached:
#   nohup scripts/widen_and_loop.sh >> harvest.out 2>&1 &
# Tunables: MAX_TRIES (default 192 = ~48h at 15 min), RETRY_SLEEP (default 900).
cd "$(dirname "$0")/.."
export PYTHONPATH=src
export FUMBBLR_DELAY="${FUMBBLR_DELAY:-5}"
MAX_TRIES="${MAX_TRIES:-192}"
RETRY_SLEEP="${RETRY_SLEEP:-900}"
tries=0
until python3 -m fumbblr.harvest_cli --refresh --top-n 50 --per-coach 12 -n 0; do
  tries=$((tries+1))
  echo "widen: refresh attempt $tries failed; retrying in $((RETRY_SLEEP/60)) min"
  [ "$tries" -ge "$MAX_TRIES" ] && { echo "widen: giving up after $MAX_TRIES attempts"; exit 1; }
  sleep "$RETRY_SLEEP"
done
echo "widen: refresh OK -- starting overnight loop"
exec bash scripts/harvest_overnight.sh --top-n 50 --per-coach 12
