# fumbblr

Convert **FUMBBL** (Fantasy Football / FFB) Blood Bowl replays into
[bloodygit](https://github.com/) training drills — `scenario/v1` *pinned* boards
that reproduce the exact game state (real teams, real skills, real injuries) at
tactically interesting moments.

The core path is **fully offline**: you hand it a local replay file and it writes
JSON drills. Downloading a replay from FUMBBL by id/match-id/launcher is an
**optional** add-on, used only when you give it something that isn't a file.
fumbblr is pure-Python **stdlib only** — no third-party dependencies.

```bash
pip install -e .

fumbblr replay_1901960.gz            # local file -> drills (no network at all)
fumbblr replay_1901960.gz --stats    # triage: what is this game good for?
fumbblr 4701297                      # OPTIONAL: download match 4701297, then convert
```

---

## Input: file first, download optional

A *source* is resolved by `fumbblr.sources.load_source()`, which dispatches on
what you give it:

| Source | Example | Network? | Handled by |
|---|---|---|---|
| local replay file (`.gz` / `.json`) | `replay_1901960.gz` | **no** | `sources.load_replay_file` (core) |
| FUMBBL `.jnlp` launcher | `~/Downloads/ffblive.jnlp` | yes | `fetch` (optional) |
| bare **replay id** | `1901960` | yes | `fetch` (optional) |
| bare **match id** | `4701297` | yes | `fetch` (optional) |

Only the first is offline. The other three need to download the replay, which is
the job of the optional `fumbblr.fetch` module. That module is **imported
lazily** — a file-only run never loads it (and never touches `urllib`), so a
deployment that only ever processes local replay files needs nothing from the
network layer.

Force offline mode with `--no-fetch` (API: `load_source(src, allow_fetch=False)`):
a non-file source then fails fast with a clear error instead of reaching out.

```python
from fumbblr import load_replay_file, build_drills

replay, replay_id = load_replay_file("replay_1901960.gz")   # offline core
families = build_drills(replay, replay_id=replay_id)        # -> dict of drills
```

The optional download feature (only if you want it):

```python
from fumbblr.fetch import fetch_replay_by_id    # imports urllib; hits fumbbl.com
replay = fetch_replay_by_id(1901960)            # cached under ~/.cache/fumbblr/
```

Network fetches are single-replay, cached on disk, and polite (identifying
User-Agent, small delay). This is a converter, **not** a bulk scraper.

### Modules

| Module | Role | Network |
|---|---|---|
| `replay.py` | parse the command stream, reconstruct the board, detect events | none |
| `convert.py` | turn detected events into `scenario/v1` drill dicts | none |
| `mapping.py` | FFB ↔ bloodygit constants (board, player status, race/skill names) | none |
| `sources.py` | resolve a source → `(replay, id)`; **offline** for files | none |
| `fetch.py` | **optional**: download a replay/match/jnlp from FUMBBL | yes |
| `cli.py` | argument parsing, family selection, file output | none |

---

## How it works

An FFB replay is a log of the websocket command stream between the FUMBBL server
and client. The payload is `gameLog.commandArray` — a list of commands; the
`serverModelSync` ones each carry a `modelChangeList` (model mutations) and a
`reportList` (game events).

`replay.py` does a **single linear pass** over that stream, maintaining a live
`FieldState`, and **deep-copies a snapshot at the start of every team turn**.
The model-change ids it consumes (verified against real 2007 and 2026 replays):

| modelChangeId | key | value | used for |
|---|---|---|---|
| `fieldModelSetPlayerCoordinate` | playerId | `[x, y]` | player position |
| `fieldModelSetPlayerState` | playerId | int (`& 255` = base) | player status |
| `fieldModelSetBallCoordinate` | — | `[x, y]` / None | ball position |
| `fieldModelRemovePlayer` | playerId | — | player left the pitch |
| `turnDataSetTurnNr` | home/away | int | turn number (snapshot trigger) |
| `turnDataSetReRolls` | home/away | int | team rerolls remaining |
| `gameSetHalf` | — | int | half (1/2) |
| `gameSetTurnMode` | — | str | setup / kickoff / regular / … |
| `teamResultSetScore` | home/away | int | running score |
| `actingPlayerSetPlayerId` | — | playerId | who is currently acting |

A snapshot only becomes a drill if real play is happening at that turn-start
(`turn_mode ∈ {regular, kickoff}` — not setup or end-of-game).

### The board models line up — so geometry copies across verbatim

| | FFB | bloodygit |
|---|---|---|
| Board | 26 × 15, origin top-left | `BOARD_WIDTH=26, BOARD_HEIGHT=15`, same origin |
| Coords | `(x, y)` | `(x, y)` — copied with no scaling or flipping |
| Player state | `PlayerState` int | `PlayerStatus` (mapped in `mapping.py`) |

End zones in bloodygit are fixed: **seat 0 attacks `x=25`, seat 1 attacks
`x=0`**. fumbblr reads which end zone each team attacks from the **ball position
at each touchdown**, then places the drill's active ("self") team on whichever
seat attacks that end zone. So no coordinates are ever mirrored.

Output is byte-compatible with the `scenario/v1` pinned templates bloodygit's
`engine/scenario.py` produces, and every generated drill passes bloodygit's own
`scenario.validate()` + `instantiate()`.

---

## What is extracted

This is the heart of the tool, so be precise about it. Extraction happens at
three levels: **per replay**, **per detected event**, and **per drill / per
player**.

### 1. Per replay (`ParsedReplay`)

From one linear pass, fumbblr collects:

- **`snapshots`** — a full `FieldState` deep-copy at the start of every team turn
  during live play (player coords, player statuses, ball, half, both turn
  numbers, both rerolls, running score).
- **`players`** — `player_id → PlayerInfo` for **both** teams, read from
  `playerArray` + `roster.positionArray` (see per-player fields below).
- **`races`** — `{home, away}` race names (the matchup).
- **`team_attacks`** — each team → the end-zone x (0 or 25) it attacks, derived
  from touchdowns (the unseen team gets the opposite end).
- **`touchdowns`** — from `turnEnd` reports carrying `playerIdTouchdown`,
  cross-checked against `teamResultSetScore`. Each records half, scoring team,
  that team's turn number, scorer id, the end-zone x reached, and the running
  score just after.
- **`sacks`** — a `block` report whose `defenderId` is the player standing on the
  ball square (the enemy **ball-carrier**), thrown by the other team.
- **`block_counts`** — blocks thrown per `(half, team, turn)` (bash pressure).
- **`pass_events` / `handoff_events` / `foul_events`** — discrete skill actions
  (`pass`/`passRoll`, `handOff`, `foul` reports) bucketed under the acting team's
  turn, with the acting player id.
- **`inventory`** — raw counts of every report id seen (touchdowns, blocks,
  blitzes, injuries, pickups, ball scatters, kickoffs, stallers, …), for triage.

### 2. Per drill (`scenario/v1` dict)

Each drill pins one snapshot and adds:

| Field | Meaning |
|---|---|
| `schema` | `"scenario/v1"` |
| `id` | `clk1_…` / `sack_…` / `block_…` / `pass_…` / `handoff_…` / `foul_…` (family = `id.rsplit("_",1)[0]`) |
| `desc` | human description (replay id, what happened, half/turn) |
| `weight` | `1.0` |
| `themes` | e.g. `["score_run"]`, `["sack_carrier"]`, `["passing_play"]` |
| `source` | provenance: `{kind: "fumbbl_replay", replay_id, drill, …}` (scorer/carrier/actor id, `turns_before_td`, `clk`, `n_blocks` as relevant) |
| `races` | **seat-indexed** matchup, e.g. `["High Elf", "Tomb Kings"]` — pins real rosters |
| `clock` | `{half, turn}` — half and the active team's turn number |
| `score` | `{self_lead}` — active team's lead at the snapshot |
| `resources` | `{self_rerolls, opp_rerolls}` |
| `board` | the pinned board (below) |

### 3. Per board / per player — the real-team fidelity

The point is to train against the **actual competitive teams** — league rosters
with developed skills, niggling stat reductions, players KO'd / in the injury box
— not pristine tournament line-ups.

The **board** carries `mode: "pinned"`, `active_team` (the seat of the "self"
team), the `ball` (`{x, y, held_by}` where `held_by` is the global player id or
`-1`), and one entry per **on-pitch** player. Each **player** entry pins:

- **placement**: `slot` (0–15, stable per team in jersey-number order over the
  players on the pitch), `team` (seat 0/1), `x`, `y`;
- **`status`**: bloodygit `PlayerStatus` mapped from the FFB `PlayerState`
  (ACTIVE / PRONE / STUNNED / CASUALTY / SENT_OFF / …);
- **`position`**: the position label (e.g. `"Dragon Prince"`);
- **current stats** `ma / st / ag / pa / av` — already reflecting injuries **and**
  stat-up advances (FFB `+MA`/`+AG` advance *markers* are dropped because the
  boost is already folded into the stat); `pa` of `0` means cannot pass;
- **`skills`**: the **full** current skill set (base + learned), spelled the
  bloodygit way (`normalize_skill`);
- **`skill_params`**: numeric parameters for parameterised skills
  (`Loner 4+`, `Mighty Blow +1`, `Dirty Player +1`, …), with standard BB2025
  defaults filled in where FFB omits them;
- **`keywords`**: player-type keywords (Animosity / Hatred targeting);
- **`has_ball`**: present + `true` for the carrier.

**Injured / KO'd players are simply absent from the snapshot**, so an
under-strength real game becomes an outnumbered drill for free.

### What is deliberately NOT extracted / dropped

- **Off-pitch players** — anyone in a dugout / reserves / KO / injury box is not
  emitted (matching hand-written bloodygit drills, which list pitch players only;
  bloodygit defaults the rest to RESERVE).
- **FFB advance markers** (`+MA`, `+AG`, …) — dropped, because the stat already
  reflects the advance.
- **Star-only / unmodelled skills** — names bloodygit's `SKILL_INDEX` doesn't
  know (e.g. "The Flashing Blade") are dropped downstream; the player is still
  placed with their stats and recognised skills.
- **Keyword skill *targets*** (Animosity/Hatred *of whom*) — left to bloodygit's
  keyword handling rather than re-indexed here.

---

## Drill families

Six families are mined from one replay. The family is encoded in the id so
bloodygit can route it (`family = id.rsplit("_", 1)[0]`):

| Family | id prefix | What it captures | Active team | Default dir |
|---|---|---|---|---|
| **score** | `clk1` / `clk2` / `clk3` | start of the scoring team's turn, 0 / 1 / 2 turns before each TD — the score-in-N-turns **depth ladder** | scorer | `drills_clock/fumbbl/` |
| **sack** | `sack` | start of the **defending** team's turn in which they blitzed the enemy ball-carrier | defender | `scenarios_defense/fumbbl/` |
| **block** | `block` | start of a **bash-pressure** turn (≥ `--min-blocks`, default 3), minus turns already emitted as sacks | basher | `scenarios_defense/fumbbl/` |
| **pass** | `pass` | start of a turn in which the acting team completed a pass | passer | `drills_clock/fumbbl/` |
| **handoff** | `handoff` | start of a turn in which the acting team made a hand-off | hander | `drills_clock/fumbbl/` |
| **foul** | `foul` | start of a turn in which the acting team fouled | fouler | `scenarios_defense/fumbbl/` |

The `clk1/clk2/clk3` ids slot straight into bloodygit's existing
`--curriculum-rungs clk1,clk2,clk3,clk4` reverse-curriculum ladder; the defensive
families are picked up by `--defense-dir` (which descends one subdir level — hence
the `fumbbl/` subdir). All families inherit the full per-player fidelity above.

---

## Output

Drills are written under a **data root** (default: `<repo>/data/`, which is
gitignored), in a tree that mirrors the bloodygit dirs its curriculum reads, so
it can be copied straight in:

```
data/
  drills_clock/fumbbl/        clk1_*.json clk2_*.json clk3_*.json pass_*.json handoff_*.json
  scenarios_defense/fumbbl/   sack_*.json block_*.json foul_*.json
```

Override the root with `--out DIR` (the same `{drills_clock,scenarios_defense}/fumbbl/`
tree is created under it). `--dry-run` and `--stats` write nothing.

### Example drill

A 1-turn scoring drill (`clk1_<digits>.json`):

```jsonc
{
  "schema": "scenario/v1",
  "id": "clk1_1901960000",                 // family clk1 (1-turn score)
  "themes": ["score_run"],
  "source": {"kind": "fumbbl_replay", "replay_id": "1901960",
             "drill": "score", "scorer_id": "…",
             "turns_before_td": 0, "clk": 1},
  "races": ["High Elf", "Tomb Kings"],      // seat-indexed; pins the real matchup
  "clock": {"half": 1, "turn": 6},          // active team's turn number
  "score": {"self_lead": 0},                // active team's lead at the snapshot
  "resources": {"self_rerolls": 2, "opp_rerolls": 1},
  "board": {
    "mode": "pinned", "active_team": 0,
    "players": [{"slot": 0, "team": 0, "x": 24, "y": 11, "status": "ACTIVE",
                 "position": "Dragon Prince", "ma": 9, "st": 3, "ag": 1,
                 "pa": 4, "av": 9, "skills": ["Block", "Dodge", "Sidestep"],
                 "skill_params": {"Mighty Blow": 1}, "keywords": ["Blitzer", "Elf"],
                 "has_ball": true}],
    "ball": {"x": 24, "y": 11, "held_by": 0}
  }
}
```

---

## CLI

```bash
fumbblr SOURCE [SOURCE ...] [options]
```

| Option | Default | Effect |
|---|---|---|
| `--out DIR` | `<repo>/data` | output root for the drill tree |
| `--no-fetch` | off | offline mode: accept local replay files only, never download |
| `--turns-before N` | 2 | turns before each TD to snapshot (→ clk1..N+1) |
| `--min-blocks N` | 3 | blocks/turn to qualify as a bash-pressure drill |
| `--families a,b,…` | all six | comma-separated families to emit |
| `--stats` | off | report each replay's drill yield + event counts, write nothing |
| `--dry-run` | off | print a summary, write nothing |

```bash
# Triage a pile of replays by what they're good for (writes nothing):
fumbblr replay_1901960.gz --stats
#   1901960: High Elf vs Tomb Kings
#      drills available  -> score(clk):9  sack:3  block:7  pass:1  handoff:0  foul:1
#      events            -> TDs:3 passes:1 blocks:57 blitzes:30 ...

fumbblr replay_1901960.gz --families score          # one family only
fumbblr replay_1901960.gz --out /tmp/drills         # custom output root
fumbblr 4701297 --no-fetch                           # errors: id needs the network
```

---

## Consuming the drills (bloodygit)

The drills are byte-compatible `scenario/v1`, but the full real-player fidelity
relies on two small, **backward-compatible** bloodygit additions in
`engine/scenario.py` (these live in bloodygit, **not** in this repo):

1. `instantiate()` honours a template-level `"races"` (overrides the caller's
   matchup; absent → unchanged).
2. `_instantiate_pinned()` honours per-player `skills` (full bitset replace),
   `skill_params`, `keywords`, and `position`, alongside the existing
   `add_skills` + stat overrides.

With those, every pinned player is reproduced with **zero** stat / skill / param
mismatches in the live `GameState`, even when the trainer passes a different
matchup. fumbblr itself imports nothing from bloodygit — you can generate drills
with Python alone; you only need (patched) bloodygit to *train on* them.

---

## Develop

```bash
pip install -e ".[dev]"
python -m pytest        # offline tests against the checked-in 2026 fixture
```

The test suite is fully offline (it runs against `tests/fixtures/replay_1901960.gz`
and never touches the network or the `fetch` module).

## Possible extensions

- **Pass drills**: a bashy game has ~no passing (the 2026 fixture has 1 pass).
  `--stats` lets you farm passing-heavy replays for the `pass`/`handoff` families.
- **Imitation learning**: per-decision `(state, action)` trajectories — the
  command stream contains the actions too, not just the turn-start boards.
