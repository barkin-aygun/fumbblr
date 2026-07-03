"""Replay snapshots -> bloodygit ``scenario/v1`` pinned drills.

The drill families mined from one replay, each landing in the bloodygit
dir its curriculum already reads (family = ``id.rsplit("_", 1)[0]``):

* **scoring** (``fclk1`` / ``fclk2`` / ``fclk3``): the start of the scoring
  team's turn 0 / 1 / 2 turns before a touchdown -> the score-in-N-turns depth
  ladder from real human TDs (``--curriculum-rungs fclk1,fclk2,...``).
  Dir: ``data/drills_clock/fumbbl/``.
* **drop** (``drop1``): the start of the turn in which a team knocked the ball
  out of the enemy carrier's hands (defense-forced turnover) -> force-the-drop
  drills (bloodygit ``balldown`` objective, ``active_team`` = the forcing side).
  Dir: ``data/drills_drop/fumbbl/``.
* **sack** (``sack``): the start of the defending team's turn in which they
  blitzed the enemy ball-carrier (carrier attacked, ball not necessarily lost)
  -> win-the-ball drills, second rung of the defense ladder.
  Dir: ``data/drills_drop/fumbbl/``.  A turn that actually dislodged the ball
  emits only ``drop1`` (drop takes precedence).
* **block** (``block``): the start of a heavy-block turn (bash pressure).
  Dir: ``data/scenarios_defense/fumbbl/``.

Every drill carries the full real-player identity (stats + skills + params +
keywords) and pins the real matchup via top-level ``races`` (see ``_build_board``
/ scenario.py).  Orientation: bloodygit's end zones are fixed (seat 0 attacks
x=25, seat 1 attacks x=0); the drill's "self" team maps to whichever seat
attacks the end zone it attacks, so coordinates copy across verbatim.
"""
from __future__ import annotations

from .mapping import (N_TEAM_SLOTS, SCHEMA, map_race, normalize_skill,
                      on_pitch, status_name)
from .replay import ParsedReplay, TurnSnapshot

_DEFAULT_PARAMS = {"Mighty Blow": 1, "Dirty Player": 1, "Loner": 4}


def _i(v) -> int:
    """Coerce an FFB stat to int (passing may be None -> 0 = cannot pass)."""
    return int(v) if v is not None else 0


def _other(team: str) -> str:
    return "away" if team == "home" else "home"


def _seat_for(attacks_x: int) -> int:
    """bloodygit seat that attacks end zone ``attacks_x`` (0 -> seat 1, 25 -> 0)."""
    return 0 if attacks_x >= 13 else 1


def _clean_skills(pi) -> tuple[list, dict]:
    """Real player's skills + params, with FFB characteristic-advance markers
    ('+MA', '+AG', ...) dropped (already folded into the current stats) and
    everything spelled the bloodygit way."""
    skills = [normalize_skill(s) for s in pi.skills if not s.startswith("+")]
    params = {normalize_skill(k): v for k, v in pi.skill_params.items()
              if not k.startswith("+")}
    for s in skills:
        if s in _DEFAULT_PARAMS and s not in params:
            params[s] = _DEFAULT_PARAMS[s]
    return skills, params


def _assign_slots(snap: TurnSnapshot, players) -> dict:
    """player_id -> slot (0..15), per FFB team in stable nr order over the
    players currently on the pitch."""
    by_team: dict = {"home": [], "away": []}
    for pid, xy in snap.field.coord.items():
        if not xy or not on_pitch(xy[0], xy[1]):
            continue
        if players.get(pid) is not None:
            by_team[players[pid].team].append(pid)
    slot_of: dict = {}
    for pids in by_team.values():
        pids.sort(key=lambda p: (players[p].nr, p))
        for slot, pid in enumerate(pids[:N_TEAM_SLOTS]):
            slot_of[pid] = slot
    return slot_of


