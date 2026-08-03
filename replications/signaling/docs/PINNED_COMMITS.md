# Pinned baselines

Both arms of the signaling port are frozen against exact commits.

| Arm | Repo | Commit | Ref |
|-----|------|--------|-----|
| Concordia baseline | https://github.com/google-deepmind/concordia | `7779a4c9f96bad10816d88c54e4cb17d53ac5222` | tag `signaling-baseline` in `/scratch/sneheel/silisocs-benchmark/repos/concordia`; worktree checkout at `/scratch/sneheel/external/concordia-signaling-7779a4c` |
| SiliSocS | this repo | `cfaa45b` (v0.4.0, `main`) | tag `v0.4.0` |

Notes:

- The signaling example (`examples/signaling/`) does **not** exist in Concordia
  v2.4.0 (the S5-benchmark pin); it only exists on upstream `main`. The pin above
  is the upstream `main` tip as of 2026-08-01.
- `concordia/contrib/components/game_master/marketplace.py` is behaviorally
  identical between v2.4.0 and the pin (only `# pyrefly: ignore` comments differ).
- Port surface at the pin: `run.py` 196 + `simulation.py` 445 + `dial.py` 331 +
  `agents/consumer.py` 256 + `agents/convo_agent.py` 466 = 1,694 example lines,
  plus contrib `marketplace.py` 857, `dial_dyad_initializer.py` 158, and
  `day_in_the_life_initializer.py` (prompts + event generation). Data:
  `configs/goods.py` 835 + `configs/personas.py` 4,834.
- `configs/signaling.py` referenced by the upstream README does not exist at the
  pin; Part-2 non-monetary signaling is out of scope (no baseline to port).
