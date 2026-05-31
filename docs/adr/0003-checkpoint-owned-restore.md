# ADR 0003: Checkpoint-Owned Restore

## Status

Accepted.

## Context

Checkpoint restore was previously modeled as simulation initialization. That
made replay look like seed content and required engine-level replay payloads
in `action_events.jsonl`.

## Decision

Checkpoint restore belongs under `sim.checkpoint`, not
`sim.initialization.simulation`.

Users provide a prior output directory with `sim.checkpoint.source_run` and a
restore strategy with `sim.checkpoint.restore`. The native social restore
selects the latest checkpoint, loads runtime object state, initializes game
masters, and replays backend action events through `GameMaster.resolve_action`.

`action_events.jsonl` remains a backend-domain log. The engine does not emit
separate replay payloads.

## Consequences

- Restore behavior is explicit and checkpoint-owned.
- Simulation initialization remains reserved for application startup content,
  such as seed posts.
- Non-social restore requires a custom checkpoint restore strategy.
