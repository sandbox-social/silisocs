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
restore strategy with `sim.checkpoint.restore`. Restore selects the latest
checkpoint, initializes runtime scaffolding, and then applies checkpointed
object state. Built-in agents, Game Masters, components, and local backends use
`get_state()` / `set_state()` for that restore. The native social replay
strategy remains available for source runs that do not carry backend state; it
rebuilds backend actions through `GameMaster.resolve_action`.

`action_events.jsonl` remains a backend-domain log. The engine does not emit
separate replay payloads.

## Consequences

- Restore behavior is explicit and checkpoint-owned.
- Simulation initialization remains reserved for application startup content,
  such as seed posts.
- Backends can make restore robust by keeping durable world state behind their
  own `get_state()` / `set_state()` hooks.
