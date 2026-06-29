"""Parse an FFB replay command stream and reconstruct board state.

An FFB replay is a log of websocket packets between the FUMBBL server and the
client.  The interesting payload is ``gameLog.commandArray`` -- a list of
commands; the ``serverModelSync`` ones each carry a ``modelChangeList`` (model
mutations) and a ``reportList`` (game events).  Replaying the mutations in order
reconstructs the exact field state at any point; the reports flag touchdowns.

We do a single linear pass, maintaining a live ``FieldState``, and capture a
deep snapshot at the start of every team turn.  Touchdowns are read from
``turnEnd`` reports (``playerIdTouchdown``) cross-checked against
``teamResultSetScore`` running totals.

Model-change ids we consume (verified against real 2007 and 2026 replays):
  fieldModelSetPlayerCoordinate  key=playerId  value=[x, y]
  fieldModelSetPlayerState       key=playerId  value=int (base = value & 255)
  fieldModelSetBallCoordinate                  value=[x, y] (or None / 0)
  fieldModelRemovePlayer         key=playerId
  turnDataSetTurnNr              key=home|away value=int (that team's turn no.)
  turnDataSetReRolls             key=home|away value=int
  gameSetHalf                                  value=int (1 or 2)
  gameSetTurnMode                              value=str (setup/kickoff/regular/...)
  teamResultSetScore             key=home|away value=int (running score)
"""
from __future__ import annotations

import copy
import gzip
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .mapping import on_pitch


def on_pitch_xy(xy) -> bool:
    return bool(xy) and on_pitch(xy[0], xy[1])


def _other(team: str | None) -> str | None:
    if team is None:
        return None
    return "away" if team == "home" else "home"

# Active turn modes: a snapshot is only a useful drill start if real play is
# happening (not setup / kickoff-event resolution / end-of-game).
_PLAY_MODES = {"regular", "kickoff"}

# x >= BOARD_MID -> the scorer reached the right end zone (x=25); else left (x=0)
BOARD_MID = 13


def load_replay(source) -> dict:
    """Load a replay dict from an already-parsed dict, a ``.json`` path, or a
    gzipped ``.gz`` path."""
    if isinstance(source, dict):
        return source
    p = Path(source)
    if p.suffix == ".gz":
        with gzip.open(p, "rb") as f:
            return json.load(f)
    with open(p) as f:
        return json.load(f)


@dataclass
class PlayerInfo:
    player_id: str
    team: str          # "home" | "away"
    nr: int
    name: str
    position_name: str
    player_type: str   # "Regular" | "Big Guy" | "Star" | ...
    ma: int            # CURRENT stats (reflect injuries / advances), FFB == bloodygit
    st: int
    ag: int
    pa: int            # passing; None/0 -> cannot pass
    av: int
    skills: list       # full current skill set (raw FFB names)
    skill_params: dict  # raw skill name -> int param (Loner/Mighty Blow/Dirty Player)
    keywords: list     # player-type keywords (Animosity / Hatred targeting)


def _numeric(v):
    """FFB skillValues are strings like '4', '+1' (or keyword strings / None)."""
    if v is None:
        return None
    try:
        return int(str(v).strip().strip("+"))
    except ValueError:
        return None  # keyword param (Animosity/Hatred target) -> not numeric


def extract_players(replay: dict) -> dict:
    """player_id -> PlayerInfo for both teams.

    Each player's CURRENT stats + full skill list come from the player record;
    base-skill params and keywords come from the matching ``positionArray``
    entry (the player record's ``skillValuesMap`` overrides where present)."""
    out: dict = {}
    game = replay["game"]
    for team in ("home", "away"):
        side = game["team" + team.capitalize()]
        pos_by_id = {p["positionId"]: p
                     for p in side.get("roster", {}).get("positionArray", [])}
        for p in side["playerArray"]:
            pos = pos_by_id.get(p.get("positionId"), {})
            skills = list(p.get("skillArray", []))

            # params: position base-skill values, overlaid with player-specific
            params: dict = {}
            pos_skills = pos.get("skillArray", []) or []
            pos_vals = pos.get("skillValues", []) or []
            for name, val in zip(pos_skills, pos_vals):
                n = _numeric(val)
                if n is not None:
                    params[name] = n
            for name, val in (p.get("skillValuesMap") or {}).items():
                n = _numeric(val)
                if n is not None:
                    params[name] = n
            params = {k: v for k, v in params.items() if k in skills}

            out[p["playerId"]] = PlayerInfo(
                player_id=p["playerId"], team=team, nr=p.get("playerNr", 0),
                name=p.get("playerName", ""),
                position_name=pos.get("positionName", p.get("playerType", "")),
                player_type=p.get("playerType", "Regular"),
                ma=p.get("movement"), st=p.get("strength"), ag=p.get("agility"),
                pa=p.get("passing"), av=p.get("armour"),
                skills=skills, skill_params=params,
                keywords=list(pos.get("keywords", [])),
            )
    return out


