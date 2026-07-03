"""Harvest good games by top coaches into bloodygit drills -- slowly, politely.

This is the batch orchestrator that ties the pieces together:

    top coaches (coaches.top_coaches)
        -> their recent matches, ranked (coaches.ranked_matches)
        -> a persistent, de-duplicated queue of match ids
        -> for each: match -> replay (fetch), replay -> drills (convert)
        -> drills written in the bloodygit layout (output.write_families)

It is built for **unattended, long-running** use: a single run processes only a
small batch, records everything it did in a crash-safe JSON *state file*, and
never re-downloads a replay it has already handled.  Run it once every so often
(see ``scripts/harvest_overnight.sh``) and it trickles games in over days rather
than hammering FUMBBL -- which is exactly what the site owner asked for.

The state file (default ``<out>/harvest_state.json``) records:

    queue      : ranked match ids still to process (rebuilt when drained)
    done       : {match_id: {replay_id, drills, coach_id, ...}} already handled
    failed     : {match_id: reason} that errored (retried on a later refresh)
    coaches    : the discovered coach roster (cached; refreshed on demand)
    totals     : cumulative drills written per family
    log        : recent run summaries
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .convert import build_drills
from .output import FAMILIES, write_families

# The state file is small JSON; we rewrite it atomically after every game so a
# power cut mid-batch loses at most the drills of the game in flight.
STATE_NAME = "harvest_state.json"


def _now() -> str:
    """ISO-ish timestamp for log lines (local time; best-effort)."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Harvester:
    """Owns the state file and runs batches against it."""
    root: Path
    state_path: Path
    turns_before: int = 2
    min_blocks: int = 3
    families: tuple[str, ...] = FAMILIES
    division: str | None = "2"          # Competitive
    min_score: float = 8.0              # skip low-action games entirely
    per_coach: int = 6                  # best-N recent games taken per coach
    top_n: int = 15                     # how many top coaches to mine
    state: dict = field(default_factory=dict)

    # -- state io ---------------------------------------------------------- #

    def load(self) -> "Harvester":
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        self.state.setdefault("queue", [])
        self.state.setdefault("done", {})
        self.state.setdefault("failed", {})
        self.state.setdefault("coaches", [])
        self.state.setdefault("totals", {})
        self.state.setdefault("log", [])
        return self

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.state_path)  # atomic on POSIX

    # -- queue building ---------------------------------------------------- #

    def refresh_coaches(self) -> list[dict]:
        """Discover (and cache) the top-coach roster from FUMBBL's toplist."""
        from . import coaches
        div_name = coaches.DIVISION_NAME.get(self.division or "2", "Competitive")
        roster = coaches.top_coaches(division=div_name, limit=self.top_n)
        self.state["coaches"] = roster
        return roster

    def rebuild_queue(self, *, refresh_coaches: bool = False) -> int:
        """(Re)build the ranked match queue from the coach roster.

        Skips matches already in ``done`` or ``failed``.  Returns the number of
        fresh match ids queued.  Discovery here is all light metadata -- no
        replays are downloaded."""
        from . import coaches
        roster = self.state.get("coaches") or []
        if refresh_coaches or not roster:
            roster = self.refresh_coaches()

        done, failed = self.state["done"], self.state["failed"]
        queued = {q["match_id"] for q in self.state["queue"]}
        candidates: list[dict] = []
        for c in roster:
            try:
                matches = coaches.ranked_matches(
                    c["coach_id"], division=self.division, min_score=self.min_score)
            except Exception as e:  # noqa: BLE001 - one bad coach shouldn't stop us
                self._log(f"! coach {c.get('name', c['coach_id'])}: {e}")
                continue
            for m in matches[:self.per_coach]:
                mid = m.match_id
                if mid in done or mid in failed or mid in queued:
                    continue
                queued.add(mid)
                candidates.append({
                    "match_id": mid, "score": round(m.score(), 1),
                    "coach_id": c["coach_id"], "coach": c.get("name", ""),
                    "date": m.date, "td": m.touchdowns, "cas": m.casualties,
                })
        # Best games first across the whole roster.
        candidates.sort(key=lambda q: q["score"], reverse=True)
        self.state["queue"].extend(candidates)
        self.state["queue"].sort(key=lambda q: q["score"], reverse=True)
        return len(candidates)

    # -- the batch --------------------------------------------------------- #

    def run_batch(self, n: int, *, dry_run: bool = False) -> dict:
        """Process up to ``n`` queued games: download replay, build & write
        drills, record the result.  Stops early (gracefully) if FUMBBL rate-limits
        us.  Returns a summary dict."""
        from . import fetch

        if not self.state["queue"]:
            self.rebuild_queue()
        if not self.state["queue"]:
            self._log("queue empty after rebuild -- nothing new to harvest")
            self.save()
            return {"processed": 0, "drills": 0, "rate_limited": False,
                    "note": "nothing new"}

        processed = drills_total = 0
        rate_limited = False
        for _ in range(n):
            if not self.state["queue"]:
                break
            item = self.state["queue"].pop(0)
            mid = item["match_id"]
            try:
                counts = self._harvest_one(item, dry_run=dry_run)
            except fetch.RateLimited as e:
                # Put it back; stop the batch so the next scheduled run retries.
                self.state["queue"].insert(0, item)
                self._log(f"rate limited on match {mid}: {e} -- stopping batch")
                rate_limited = True
                break
            except Exception as e:  # noqa: BLE001 - isolate per-game failures
                self.state["failed"][mid] = str(e)
                self._log(f"! match {mid}: {e}")
                self.save()
                continue

            n_drills = sum(counts.values())
            drills_total += n_drills
            processed += 1
            for fam, c in counts.items():
                self.state["totals"][fam] = self.state["totals"].get(fam, 0) + c
            self._log(f"match {mid} ({item.get('coach','?')}, score {item.get('score')}) "
                      f"-> {n_drills} drills " + (
                          " ".join(f"{k}:{v}" for k, v in counts.items()) or "(none)"))
            self.save()

        return {"processed": processed, "drills": drills_total,
                "rate_limited": rate_limited, "remaining": len(self.state["queue"])}

    def _harvest_one(self, item: dict, *, dry_run: bool) -> dict[str, int]:
        """One game: match id -> replay -> drills on disk.  Returns per-family
        written counts.  Records provenance in ``done``."""
        from . import fetch
        mid = item["match_id"]

        # We *know* mid is a match id, so resolve match -> replay directly
        # (2 requests) instead of fetch_source's probe-as-replay-first (3).
        rid = fetch.resolve_replay_id_from_match(mid)
        if not rid:
            raise ValueError(f"match {mid} has no replay id")
        replay = fetch.fetch_replay_by_id(rid)   # cached on disk by fetch
        if replay is None:
            raise ValueError(f"FUMBBL returned no replay for id {rid}")
        fams = build_drills(replay, replay_id=rid,
                            turns_before=self.turns_before, min_blocks=self.min_blocks)
        counts = write_families(fams, self.root, families=self.families,
                                dry_run=dry_run)
        self.state["done"][mid] = {
            "replay_id": rid, "coach_id": item.get("coach_id"),
            "coach": item.get("coach"), "score": item.get("score"),
            "drills": dict(counts), "at": _now(),
        }
        return counts

    # -- misc -------------------------------------------------------------- #

    def _log(self, msg: str) -> None:
        line = f"[{_now()}] {msg}"
        print(line)
        log = self.state.setdefault("log", [])
        log.append(line)
        del log[:-200]  # keep the tail bounded


def make_harvester(out_root, state_path=None, **kw) -> Harvester:
    """Build a :class:`Harvester` rooted at ``out_root`` and load its state."""
    root = Path(out_root)
    sp = Path(state_path) if state_path else root / STATE_NAME
    return Harvester(root=root, state_path=sp, **kw).load()
