# fumbblr

Convert **FUMBBL** (Fantasy Football / FFB) Blood Bowl replays into
[bloodygit](../bloodygit) training drills (`scenario/v1` pinned boards).

It mines **three drill families** from one replay, each written to the bloodygit
dir its curriculum already reads (bloodygit derives the family from the drill id
as `id.rsplit("_", 1)[0]`):

| Family | What it captures | id → family | Lands in |
|---|---|---|---|
| **score** | start of the scoring team's turn, 0/1/2 turns before each TD — the score-in-N-turns **depth ladder** | `clk1`/`clk2`/`clk3` | `data/drills_clock/fumbbl/` |
| **sack** | start of the **defending** team's turn where they blitzed the enemy ball-carrier (`active_team` = defender) | `sack` | `data/scenarios_defense/fumbbl/` |
| **block** | start of a **bash-pressure** turn (≥3 blocks) | `block` | `data/scenarios_defense/fumbbl/` |

The `clk1/clk2/clk3` families slot straight into bloodygit's existing
`--curriculum-rungs clk1,clk2,clk3,clk4` reverse-curriculum ladder; the sack/block
families are picked up by `--defense-dir` (default `data/scenarios_defense`, which
descends one subdir level). Both inherit the full real-team fidelity below.

## How it works

FFB replays are a log of the websocket command stream between the FUMBBL server
and client (`gameLog.commandArray`). We replay the model mutations
(`fieldModelSetPlayerCoordinate`, `fieldModelSetPlayerState`,
`fieldModelSetBallCoordinate`, `turnDataSetTurnNr`, `gameSetHalf`, …) to
reconstruct the exact board at any point, and read touchdowns from `turnEnd`
reports (`playerIdTouchdown`) cross-checked with `teamResultSetScore`.

The two game models line up almost perfectly, which makes the conversion exact:

| | FFB | bloodygit |
|---|---|---|
| Board | 26 × 15, origin top-left | `BOARD_WIDTH=26, BOARD_HEIGHT=15`, same origin |
| Coords | `(x, y)` | `(x, y)` — copied verbatim |
| Player state | `PlayerState` int | `PlayerStatus` (mapped in `mapping.py`) |

End zones in bloodygit are fixed (seat 0 attacks `x=25`, seat 1 attacks `x=0`).
We map each scoring team to whichever seat attacks the end zone it actually
reached (read from the **ball** position at the touchdown), so geometry carries
across with no flipping.

Output is byte-compatible with the `scenario/v1` pinned templates bloodygit's
`engine/scenario.py` produces, and every generated drill passes bloodygit's own
`scenario.validate()` + `instantiate()`.

## Real teams, reproduced exactly

The whole point is to train against the **actual competitive teams** — league
rosters with developed skills, niggling stat reductions, players KO'd / in the
injury box — not pristine tournament line-ups. Each drill therefore pins:

- the **matchup**: a seat-indexed `"races": ["High Elf", "Tomb Kings"]` so the
  real rosters are fielded (team-level rerolls / special rules / apothecary);
- every on-pitch player's **exact current identity**, sourced from the replay's
  `playerArray` + `roster.positionArray`:
  - current `ma/st/ag/pa/av` (already reflecting injuries and stat-up advances —
    FFB's `+MA`/`+AG` advance markers are dropped, since the boost is in the
    stat);
  - the **full skill set** (base + learned), spelled the bloodygit way;
  - parameterised-skill values (`Loner 4+`, `Mighty Blow +1`, …);
  - player-type `keywords` (Animosity / Hatred targeting);
  - the `position` label.

Injured / KO'd players are simply absent from the snapshot, so an under-strength
real game becomes an outnumbered drill for free.

This relies on two small, backward-compatible bloodygit additions (`engine/scenario.py`):

1. `instantiate()` honours a template-level `"races"` (overrides the caller's
   matchup; absent → unchanged).
2. `_instantiate_pinned()` honours per-player `skills` (full bitset replace),
   `skill_params`, `keywords`, and `position`, alongside the existing
   `add_skills` + stat overrides.

