"""Offline tests for the harvesting layer (coach discovery + batch orchestration).

No network: the toplist / xml:matches / replay downloads are all stubbed, and
the replay itself is the checked-in 2026 fixture.  These cover the pure logic --
match ranking, queue building/de-duplication, crash-safe state -- that decides
*which* games get converted."""
from pathlib import Path

import fumbblr.coaches as coaches
import fumbblr.fetch as fetch
from fumbblr.coaches import MatchInfo, parse_matches
from fumbblr.harvest import make_harvester
from fumbblr.replay import load_replay

FIXTURE = Path(__file__).parent / "fixtures" / "replay_1901960.gz"

_MATCHES_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<matches>
  <match id="100"><date>2026-01-01 00:00:00</date><division>2</division>
    <home id="1"><coach id="10"/><name>A</name><touchdowns>2</touchdowns>
      <casualties total="1"/>
      <performances><performance blocks="5" passing="6" completions="1" fouls="0"/></performances></home>
    <away id="2"><coach id="20"/><name>B</name><touchdowns>2</touchdowns>
      <casualties total="0"/>
      <performances><performance blocks="4" passing="0" completions="0" fouls="0"/></performances></away>
  </match>
  <match id="200"><date>2026-01-02 00:00:00</date><division>2</division>
    <home id="1"><coach id="10"/><name>A</name><touchdowns>3</touchdowns>
      <casualties total="0"/>
      <performances><performance blocks="1" passing="0" completions="0" fouls="0"/></performances></home>
    <away id="3"><coach id="30"/><name>C</name><touchdowns>0</touchdowns>
      <casualties total="0"/>
      <performances><performance blocks="1" passing="0" completions="0" fouls="0"/></performances></away>
  </match>
  <match id="300"><date>2026-01-03 00:00:00</date><division>1</division>
    <home id="1"><coach id="10"/><name>A</name><touchdowns>0</touchdowns>
      <casualties total="0"/><performances/></home>
    <away id="4"><coach id="40"/><name>D</name><touchdowns>0</touchdowns>
      <casualties total="0"/><performances/></away>
  </match>
</matches>"""


def test_parse_matches_and_division_filter():
    ms = parse_matches(_MATCHES_XML)
    assert [m.match_id for m in ms] == ["100", "200", "300"]
    m = ms[0]
    assert m.touchdowns == 4 and m.completions == 1 and m.passing == 6
    assert m.coaches == ("10", "20") and m.scores == (2, 2)


def test_score_rewards_contested_and_passing():
    ms = {m.match_id: m for m in parse_matches(_MATCHES_XML)}
    # match 100 (2-2, a completed pass) beats match 200 (3-0 shut-out, no pass)
    assert ms["100"].score() > ms["200"].score()


def test_harvest_end_to_end_offline(tmp_path, monkeypatch):
    """Full batch with the network stubbed: ranking -> queue -> convert -> write,
    plus de-dup on a second run."""
    replay = load_replay(FIXTURE)

    def fake_ranked(coach_id, *, division="2", min_score=0.0):
        ms = [m for m in parse_matches(_MATCHES_XML)
              if (division is None or m.division == division) and m.score() >= min_score]
        ms.sort(key=lambda m: m.score(), reverse=True)
        return ms

    monkeypatch.setattr(coaches, "ranked_matches", fake_ranked)
    # match 100 -> replay 1000 (1000 % 5 == 0: eval holdout); match 200 -> 2001
    monkeypatch.setattr(fetch, "resolve_replay_id_from_match",
                        lambda mid: f"{mid}0" if mid == "100" else f"{mid}1")
    monkeypatch.setattr(fetch, "fetch_replay_by_id", lambda rid: replay)

    out = tmp_path / "data"
    h = make_harvester(out, top_n=1, per_coach=10, min_score=0.0)
    h.state["coaches"] = [{"coach_id": "10", "name": "A"}]  # skip toplist network

    s1 = h.run_batch(2)
    assert s1["processed"] == 2                       # 2 competitive games (div 2)
    assert s1["drills"] > 0
    assert len(h.state["done"]) == 2
    # drills actually written in the bloodygit layout, per-replay eval split:
    # replay 2001 -> main tree; replay 1000 (%5 == 0) -> eval_holdout mirror
    assert list((out / "drills_clock" / "fumbbl").glob("fclk1_*.json"))
    assert list((out / "drills_drop" / "fumbbl").glob("drop1_*.json"))
    hold = out / "eval_holdout"
    assert list((hold / "drills_clock" / "fumbbl").glob("fclk1_*.json"))
    assert list((hold / "drills_drop" / "fumbbl").glob("drop1_*.json"))
    # nothing from the holdout replay leaked into the main tree (and vice versa)
    import json as _json
    for f in (out / "drills_clock" / "fumbbl").glob("fclk1_*.json"):
        assert _json.loads(f.read_text())["source"]["replay_id"] == "2001"
    for f in (hold / "drills_clock" / "fumbbl").glob("fclk1_*.json"):
        assert _json.loads(f.read_text())["source"]["replay_id"] == "1000"

    # state persisted; a fresh harvester over the same dir skips the done games
    h2 = make_harvester(out, min_score=0.0)
    assert len(h2.state["done"]) == 2
    h2.state["coaches"] = [{"coach_id": "10", "name": "A"}]
    s2 = h2.run_batch(5)
    assert s2["processed"] == 0                        # nothing new to do
