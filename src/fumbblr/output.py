"""Where drills go on disk -- the bloodygit-mirroring layout, shared by the
one-shot CLI (:mod:`fumbblr.cli`) and the harvester (:mod:`fumbblr.harvest`).

Drills are written under a *data root* in a tree that mirrors the bloodygit dirs
its curriculum reads, so the tree can be copied straight in:

    <root>/drills_clock/fumbbl/        score (fclk1/2/3), pass, handoff
    <root>/drills_drop/fumbbl/         drop, sack (bloodygit ``balldown`` objective)
    <root>/scenarios_defense/fumbbl/   block, foul

Train/eval split: drills from replays whose numeric id satisfies
``replay_id % 5 == 0`` land under ``<root>/eval_holdout/<same tree>`` instead --
a deterministic ~20% holdout, split per *replay* so near-identical boards from
one game never straddle the split.  bloodygit copies the holdout into its
frozen never-train eval suites.
"""
from __future__ import annotations

import json
from pathlib import Path

# The families mined from one replay, and the bloodygit subdir each lands in.
FAMILIES = ("score", "drop", "sack", "block", "pass", "handoff", "foul")

# Numeric replay ids with this residue mod HOLDOUT_MOD go to the eval holdout.
HOLDOUT_MOD = 5
HOLDOUT_DIR = "eval_holdout"


def family_dirs(root: Path) -> dict[str, Path]:
    """Map each family to its output dir under ``root`` (bloodygit layout)."""
    clock = root / "drills_clock" / "fumbbl"
    drop = root / "drills_drop" / "fumbbl"
    defense = root / "scenarios_defense" / "fumbbl"
    return {
        "score": clock,
        "pass": clock,       # ball-delivery offence -> alongside the fclk ladder
        "handoff": clock,
        "drop": drop,        # force-the-drop defense (balldown objective)
        "sack": drop,        # carrier-blitz defense -> same ladder, rung 2
        "block": defense,    # bash/aggression -> alongside foul
        "foul": defense,
    }


def _is_holdout(drill: dict) -> bool:
    """Deterministic per-replay eval split (all of one game on one side)."""
    rid = str(drill.get("source", {}).get("replay_id", ""))
    digits = "".join(c for c in rid if c.isdigit())
    return bool(digits) and int(digits) % HOLDOUT_MOD == 0


def write_families(fams: dict[str, list[dict]], root: Path, *,
                   families=FAMILIES, dry_run: bool = False,
                   holdout: bool = True) -> dict[str, int]:
    """Write the selected ``families`` of ``fams`` under ``root``.

    Returns a per-family count of drills written (or that *would* be written,
    under ``dry_run``).  With ``holdout`` (the default), drills from every
    fifth replay are routed to ``<root>/eval_holdout/...`` for the frozen eval
    suites; pass ``holdout=False`` to keep the old single-tree behaviour."""
    root = Path(root)
    dirs = family_dirs(root)
    written: dict[str, int] = {}
    for fam in families:
        drills = fams.get(fam, [])
        if not drills:
            continue
        for d in drills:
            out_dir = dirs[fam]
            if holdout and _is_holdout(d):
                out_dir = root / HOLDOUT_DIR / out_dir.relative_to(root)
            written[fam] = written.get(fam, 0) + 1
            if not dry_run:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{d['id']}.json").write_text(json.dumps(d, indent=2))
    return written
