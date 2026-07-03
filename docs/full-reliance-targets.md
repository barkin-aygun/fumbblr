# What I need to rely fully on this data

Harvest-volume targets for retiring bloodygit's *generated* drill corpus in
favour of pure human-harvested (FUMBBL) scenarios. Written 2026-07-03 alongside
the "Clock 1" curriculum cutover (human `fclk1` offense + `drop1`/`sack`
defense). Counts below are **train-side** — i.e. what's in the main tree after
the automatic per-replay `eval_holdout/` split (~20% of replays) is taken out.

Check progress any time:

```bash
for d in data/drills_clock/fumbbl data/drills_drop/fumbbl; do
  echo "$d"; ls $d | sed 's/_[0-9]*\.json//' | sort | uniq -c
done
```

## Why thresholds at all

Three failure modes bound how few boards we can train on:

1. **Eval starvation.** The anchored Wilson gates need ≥30 frozen eval drills
   per gated rung to move at all (at n=48/96 samples per eval, fewer boards
   than that means the gate is re-testing the same handful of positions).
   The holdout is ~20% of replays, so ≥30 eval boards ⇒ ≥150 total per rung.
2. **Memorization.** At ~54 games/cycle and a focus weight of 3.0, a 50-board
   family gets each board replayed every few cycles — the net can learn the
   *answers*, not the *skill*. Rule of thumb: a focus family should have
   enough boards that one board recurs at most ~1×/cycle (≥150–300 boards).
3. **Coach/race monoculture.** 19 games came from 4 coaches. A net trained on
   one coach's habits learns that coach, not Blood Bowl. Diversity floors
   below.

## The stages

| Stage | What it unlocks | Train-side thresholds |
|---|---|---|
| **A — go-live** | New curriculum live; generated corpus retained as unmanaged retention @ weight 0.25 | `fclk1 ≥ 150`, `drop1 ≥ 100` |
| **B — retire generated offense variety** | Delete generated `clk2`–`clk4`, `pass`, `passover`, `handoff`, `block_crack` from the training mix | `fclk1 ≥ 300`, `fclk2 ≥ 200`, `fclk3 ≥ 200`, `drop1 ≥ 250`, `sack ≥ 150` |
| **C — fully human** | Delete generated `clk1`, `def1`, `block_sack` too; pure human-scenario training | ≥ 800 offense boards (fclk1–3 + pass), ≥ 800 defense boards (drop1 + sack), **≥ 25 distinct coaches**, **≥ 12 races represented** |

Current standing (2026-07-03, 20 replays converted): fclk1 = 50, fclk2 = 50,
fclk3 = 43, drop1 = 33, sack = 17 (train-side). Drop yield ≈ 1.8/game, fclk1
≈ 2.5/game — Stage A needs roughly **60 games**, Stage B roughly **150**,
Stage C roughly **350–450** (drop1 is the binding constraint at ~1.8/game).

If the harvest plateaus before Stage B/C (queue exhaustion even at
top-n 50 / per-coach 12 across divisions), that is the trigger for the
**Oracle** (generative scenario synthesis seeded from the harvested boards) —
out of scope for the harvester.

## How each stage is executed (bloodygit side)

No code changes — each stage is a TOML edit + trainer group-restart:

- **A**: `configs/v2-flat2.toml` — `curriculum_rungs = "fclk1,fclk2,fclk3"`,
  `defense_rungs = "drop1,sack"`, `defense_mix = 0.5`,
  `unmanaged_weight = 0.25`.
- **B**: delete the retired generated drill files from `data/drills_clock/`
  (or move them out of the dir); restart.
- **C**: same for `data/drills_clock/clk1_*`, `data/drills_def/`,
  `data/drills_block_def/`; drop the `defense_clock_dir` /
  old `defense_dir` keys; restart.

Restart = kill the **process group** (`kill -- -$(cat runs/v2-flat2/trainer.pgid)`),
never just the main PID; the watchdog relaunches with `--resume`.

## Diversity levers (in preference order)

1. `--top-n 50 --per-coach 12` (live since 2026-07-03).
2. Second division pass: `fumbblr-harvest --refresh --division 4` (Blackbox)
   adds a disjoint coach pool; `--division all` is the widest net.
3. Lower `--min-score` below 8.0 only as a last resort — it trades board
   quality for volume.