@dataclass
class FieldState:
    """Live, mutable reconstruction of the pitch."""
    coord: dict = field(default_factory=dict)   # player_id -> [x, y]
    state: dict = field(default_factory=dict)   # player_id -> base PlayerState int
    ball: list = field(default_factory=lambda: [-1, -1])
    half: int = 0
    turn_mode: str = "startGame"
    turn: dict = field(default_factory=lambda: {"home": 0, "away": 0})
    rerolls: dict = field(default_factory=lambda: {"home": 0, "away": 0})
    score: dict = field(default_factory=lambda: {"home": 0, "away": 0})

    def clone(self) -> "FieldState":
        return copy.deepcopy(self)


@dataclass
class TurnSnapshot:
    half: int
    team: str           # the team whose turn is starting ("home"/"away")
    turn: int           # that team's turn number (1..8)
    command_nr: int
    field: FieldState


@dataclass
class Touchdown:
    half: int
    team: str           # scoring team ("home"/"away")
    turn: int           # scoring team's turn number
    scorer_id: str
    command_nr: int
    end_zone_x: int     # 0 or 25 -- the end zone the scorer reached
    score: dict         # running score immediately after the TD


@dataclass
class Sack:
    """A block thrown at the opposing ball-carrier (a sack attempt)."""
    half: int
    def_team: str       # the blocking / defending team (the drill's "self")
    turn: int           # the defending team's turn number
    command_nr: int
    carrier_id: str     # the enemy carrier being attacked


@dataclass
class BlockTurn:
    """A team-turn in which a team threw several blocks (bash pressure)."""
    half: int
    team: str
    turn: int
    n_blocks: int


@dataclass
class ActionEvent:
    """A discrete skill action (a pass throw, a hand-off, or a foul) performed by
    ``team`` during its turn -- the basis for a single-turn drill where that team
    is the active ("self") side and must reproduce the action."""
    half: int
    team: str           # the acting team (the drill's "self")
    turn: int           # the acting team's turn number
    command_nr: int
    actor_id: str       # the passer / hander / fouler
    kind: str           # "pass" | "handoff" | "foul"


