# Talk, then contribute

The reference **multi-GM composition**: a repeated public-goods game with a
communication phase. Each step, every player's flow runs through two chained
game masters —

1. **`talk_gm`** (`messaging` backend): send one private message or broadcast
   — promise, threaten, coordinate.
2. **`game_gm`** (`public_goods` backend): privately choose a contribution;
   the round resolves simultaneously at the next step boundary.

This is the "cheap talk before the move" structure used across the
communication-in-games literature: does talk raise cooperation, and do agents
keep their word?

## Run it

```sh
uv run silisocs --config-path scenarios/talk_then_contribute/conf \
  world=talk_then_contribute agents=talk_then_contribute env=talk_then_contribute
```

## What to look at

- Per-GM action logs land under `<output>/talk_gm/` and `<output>/game_gm/`;
  the run manifest indexes both.
- Studio: the interaction-network panel draws the who-messages-whom graph
  (messaging declares `interaction.directed`); with two backends in the run,
  the network and behavior panels grow an **Environment** selector to read one
  GM at a time.
- The public-goods capability study's evaluator
  (`experiments/studies/public_goods_capability/eval.py`) scores the game half
  of a run directory unchanged — per-GM logs are discovered automatically.

## Structure notes

- `conf/env/talk_then_contribute.yaml` declares both GMs under
  `gm_orchestration.gms` and binds the default flow through the chain
  (`flow_bindings.flow_to_gms.default: [talk_gm, game_gm]`).
- `conf/sim.yaml` selects the concurrent chain traversal
  (`engine.step.built_in: multi_gm`) and a deterministic roster
  (`participation: all`).
- The end-to-end contract is pinned by
  `tests/e2e/test_scripted_backend_matrix.py::test_multi_gm_talk_then_contribute_chain`,
  which runs this scenario with a scripted model.
