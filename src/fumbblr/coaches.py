"""Optional feature: discover *good games by top coaches* on FUMBBL.

This is the harvesting counterpart to :mod:`fumbblr.fetch`.  Where ``fetch``
downloads one replay you point at, this module finds *which* replays are worth
converting: it reads FUMBBL's public toplist to seed a set of strong coaches,
lists each coach's recent matches, and ranks those matches by how much tactically
interesting action they contain -- all from small metadata endpoints, **without
downloading a single replay**.  The replays themselves are fetched later, one at
a time, by :mod:`fumbblr.harvest` through the polite :mod:`fumbblr.fetch` path.

Like ``fetch``, this module is imported lazily and is the only other part of the
project that touches the network.

Endpoints used (all light-weight JSON/XML, no replay payloads):

  POST /api/clickhouse/topList     -> toplist rows (coach name + a team id)
  GET  /api/team/get/{teamId}      -> that team's coach id (name -> id bridge)
  GET  /xml:matches?c={coachId}    -> a coach's ~26 most recent matches (+ stats)
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .fetch import _api_bytes, _fetch_bytes  # polite, backing-off transport

_SITE = "https://fumbbl.com"

# The competitive divisions worth mining.  FUMBBL's ``division`` field is numeric
# in the match XML (2 == Competitive) but named in the toplist API.
DIVISION_NAME = {"1": "Ranked", "2": "Competitive", "3": "League", "4": "Blackbox"}


# --------------------------------------------------------------------------- #
# Toplist -> coach ids
# --------------------------------------------------------------------------- #

def _post_json(path: str, body: dict) -> dict:
    """POST ``body`` as JSON to /api/{path} and return the decoded response,
    through fetch's polite/back-off transport (the toplist is fumbblr's only
    POST)."""
    raw = _fetch_bytes(f"{_SITE}/api/{path}",
                       data=json.dumps(body).encode(),
                       headers={"Content-Type": "application/json"})
    return json.loads(raw.decode())


def coach_id_for_team(team_id: str | int) -> tuple[str, str] | None:
    """Resolve a team id to ``(coach_id, coach_name)`` via /api/team/get."""
    meta = json.loads(_api_bytes(f"team/get/{team_id}").decode())
    coach = meta.get("coach") or {}
    cid = coach.get("id")
    return (str(cid), coach.get("name", "")) if cid else None


def top_coaches(*, stat: str = "rating", coach_type: str = "active",
                division: str = "Competitive", roster: str = "all",
                position: str = "all", limit: int = 15) -> list[dict]:
    """Return up to ``limit`` distinct top coaches as
    ``[{"coach_id", "name", "team_id"}]``, best first.

    FUMBBL's public toplist is player-centric (top players by a stat), but each
    row names the owning coach and one of their teams, which we bridge to a coach
    id via /api/team/get.  We keep the first (highest-ranked) appearance of each
    coach.  This costs one POST plus one small GET per distinct coach -- and is
    cached by the harvester's state file, so a refresh is rare.
    """
    resp = _post_json("clickhouse/topList", {
        "stat": stat, "type": coach_type, "division": division,
        "roster": roster, "position": position,
    })
    rows = resp.get("data", []) if isinstance(resp, dict) else []
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        name = row.get("coach")
        team_id = row.get("team_id")
        if not name or not team_id or name in seen:
            continue
        seen.add(name)
        resolved = coach_id_for_team(team_id)
        if not resolved:
            continue
        cid, cname = resolved
        out.append({"coach_id": cid, "name": cname or name, "team_id": str(team_id)})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# A coach's recent matches (+ ranking)
# --------------------------------------------------------------------------- #

@dataclass
class MatchInfo:
    """One row of ``xml:matches?c=`` -- enough to rank the game without
    downloading its replay."""
    match_id: str
    date: str
    division: str
    coaches: tuple[str, str]          # (home_coach_id, away_coach_id)
    touchdowns: int = 0
    casualties: int = 0
    blocks: int = 0
    passing: int = 0                  # total passing yards
    completions: int = 0
    fouls: int = 0
    scores: tuple[int, int] = (0, 0)  # (home_td, away_td)

    def score(self) -> float:
        """A heuristic for how many good training moments a game likely yields.

        The six drill families reward variety: scores (touchdowns), ball delivery
        (completions / passing), and bash/defence (casualties, blocks, fouls).
        We weight the rarer, higher-signal events (TDs, completed passes) most,
        add a mild bonus for a *contested* scoreline (both teams scoring => more
        end-to-end play), and only lightly count blocks so a pure grind doesn't
        dominate."""
        home_td, away_td = self.scores
        contested = min(home_td, away_td)          # 0 if a shut-out
        return (
            self.touchdowns * 3.0
            + self.completions * 3.0
            + self.passing * 0.02
            + self.fouls * 1.0
            + self.casualties * 0.6
            + self.blocks * 0.05
            + contested * 4.0
        )


def _agg(side: ET.Element, key: str) -> int:
    return sum(int(p.get(key, 0)) for p in side.findall("performances/performance"))


def parse_matches(xml_bytes: bytes) -> list[MatchInfo]:
    """Parse an ``xml:matches`` document into :class:`MatchInfo` rows."""
    root = ET.fromstring(xml_bytes)
    out: list[MatchInfo] = []
    for m in root.findall("match"):
        home, away = m.find("home"), m.find("away")
        if home is None or away is None:
            continue
        cid = lambda s: (s.find("coach").get("id") if s.find("coach") is not None else "")
        td = lambda s: int(s.findtext("touchdowns") or 0)
        cas = lambda s: int((s.find("casualties").get("total") if s.find("casualties") is not None else 0) or 0)
        out.append(MatchInfo(
            match_id=m.get("id"),
            date=m.findtext("date") or "",
            division=m.findtext("division") or "",
            coaches=(cid(home), cid(away)),
            touchdowns=td(home) + td(away),
            casualties=cas(home) + cas(away),
            blocks=_agg(home, "blocks") + _agg(away, "blocks"),
            passing=_agg(home, "passing") + _agg(away, "passing"),
            completions=_agg(home, "completions") + _agg(away, "completions"),
            fouls=_agg(home, "fouls") + _agg(away, "fouls"),
            scores=(td(home), td(away)),
        ))
    return out


def coach_matches(coach_id: str | int, *, division: str | None = "2") -> list[MatchInfo]:
    """List a coach's recent matches (``xml:matches?c=``), newest first.

    ``division`` filters to one FUMBBL division id (default ``"2"`` = Competitive);
    pass ``None`` to keep every division."""
    xml = _fetch_bytes(f"{_SITE}/xml:matches?c={coach_id}")
    matches = parse_matches(xml)
    if division is not None:
        matches = [m for m in matches if m.division == division]
    return matches


def ranked_matches(coach_id: str | int, *, division: str | None = "2",
                   min_score: float = 0.0) -> list[MatchInfo]:
    """A coach's recent matches, filtered and sorted best-game-first."""
    ms = [m for m in coach_matches(coach_id, division=division)
          if m.score() >= min_score]
    ms.sort(key=lambda m: m.score(), reverse=True)
    return ms