def _build_board(snap: TurnSnapshot, self_team: str, self_seat: int, players) -> dict:
    """The ``board`` block (mode: pinned), with ``self_team`` placed on seat
    ``self_seat`` and the opponent on the other seat. Full player fidelity."""
    opp_seat = 1 - self_seat
    slot_of = _assign_slots(snap, players)

    entries, pid_to_global = [], {}
    for pid, slot in slot_of.items():
        xy = snap.field.coord[pid]
        pi = players[pid]
        seat = self_seat if pi.team == self_team else opp_seat
        skills, params = _clean_skills(pi)
        entries.append({
            "slot": slot, "team": seat,
            "x": int(xy[0]), "y": int(xy[1]),
            "status": status_name(snap.field.state.get(pid)),
            "position": pi.position_name,
            "ma": _i(pi.ma), "st": _i(pi.st), "ag": _i(pi.ag),
            "pa": _i(pi.pa), "av": _i(pi.av),
            "skills": skills, "skill_params": params,
            "keywords": list(pi.keywords),
        })
        pid_to_global[pid] = seat * N_TEAM_SLOTS + slot

    bx, by = snap.field.ball
    ball = {"x": int(bx), "y": int(by), "held_by": -1}
    if on_pitch(bx, by):
        for pid, slot in slot_of.items():
            if snap.field.coord[pid] == [bx, by]:
                gid = pid_to_global[pid]
                ball["held_by"] = gid
                for e in entries:
                    if e["team"] * N_TEAM_SLOTS + e["slot"] == gid:
                        e["has_ball"] = True
                break

    entries.sort(key=lambda e: (e["team"], e["slot"]))
    return {"mode": "pinned", "active_team": self_seat,
            "players": entries, "ball": ball}


def _drill(snap, self_team, self_seat, parsed, *, drill_id, themes, desc, source):
    """Assemble a full scenario/v1 drill around ``self_team`` (the active side)."""
    opp_team = _other(self_team)
    seat_races = [None, None]
    seat_races[self_seat] = map_race(parsed.races.get(self_team))
    seat_races[1 - self_seat] = map_race(parsed.races.get(opp_team))
    return {
        "schema": SCHEMA,
        "id": drill_id,
        "desc": desc,
        "weight": 1.0,
        "themes": themes,
        "source": source,
        "races": seat_races,
        "clock": {"half": snap.half, "turn": snap.turn},
        "score": {"self_lead": snap.field.score[self_team] - snap.field.score[opp_team]},
        "resources": {"self_rerolls": snap.field.rerolls.get(self_team, 0),
                      "opp_rerolls": snap.field.rerolls.get(opp_team, 0)},
        "board": _build_board(snap, self_team, self_seat, players=parsed.players),
    }


def _idx(replay_id, seq: int) -> str:
    """Underscore-free unique suffix so ``id.rsplit('_',1)[0]`` is the family."""
    digits = "".join(c for c in str(replay_id) if c.isdigit()) or "0"
    return f"{digits}{seq:03d}"


# --------------------------------------------------------------------------- #
# the three drill families
# --------------------------------------------------------------------------- #
def _scoring_drills(parsed, replay_id, turns_before) -> list[dict]:
    out, seq = [], 0
    for td in parsed.touchdowns:
        self_seat = _seat_for(td.end_zone_x)
        for offset in range(0, turns_before + 1):
            turn = td.turn - offset
            if turn < 1:
                continue
            snap = parsed.snapshot_for(td.half, td.team, turn)
            if snap is None:
                continue
            clk = offset + 1
            desc = (f"FUMBBL replay {replay_id}: {clk}-turn score (fclk{clk}) — start "
                    f"of the attacking team's turn {offset} before a TD "
                    f"(half {snap.half}, turn {snap.turn}). Real teams reproduced.")
            out.append(_drill(
                snap, td.team, self_seat, parsed,
                drill_id=f"fclk{clk}_{_idx(replay_id, seq)}",
                themes=["score_run"], desc=desc,
                source={"kind": "fumbbl_replay", "replay_id": str(replay_id),
                        "drill": "score", "scorer_id": td.scorer_id,
                        "turns_before_td": offset, "clk": clk}))
            seq += 1
    return out


