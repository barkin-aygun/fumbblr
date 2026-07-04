"""FFB (FUMBBL client) <-> bloodygit constants and small mapping helpers.

The two game models line up almost perfectly:

* FFB pitch is 26 wide (X: 0..25) x 15 tall (Y: 0..14), origin top-left.
  bloodygit is BOARD_WIDTH=26, BOARD_HEIGHT=15 with the same (x, y) origin.
  -> board coordinates copy across verbatim; no scaling or flipping.
* FFB places off-pitch players at negative X (home dugout) or X>=30 (away
  dugout). We only ever emit on-pitch players (matching every hand-written
  bloodygit drill, which lists pitch players only); off-pitch players default
  to RESERVE inside bloodygit.

bloodygit reference: engine/constants.py (BOARD_WIDTH/HEIGHT, N_TEAM_SLOTS),
engine/state.py (PlayerStatus), engine/scenario.py (the scenario/v1 pinned
board shape produced by ``snapshot()``).
"""
from __future__ import annotations

# --- board geometry (identical on both sides) ---------------------------- #
BOARD_WIDTH = 26
BOARD_HEIGHT = 15
N_TEAM_SLOTS = 16  # bloodygit: pid = team_seat * N_TEAM_SLOTS + slot

SCHEMA = "scenario/v1"


def on_pitch(x: int, y: int) -> bool:
    return 0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT


# --- player state -> bloodygit PlayerStatus name ------------------------- #
# FFB PlayerState is an int; the low 8 bits (value & 255) hold the base state,
# higher bits are modifiers (ACTIVE/CONFUSED/ROOTED/...). We only need the base.
# See doc reference in the FFB replay format notes.
_FFB_STATE = {
    0: "ACTIVE",    # UNKNOWN -> treat an on-pitch unknown as standing
    1: "ACTIVE",    # STANDING
    2: "ACTIVE",    # MOVING (mid-activation; standing for snapshot purposes)
    3: "PRONE",
    4: "STUNNED",
    5: "KO",        # off-pitch in practice (dugout coord) -> never emitted
    6: "CASUALTY",  # BADLY_HURT
    7: "CASUALTY",  # SERIOUS_INJURY
    8: "CASUALTY",  # RIP
    9: "RESERVE",   # off-pitch -> never emitted
    10: "RESERVE",  # MISSING
    11: "PRONE",    # FALLING (transient)
    12: "ACTIVE",   # BLOCKED (transient stand)
    13: "SENT_OFF",  # BANNED
    14: "ACTIVE",   # EXHAUSTED
}


def status_name(ffb_state: int | None) -> str:
    """bloodygit PlayerStatus name for an FFB PlayerState int (base 8 bits)."""
    if ffb_state is None:
        return "ACTIVE"
    return _FFB_STATE.get(int(ffb_state) & 255, "ACTIVE")


# --- race name -> bloodygit rosters.json key ----------------------------- #
# FFB's modern (BB2025) race names already match bloodygit's roster keys; this
# map only needs entries where they diverge.  Unknown races pass through and
# would surface as a clean error at instantiate() (extend this map to fix).
_RACE_ALIASES: dict = {
    "Chaos": "Chaos Chosen",
    "Lizardman": "Lizardmen",
    "Undead": "Shambling Undead",
    "Khorne Daemonkin": "Khorne",
}


def map_race(ffb_race: str) -> str:
    return _RACE_ALIASES.get(ffb_race, ffb_race)


# --- skill name -> bloodygit SKILL_INDEX spelling ------------------------ #
# FFB Title-Cases every word and capitalises after hyphens ("Throw Team-Mate",
# "On The Ball", "Dump-Off"); bloodygit lowercases connectives and hyphen tails
# ("Throw Team-mate", "On the Ball", "Dump-off").  We normalise generically and
# keep an explicit alias map for the handful that don't follow the rule.  Names
# bloodygit doesn't know (star-only / unmodelled skills) are dropped downstream.
_SKILL_ALIASES: dict = {
    "Ball and Chain": "Ball & Chain",
    "Pro ": "Pro",
    # FFB spells these differently from bloodygit; without the alias the skill
    # was DROPPED from every replayed board (silent corpus corruption,
    # found 2026-07-04: 'Side Step' x101 lost Sidestep entirely).
    "Side Step": "Sidestep",
    "No Hands": "No Ball",
    "Fumblerooskie": "Fumblerooski",
}
_CONNECTIVES = {"the", "of", "and", "in", "a"}


def normalize_skill(name: str) -> str:
    name = name.strip()
    if name in _SKILL_ALIASES:
        return _SKILL_ALIASES[name]
    words = name.split(" ")
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if i > 0 and low in _CONNECTIVES:
            out.append(low)
        elif "-" in w:  # lowercase the tail of a hyphenated word (Team-Mate)
            head, *rest = w.split("-")
            out.append("-".join([head] + [r.lower() for r in rest]))
        else:
            out.append(w)
    return " ".join(out)
