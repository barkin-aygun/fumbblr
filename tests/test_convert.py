"""Offline tests against the checked-in 2026 replay fixture (no network)."""
from pathlib import Path

from fumbblr.convert import build_drills, drills_from_replay, inventory
from fumbblr.mapping import BOARD_HEIGHT, BOARD_WIDTH, N_TEAM_SLOTS
from fumbblr.replay import ParsedReplay, load_replay

FIXTURE = Path(__file__).parent / "fixtures" / "replay_1901960.gz"
_VALID_STATUS = {"ACTIVE", "USED", "PRONE", "STUNNED", "KO",
                 "CASUALTY", "RESERVE", "SENT_OFF"}


def _replay():
    return load_replay(FIXTURE)


def _all(fams):
    return [d for v in fams.values() for d in v]


def test_touchdowns_and_events_detected():
    p = ParsedReplay(_replay())
    assert len(p.touchdowns) == 3                  # High Elf 2 - 1 Tomb Kings
    assert p.touchdowns[-1].score == {"home": 2, "away": 1}
    assert len(p.sacks) >= 1 and p.inventory["block"] == 57
    assert p.team_attacks and set(p.team_attacks) == {"home", "away"}


def test_drops_detected():
    """Defense-forced ball losses are found, deduped per defending turn, and
    never credited to the carrier's own team."""
    p = ParsedReplay(_replay())
    assert len(p.drops) == 2
    keys = {(d.half, d.def_team, d.turn) for d in p.drops}
    assert len(keys) == len(p.drops)               # deduped
    for d in p.drops:
        assert p.team_of(d.carrier_id) != d.def_team
        assert d.cause in ("block", "other")


def test_families_and_fclk_ladder():
    fams = build_drills(_replay(), replay_id="1901960")
    assert len(fams["score"]) == 9 and len(fams["sack"]) >= 1 and len(fams["block"]) >= 1
    assert len(fams["drop"]) == 2
    # scoring drills form an fclk1/fclk2/fclk3 ladder (family = id.rsplit("_",1)[0])
    clk = {d["id"].rsplit("_", 1)[0] for d in fams["score"]}
    assert clk == {"fclk1", "fclk2", "fclk3"}
    for d in fams["score"]:
        assert d["source"]["clk"] == int(d["id"].rsplit("_", 1)[0][4:])
    # drop/sack/block families resolve correctly too
    assert all(d["id"].rsplit("_", 1)[0] == "drop1" for d in fams["drop"])
    assert all(d["id"].rsplit("_", 1)[0] == "sack" for d in fams["sack"])
    assert all(d["id"].rsplit("_", 1)[0] == "block" for d in fams["block"])
    assert drills_from_replay(_replay(), replay_id="1901960") == fams["score"]


def test_drop_drills_are_forcer_active_and_skip_sacks():
    """Drop drills put the forcing side on active_team with the ENEMY holding
    the ball; a turn emitted as a drop never re-emits as a sack."""
    p = ParsedReplay(_replay())
    fams = build_drills(_replay(), replay_id="1901960")
    for d in fams["drop"]:
        assert d["themes"] == ["force_drop"]
        assert d["source"]["drill"] == "drop"
        hb = d["board"]["ball"]["held_by"]
        if hb >= 0:                                # the ENEMY carrier holds the ball
            assert hb // N_TEAM_SLOTS != d["board"]["active_team"]
    # drop takes precedence: a turn emitted as drop1 never re-emits as sack
    drop_keys = {(x.half, x.def_team, x.turn) for x in p.drops
                 if x.def_team in p.team_attacks}
    sack_keys = {(s.half, s.def_team, s.turn) for s in p.sacks
                 if s.def_team in p.team_attacks}
    assert len(fams["sack"]) == len(sack_keys - drop_keys)


def test_sack_drills_are_defender_active():
    fams = build_drills(_replay(), replay_id="1901960")
    for d in fams["sack"]:
        assert d["themes"] == ["sack_carrier"]
        hb = d["board"]["ball"]["held_by"]
        if hb >= 0:                                # the ENEMY carrier holds the ball
            assert hb // N_TEAM_SLOTS != d["board"]["active_team"]


def test_boards_are_legal_all_families():
    for d in _all(build_drills(_replay(), replay_id="1901960")):
        board = d["board"]
        assert board["active_team"] in (0, 1)
        assert d["races"] == ["High Elf", "Tomb Kings"]
        seen_slots = {0: set(), 1: set()}
        seen_sq = set()
        for p in board["players"]:
            assert p["status"] in _VALID_STATUS
            assert 0 <= p["x"] < BOARD_WIDTH and 0 <= p["y"] < BOARD_HEIGHT
            assert 0 <= p["slot"] < N_TEAM_SLOTS
            assert p["slot"] not in seen_slots[p["team"]]
            seen_slots[p["team"]].add(p["slot"])
            assert (p["x"], p["y"]) not in seen_sq
            seen_sq.add((p["x"], p["y"]))
            assert not any(s.startswith("+") for s in p["skills"])
        assert len(seen_slots[0]) >= 3 and len(seen_slots[1]) >= 3


def test_developed_player_identity_preserved():
    """A developed Dragon Prince keeps base + learned skills; Mighty Blow-type
    skills get their standard BB2025 param."""
    drills = build_drills(_replay(), replay_id="1901960")["score"]
    dp = next(p for d in drills for p in d["board"]["players"]
              if p.get("position") == "Dragon Prince" and "Dodge" in p["skills"])
    assert {"Block", "My Ball", "Steady Footing", "Dodge", "Sidestep"} <= set(dp["skills"])
    assert dp["ma"] >= 8                            # +MA advance folded into the stat
    tg = next(p for d in drills for p in d["board"]["players"]
              if "Mighty Blow" in p["skills"])
    assert tg["skill_params"].get("Mighty Blow") == 1


def test_inventory_triage():
    inv = inventory(_replay(), replay_id="1901960")
    assert inv["matchup"] == ["High Elf", "Tomb Kings"]
    assert inv["drills"]["score"] == 9
    assert inv["drills"]["drop"] == 2
    assert inv["events"]["drops_forced"] == 2
    assert inv["events"]["passes"] == 1            # bash game: ~no passing
    assert inv["events"]["blocks"] == 57