def _drop_drills(parsed, replay_id) -> tuple[list[dict], set]:
    """Turn-start before a team knocked the ball out of the enemy carrier's
    hands. Returns the drills and the set of (half, team, turn) keys used (so
    sack/block drills skip them -- drop takes precedence)."""
    out, seq, keys = [], 0, set()
    for d in parsed.drops:
        if d.def_team not in parsed.team_attacks:
            continue
        key = (d.half, d.def_team, d.turn)
        if key in keys:
            continue
        snap = parsed.snapshot_for(d.half, d.def_team, d.turn)
        if snap is None:
            continue
        keys.add(key)
        self_seat = _seat_for(parsed.team_attacks[d.def_team])
        desc = (f"FUMBBL replay {replay_id}: force the drop — start of the "
                f"defending team's turn (half {snap.half}, turn {snap.turn}); "
                f"they knocked the ball loose. Real teams reproduced.")
        drill = _drill(
            snap, d.def_team, self_seat, parsed,
            drill_id=f"drop1_{_idx(replay_id, seq)}",
            themes=["force_drop"], desc=desc,
            source={"kind": "fumbbl_replay", "replay_id": str(replay_id),
                    "drill": "drop", "carrier_id": d.carrier_id,
                    "cause": d.cause})
        # The drill's premise is "the ENEMY carries — knock it loose": a
        # snapshot where the ball is already loose (or somehow on the forcing
        # side) would be an instant win under bloodygit's balldown objective.
        held = drill["board"]["ball"]["held_by"]
        if held < 0 or held // N_TEAM_SLOTS == self_seat:
            keys.discard(key)          # let the turn fall back to sack/block
            continue
        out.append(drill)
        seq += 1
    return out, keys


def _sack_drills(parsed, replay_id, skip_keys=frozenset()) -> tuple[list[dict], set]:
    """Defender's turn-start before they blitz the enemy carrier. Returns the
    drills and the set of (half, team, turn) keys used (so block drills skip
    them). Turns in ``skip_keys`` (already emitted as drop drills) are skipped."""
    out, seq, keys, seen = [], 0, set(), set()
    for s in parsed.sacks:
        if s.def_team not in parsed.team_attacks:
            continue
        key = (s.half, s.def_team, s.turn)
        if key in seen or key in skip_keys:
            continue
        seen.add(key)
        snap = parsed.snapshot_for(s.half, s.def_team, s.turn)
        if snap is None:
            continue
        keys.add(key)
        self_seat = _seat_for(parsed.team_attacks[s.def_team])
        desc = (f"FUMBBL replay {replay_id}: enemy carrier exposed — start of the "
                f"defending team's turn (half {snap.half}, turn {snap.turn}); they "
                f"blitzed the carrier to win the ball. Real teams reproduced.")
        out.append(_drill(
            snap, s.def_team, self_seat, parsed,
            drill_id=f"sack_{_idx(replay_id, seq)}",
            themes=["sack_carrier"], desc=desc,
            source={"kind": "fumbbl_replay", "replay_id": str(replay_id),
                    "drill": "sack", "carrier_id": s.carrier_id}))
        seq += 1
    return out, keys


def _block_drills(parsed, replay_id, min_blocks, skip_keys) -> list[dict]:
    """Start of a heavy-block (bash-pressure) turn, minus turns already emitted
    as sack drills."""
    out, seq = [], 0
    for bt in sorted(parsed.block_turns(min_blocks),
                     key=lambda b: (-b.n_blocks, b.half, b.turn)):
        if bt.team not in parsed.team_attacks:
            continue
        if (bt.half, bt.team, bt.turn) in skip_keys:
            continue
        snap = parsed.snapshot_for(bt.half, bt.team, bt.turn)
        if snap is None:
            continue
        self_seat = _seat_for(parsed.team_attacks[bt.team])
        desc = (f"FUMBBL replay {replay_id}: bash pressure — start of a turn the "
                f"team threw {bt.n_blocks} blocks (half {snap.half}, "
                f"turn {snap.turn}). Real teams reproduced.")
        out.append(_drill(
            snap, bt.team, self_seat, parsed,
            drill_id=f"block_{_idx(replay_id, seq)}",
            themes=["open_field_sack"], desc=desc,
            source={"kind": "fumbbl_replay", "replay_id": str(replay_id),
                    "drill": "block", "n_blocks": bt.n_blocks}))
        seq += 1
    return out


# --------------------------------------------------------------------------- #
# skill-action families (pass / hand-off / foul)
# --------------------------------------------------------------------------- #
# family -> (drill tag, theme, human label). One single-turn drill per
# (half, team, turn) in which the acting team performed the action.
_ACTION_FAMILIES = {
    "pass": ("pass", "passing_play", "completed a pass"),
    "handoff": ("handoff", "passing_play", "made a hand-off"),
    "foul": ("foul", None, "committed a foul"),
}


