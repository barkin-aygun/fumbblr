"""``fumbblr-harvest`` -- trickle good games by top FUMBBL coaches into drills.

Discovers strong coaches from FUMBBL's toplist, ranks their recent competitive
games by how much tactically interesting action they contain, and converts a
small batch per run into bloodygit ``scenario/v1`` drills.  State is persisted,
so it is safe (and intended) to run repeatedly on a timer -- it never
re-downloads a replay it has already handled, and it slows down / stops when
FUMBBL rate-limits it.

    fumbblr-harvest                       # one polite batch (default 3 games)
    fumbblr-harvest -n 5                   # up to 5 games this run
    fumbblr-harvest --coaches 3893,52015  # mine specific coach ids, skip toplist
    fumbblr-harvest --refresh             # re-pull the toplist / coach roster
    fumbblr-harvest --status              # print state summary, do nothing
    fumbblr-harvest --dry-run -n 3        # resolve + convert but write nothing

Politeness: every HTTP request waits FUMBBLR_DELAY seconds (default 5) and backs
off on errors.  Combined with a small batch and a long timer, downloads stay
low -- as the site owner asked.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .harvest import make_harvester
from .output import FAMILIES

_DATA = Path(__file__).resolve().parents[2] / "data"


def _print_status(h) -> None:
    s = h.state
    done, queue, failed = s["done"], s["queue"], s["failed"]
    totals = "  ".join(f"{k}:{v}" for k, v in sorted(s["totals"].items())) or "none"
    print(f"state: {h.state_path}")
    print(f"  coaches known : {len(s['coaches'])}")
    print(f"  games done    : {len(done)}")
    print(f"  games queued  : {len(queue)}")
    print(f"  games failed  : {len(failed)}")
    print(f"  drills total  : {totals}")
    if queue:
        print("  next up:")
        for q in queue[:5]:
            print(f"    match {q['match_id']}  score {q.get('score')}  "
                  f"{q.get('coach','?')}  {q.get('date','')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="fumbblr-harvest", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--num", type=int, default=3,
                    help="max games to process this run (default 3 -- keep it gentle)")
    ap.add_argument("--out", type=Path, default=_DATA,
                    help="output root for the drill tree (default: <repo>/data)")
    ap.add_argument("--state", type=Path, default=None,
                    help="state file path (default: <out>/harvest_state.json)")
    ap.add_argument("--coaches", default=None,
                    help="comma-separated coach ids to mine instead of the toplist")
    ap.add_argument("--top-n", type=int, default=15,
                    help="how many top coaches to pull from the toplist (default 15)")
    ap.add_argument("--per-coach", type=int, default=6,
                    help="best-N recent games taken per coach (default 6)")
    ap.add_argument("--min-score", type=float, default=8.0,
                    help="skip games below this action-density score (default 8)")
    ap.add_argument("--division", default="2",
                    help="FUMBBL division id to mine, or 'all' (default 2=Competitive)")
    ap.add_argument("--families", default=",".join(FAMILIES),
                    help="comma-separated drill families to emit (default: all)")
    ap.add_argument("--turns-before", type=int, default=2)
    ap.add_argument("--min-blocks", type=int, default=3)
    ap.add_argument("--refresh", action="store_true",
                    help="re-pull the toplist and rebuild the queue before running")
    ap.add_argument("--status", action="store_true",
                    help="print the state summary and exit (no downloads)")
    ap.add_argument("--dry-run", action="store_true",
                    help="download + convert but write no drill files")
    args = ap.parse_args(argv)

    families = tuple(f.strip() for f in args.families.split(",") if f.strip())
    division = None if args.division.lower() == "all" else args.division

    h = make_harvester(
        args.out, state_path=args.state,
        turns_before=args.turns_before, min_blocks=args.min_blocks,
        families=families, division=division, min_score=args.min_score,
        per_coach=args.per_coach, top_n=args.top_n)

    # An explicit coach list overrides toplist discovery (and is cached in state).
    if args.coaches:
        h.state["coaches"] = [{"coach_id": c.strip(), "name": c.strip()}
                              for c in args.coaches.split(",") if c.strip()]

    if args.status:
        _print_status(h)
        return 0

    if args.refresh or not h.state["queue"]:
        n_new = h.rebuild_queue(refresh_coaches=args.refresh and not args.coaches)
        h._log(f"queue rebuilt: +{n_new} games ({len(h.state['queue'])} total)")
        h.save()

    summary = h.run_batch(args.num, dry_run=args.dry_run)
    h.save()
    print(f"\nbatch: processed {summary['processed']}  "
          f"drills {summary.get('drills', 0)}  "
          f"queued {summary.get('remaining', len(h.state['queue']))}"
          + ("  [RATE LIMITED -- backing off]" if summary.get("rate_limited") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