class ParsedReplay:
    """One linear pass over the command stream: collects turn-start snapshots,
    touchdowns, sacks (blocks on the carrier), per-turn block counts, passes,
    each team's attacking end zone, and a drill-type inventory."""

    def __init__(self, replay: dict):
        self.replay = replay
        self.players = extract_players(replay)
        self.races = {team: replay["game"]["team" + team.capitalize()].get("race")
                      for team in ("home", "away")}
        self.snapshots: list[TurnSnapshot] = []
        self.touchdowns: list[Touchdown] = []
        self.sacks: list[Sack] = []
        self.passes: list[dict] = []
        self.pass_events: list[ActionEvent] = []     # pass throws (by acting team)
        self.handoff_events: list[ActionEvent] = []  # hand-offs
        self.foul_events: list[ActionEvent] = []     # fouls
        self.block_counts: Counter = Counter()    # (half, team, turn) -> n blocks
        self.inventory: Counter = Counter()        # event kind -> count
        self.team_attacks: dict = {}               # team -> end-zone x (0 or 25)
        self._parse()
        self._resolve_orientation()

    # -- lookups --------------------------------------------------------- #
    def team_of(self, player_id: str) -> str | None:
        pi = self.players.get(player_id)
        return pi.team if pi else None

    def snapshot_for(self, half: int, team: str, turn: int) -> TurnSnapshot | None:
        """The turn-start snapshot for a given (half, team, turn), or None."""
        for s in self.snapshots:
            if s.half == half and s.team == team and s.turn == turn:
                return s
        return None

    def block_turns(self, min_blocks: int = 3) -> list[BlockTurn]:
        """Team-turns with at least ``min_blocks`` blocks (bash pressure)."""
        return [BlockTurn(half=h, team=t, turn=tn, n_blocks=n)
                for (h, t, tn), n in self.block_counts.items() if n >= min_blocks]

    def _carrier(self, st: FieldState) -> str | None:
        """The player standing on the ball square (the carrier), or None."""
        if not on_pitch_xy(st.ball):
            return None
        for pid, xy in st.coord.items():
            if xy == st.ball:
                return pid
        return None

    # -- the pass -------------------------------------------------------- #
    def _parse(self) -> None:
        st = FieldState()
        acting = None        # current acting player id
        for cmd in self.replay["gameLog"]["commandArray"]:
            if cmd.get("netCommandId") != "serverModelSync":
                continue
            cn = cmd.get("commandNr")
            turn_started: list[tuple[str, int]] = []

            for ch in cmd["modelChangeList"]["modelChangeArray"]:
                cid = ch.get("modelChangeId")
                ck = ch.get("modelChangeKey")
                cv = ch.get("modelChangeValue")
                if cid == "fieldModelSetPlayerCoordinate":
                    st.coord[ck] = cv
                elif cid == "fieldModelSetPlayerState" and cv is not None:
                    st.state[ck] = int(cv) & 255
                elif cid == "fieldModelSetBallCoordinate" and hasattr(cv, "__len__"):
                    st.ball = list(cv)
                elif cid == "fieldModelRemovePlayer":
                    st.coord.pop(ck, None)
                    st.state.pop(ck, None)
                elif cid == "gameSetHalf":
                    st.half = cv
                elif cid == "gameSetTurnMode":
                    st.turn_mode = cv
                elif cid == "turnDataSetReRolls":
                    st.rerolls[ck] = cv
                elif cid == "teamResultSetScore":
                    st.score[ck] = cv
                elif cid == "actingPlayerSetPlayerId":
                    acting = cv
                elif cid == "turnDataSetTurnNr":
                    st.turn[ck] = cv
                    turn_started.append((ck, cv))

            # capture a snapshot at the *start* of each team turn during play
            for team, turn in turn_started:
                if st.turn_mode in _PLAY_MODES:
                    self.snapshots.append(TurnSnapshot(
                        half=st.half, team=team, turn=turn,
                        command_nr=cn, field=st.clone()))

            for rep in cmd["reportList"]["reports"]:
                rid = rep.get("reportId")
                self.inventory[rid] += 1
                if rid == "turnEnd" and rep.get("playerIdTouchdown"):
                    scorer = rep["playerIdTouchdown"]
                    team = self.team_of(scorer) or "home"
                    # The scorer is already in the dugout by the time the turnEnd
                    # report fires, but the ball still sits on the TD square -> its
                    # x identifies the end zone reached.
                    end_x = 25 if st.ball[0] >= BOARD_MID else 0
                    self.touchdowns.append(Touchdown(
                        half=st.half, team=team, turn=st.turn.get(team, 0),
                        scorer_id=scorer, command_nr=cn, end_zone_x=end_x,
                        score=dict(st.score)))
                elif rid == "block":
                    blk_team = self.team_of(acting)
                    if blk_team:
                        key = (st.half, blk_team, st.turn.get(blk_team, 0))
                        self.block_counts[key] += 1
                        carrier = self._carrier(st)
                        if carrier and rep.get("defenderId") == carrier \
                                and self.team_of(carrier) != blk_team:
                            self.sacks.append(Sack(
                                half=st.half, def_team=blk_team,
                                turn=st.turn.get(blk_team, 0), command_nr=cn,
                                carrier_id=carrier))
                elif rid in ("pass", "passRoll", "handOff"):
                    self.passes.append({"half": st.half, "command_nr": cn,
                                        "report": rid})
                    self._record_action(st, rid, rep, acting, cn)
                elif rid == "foul":
                    self._record_action(st, rid, rep, acting, cn)

    def _record_action(self, st, rid, rep, acting, cn) -> None:
        """Bucket a pass / hand-off / foul report under the acting team's turn."""
        if rid in ("pass", "passRoll"):
            actor = rep.get("playerId") or acting
            team = self.team_of(actor)
            kind, bucket = "pass", self.pass_events
        elif rid == "handOff":
            actor = rep.get("playerId") or acting
            team = self.team_of(actor)
            kind, bucket = "handoff", self.handoff_events
        else:  # foul: the report names the victim; the fouler is the acting player.
            victim_team = self.team_of(rep.get("defenderId"))
            actor = acting
            team = self.team_of(acting)
            # A foul is always against an opponent; if the acting player is
            # unknown or (wrongly) on the victim's team, infer the fouling team
            # as the victim's opponent.
            if victim_team and team != _other(victim_team):
                team = _other(victim_team)
            kind, bucket = "foul", self.foul_events
        if team:
            bucket.append(ActionEvent(
                half=st.half, team=team, turn=st.turn.get(team, 0),
                command_nr=cn, actor_id=actor or "", kind=kind))

    def _resolve_orientation(self) -> None:
        """Each team keeps the same end zone all game; derive it from the TDs
        (a team's TD end zone = the end it attacks), filling the unseen team as
        the opposite. Games with no TD leave the map empty (defense/block drills
        that need it are skipped)."""
        for td in self.touchdowns:
            self.team_attacks[td.team] = td.end_zone_x
        for a, b in (("home", "away"), ("away", "home")):
            if a in self.team_attacks and b not in self.team_attacks:
                self.team_attacks[b] = 0 if self.team_attacks[a] == 25 else 25