def _action_drills(parsed, replay_id, events, family) -> list[dict]:
    """Turn-start boards where ``self`` (the acting team) went on to pass / hand
    off / foul -- one drill per acting team-turn, with that team active."""
    drill, theme, label = _ACTION_FAMILIES[family]
    out, seq, seen = [], 0, set()
    for ev in events:
        if ev.team not in parsed.team_attacks:
            continue
        key = (ev.half, ev.team, ev.turn)
        if key in seen:
            continue
        snap = parsed.snapshot_for(ev.half, ev.team, ev.turn)
        if snap is None:
            continue
        seen.add(key)
        self_seat = _seat_for(parsed.team_attacks[ev.team])
        desc = (f"FUMBBL replay {replay_id}: start of the acting team's turn "
                f"(half {snap.half}, turn {snap.turn}); they {label}. "
                f"Real teams reproduced.")
        out.append(_drill(
            snap, ev.team, self_seat, parsed,
            drill_id=f"{family}_{_idx(replay_id, seq)}",
            themes=[theme] if theme else [], desc=desc,
            source={"kind": "fumbbl_replay", "replay_id": str(replay_id),
                    "drill": drill, "actor_id": ev.actor_id}))
        seq += 1
    return out


def _action_count(parsed, events) -> int:
    """Deduped count of emittable action-turn drills (for triage/inventory)."""
    return len({(e.half, e.team, e.turn) for e in events
                if e.team in parsed.team_attacks
                and parsed.snapshot_for(e.half, e.team, e.turn) is not None})


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def build_drills(replay: dict, *, replay_id, turns_before=2, min_blocks=3) -> dict:
    """All drill families mined from a replay: ``{"score", "drop", "sack",
    "block", "pass", "handoff", "foul"}`` (each a list of scenario/v1 dicts)."""
    parsed = ParsedReplay(replay)
    drop, drop_keys = _drop_drills(parsed, replay_id)
    sack, sack_keys = _sack_drills(parsed, replay_id, skip_keys=drop_keys)
    return {
        "score": _scoring_drills(parsed, replay_id, turns_before),
        "drop": drop,
        "sack": sack,
        "block": _block_drills(parsed, replay_id, min_blocks,
                               sack_keys | drop_keys),
        "pass": _action_drills(parsed, replay_id, parsed.pass_events, "pass"),
        "handoff": _action_drills(parsed, replay_id, parsed.handoff_events,
                                  "handoff"),
        "foul": _action_drills(parsed, replay_id, parsed.foul_events, "foul"),
    }


def drills_from_replay(replay: dict, *, replay_id, turns_before=2) -> list[dict]:
    """Back-compat: just the scoring (clk) drills."""
    return build_drills(replay, replay_id=replay_id,
                        turns_before=turns_before)["score"]


def inventory(replay: dict, *, replay_id, turns_before=2, min_blocks=3) -> dict:
    """What a replay is good for — drill yields + raw event counts, for triage."""
    parsed = ParsedReplay(replay)
    inv = parsed.inventory
    drop_keys = {(d.half, d.def_team, d.turn) for d in parsed.drops
                 if d.def_team in parsed.team_attacks}
    sack_keys = {(s.half, s.def_team, s.turn) for s in parsed.sacks
                 if s.def_team in parsed.team_attacks} - drop_keys
    return {
        "replay_id": str(replay_id),
        "matchup": [parsed.races.get("home"), parsed.races.get("away")],
        "drills": {
            "score": sum(1 for td in parsed.touchdowns
                         for o in range(turns_before + 1) if td.turn - o >= 1),
            "drop": len(drop_keys),
            "sack": len(sack_keys),
            "block": sum(1 for b in parsed.block_turns(min_blocks)
                         if b.team in parsed.team_attacks
                         and (b.half, b.team, b.turn) not in sack_keys
                         and (b.half, b.team, b.turn) not in drop_keys),
            "pass": _action_count(parsed, parsed.pass_events),
            "handoff": _action_count(parsed, parsed.handoff_events),
            "foul": _action_count(parsed, parsed.foul_events),
        },
        "events": {
            "touchdowns": len(parsed.touchdowns),
            "drops_forced": len(parsed.drops),
            "offense_turn_drops": inv.get("offense_turn_drop", 0),
            "passes": len(parsed.passes),
            "blocks": inv.get("block", 0),
            "blitzes": inv.get("selectBlitzTarget", 0),
            "injuries": inv.get("injury", 0),
            "pickups": inv.get("pickUpRoll", 0),
            "ball_scatters": inv.get("scatterBall", 0),
            "kickoffs": inv.get("kickoffResult", 0),
            "stallers": inv.get("stallerDetected", 0),
        },
    }
