"""``fumbblr`` CLI: replay source(s) -> bloodygit scenario/v1 drill JSON files.

    fumbblr 4701297                         # match id -> all drill families
    fumbblr ~/Downloads/ffblive.jnlp
    fumbblr replay_1901960.gz --stats       # just report what the game is good for

Each drill family is written to the bloodygit dir its curriculum reads:
    score (clk1/2/3) -> data/drills_clock/fumbbl/
    sack, block      -> data/scenarios_defense/fumbbl/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .convert import build_drills, inventory
from .fetch import load_source

_BG = Path(__file__).resolve().parents[2].parent / "bloodygit"
_CLOCK = _BG / "data" / "drills_clock" / "fumbbl"
_DEF = _BG / "data" / "scenarios_defense" / "fumbbl"
_DIRS = {
    "score": _CLOCK,
    "sack": _DEF,
    "block": _DEF,
    "pass": _CLOCK,      # ball-delivery offence -> alongside the clk score ladder
    "handoff": _CLOCK,
    "foul": _DEF,        # bash/aggression -> alongside sack/block
}
_FAMILIES = ("score", "sack", "block", "pass", "handoff", "foul")


def _print_inventory(inv: dict) -> None:
    m = inv["matchup"]
    print(f"{inv['replay_id']}: {m[0]} vs {m[1]}")
    d, e = inv["drills"], inv["events"]
    print(f"   drills available  -> score(clk):{d['score']:2d}  sack:{d['sack']:2d}  "
          f"block:{d['block']:2d}  pass:{d['pass']:2d}  handoff:{d['handoff']:2d}  "
          f"foul:{d['foul']:2d}")
    print(f"   events            -> TDs:{e['touchdowns']} passes:{e['passes']} "
          f"blocks:{e['blocks']} blitzes:{e['blitzes']} injuries:{e['injuries']} "
          f"pickups:{e['pickups']} scatters:{e['ball_scatters']} "
          f"kickoffs:{e['kickoffs']} stallers:{e['stallers']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fumbblr", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+",
                    help="replay/match id, .jnlp, or local .gz/.json replay file")
    ap.add_argument("--out", type=Path, default=None,
                    help="override output root (default: the bloodygit data dirs)")
    ap.add_argument("--turns-before", type=int, default=2,
                    help="turns before each TD to snapshot (default: 2 -> clk1..3)")
    ap.add_argument("--min-blocks", type=int, default=3,
                    help="blocks/turn to qualify as a bash-pressure drill (default 3)")
    ap.add_argument("--families", default=",".join(_FAMILIES),
                    help="comma-separated families to emit (default: all)")
    ap.add_argument("--stats", action="store_true",
                    help="report each replay's drill yield + event counts, write nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="print a summary, write nothing")
    args = ap.parse_args(argv)
    families = [f.strip() for f in args.families.split(",") if f.strip()]

    totals: dict = {}
    for src in args.sources:
        try:
            replay, rid = load_source(src)
        except Exception as e:  # noqa: BLE001 - clean per-source message
            print(f"!! {src}: {e}", file=sys.stderr)
            continue

        if args.stats:
            _print_inventory(inventory(replay, replay_id=rid,
                                       turns_before=args.turns_before,
                                       min_blocks=args.min_blocks))
            continue

        fams = build_drills(replay, replay_id=rid, turns_before=args.turns_before,
                            min_blocks=args.min_blocks)
        n = {k: len(v) for k, v in fams.items()}
        print(f"{src}: replay {rid} -> "
              + "  ".join(f"{k}:{n.get(k, 0)}" for k in _FAMILIES))
        for fam in families:
            drills = fams.get(fam, [])
            out_dir = (args.out / fam) if args.out else _DIRS[fam]
            if drills and not (args.dry_run):
                out_dir.mkdir(parents=True, exist_ok=True)
            for d in drills:
                totals[fam] = totals.get(fam, 0) + 1
                if not args.dry_run:
                    (out_dir / f"{d['id']}.json").write_text(json.dumps(d, indent=2))

    if not args.stats:
        where = "(dry run)" if args.dry_run else "written to bloodygit data dirs"
        summary = "  ".join(f"{k}:{v}" for k, v in sorted(totals.items())) or "none"
        print(f"\ntotal drills: {summary}  {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