Verified end-to-end: every pinned player is reproduced with **zero** stat /
skill / param mismatches in the live `GameState`, even when the trainer passes a
different matchup.

## Usage

```bash
# Mine all families from a FUMBBL match id (resolved via match/get -> replayId),
# writing each into the right bloodygit data dir:
fumbblr 4701297

# A replay id directly, or the .jnlp launcher FUMBBL hands you, or a local file:
fumbblr 1901960
fumbblr ~/Downloads/ffblive.jnlp
fumbblr path/to/replay_1901960.gz --turns-before 2 --dry-run

# Triage: what is a replay good for? (writes nothing)
fumbblr replay_1901960.gz --stats
#   1901960: High Elf vs Tomb Kings
#      drills available  -> score(clk): 9  sack: 3  block: 7
#      events            -> TDs:3 passes:1 blocks:57 blitzes:30 ...

# Emit only some families, or redirect output:
fumbblr 1901960 --families score
fumbblr 1901960 --out /tmp/drills        # writes /tmp/drills/{score,sack,block}/
```

A *source* may be: a FUMBBL **match id**, a **replay id**, a **`.jnlp`**
launcher, or a local **`.gz` / `.json`** replay file. Network fetches are
single-replay, cached under `~/.cache/fumbblr/`, and polite — this is a
converter, **not** a bulk scraper.

`--stats` reports each replay's drill yield and event counts so you can triage a
pile of replays by what they're good for (bashy games → sack/block, passing
games → future pass drills). bloodygit's catalogue loaders descend one subdir
level, so the `fumbbl/` subdirs are picked up automatically.

## Output shape

A scoring drill (`clk<N>_<digits>.json`; sack/block are `sack_*` / `block_*`):

```jsonc
{
  "schema": "scenario/v1",
  "id": "clk1_1901960000",                 // family clk1 (1-turn score)
  "themes": ["score_run"],
  "source": {"kind": "fumbbl_replay", "replay_id": "1901960",
             "drill": "score", "scorer_id": "…",
             "turns_before_td": 0, "clk": 1},
  "races": ["High Elf", "Tomb Kings"],      // seat-indexed; pins the real matchup
  "clock": {"half": 1, "turn": 6},
  "score": {"self_lead": 0},               // active team's lead at the snapshot
  "resources": {"self_rerolls": 2, "opp_rerolls": 1},
  "board": {
    "mode": "pinned", "active_team": 0,
    "players": [{"slot": 0, "team": 0, "x": 24, "y": 11, "status": "ACTIVE",
                 "position": "Dragon Prince", "ma": 9, "st": 3, "ag": 1,
                 "pa": 4, "av": 9, "skills": ["Block", "Dodge", "Sidestep", …],
                 "skill_params": {"Mighty Blow": 1}, "keywords": ["Blitzer", "Elf"],
                 "has_ball": true}, …],
    "ball": {"x": 24, "y": 11, "held_by": 0}
  }
}
```

Sack/block drills have the same shape with `"drill": "sack"`/`"block"` and
`active_team` = the defending/bashing team (the enemy carrier holds the ball).

## Develop

```bash
python -m pytest        # offline tests against the checked-in 2026 fixture
```

## Notes & possible extensions

- **Star players & unmodelled skills**: star-only skills the engine doesn't have
  (e.g. "The Flashing Blade") are silently dropped by bloodygit's `SKILL_INDEX`
  lookup; the player is still placed with their stats and known skills.
- **Keyword-param skills**: numeric params (Loner/Mighty Blow/Dirty Player) are
  carried; keyword params (Animosity/Hatred *targets*) are left to bloodygit's
  keyword handling rather than re-indexed here.
- **Pass drills**: a bashy game has ~no passing (the 2026 fixture has 1 pass).
  `--stats` lets you triage replays so you farm passing games for a future
  `pass` family; the throw events are already detected (`ParsedReplay.passes`).
- **Future**: per-decision `(state, action)` trajectories for
  imitation learning — the command stream contains the actions too.
```
