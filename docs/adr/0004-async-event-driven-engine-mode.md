# ADR 0004: Async Event-Driven Engine Mode

## Status

Proposed.

> **Note (2026-07-07):** This ADR is about a distinct feature — an *event-driven*
> (discrete-event) engine mode — and remains proposed. Its Context/Decision claim
> that the language-model layer stays synchronous is now **out of date**: an
> optional asyncio *turn executor* plus additive `sample_*_async` / `act_async`
> methods have since landed, selected by `sim.engine.executor: asyncio` (default
> `threads`). That async path is an execution-throughput change with identical
> round-based behavior; it does **not** implement the event-driven semantics this
> ADR proposes. See `docs/configuration.md` (`sim.engine.executor`) and
> `SCALABILITY_PLAN.md` Phase 2.

## Context

The runtime engine today is round-based but already compute-parallel. The
`FixedStepsLoopStrategy` advances discrete steps (global ticks); within a step the
step strategy groups agents into flow batches, runs flows sequentially, and runs
agents within a flow concurrently through a thread pool. Each turn observes a
snapshot of world state at batch start, calls `act()` (a blocking language-model
call that overlaps with other agents), and resolves under a per-game-master lock.
The language-model layer is synchronous; concurrency comes from threads.

This gives simultaneous-move, lockstep rounds. A conversation thread advances at
most one exchange per global step, so causal chains (a post, then a reaction, then
a reply to that reaction) take several steps to play out. "Async agents" is often
requested, but it splits into two very different things, and only one is a real
change.

## Decision

Add an **event-driven engine mode** as a new `LoopStrategy` plus `StepStrategy`
(selected via `sim.engine.step.built_in: async_event`) and a scheduler component
that replaces `next_acting`. We do **not** port the language-model layer to
`asyncio`: thread-pool parallelism already covers concurrent model calls, so an
`asyncio` rewrite changes implementation, not behavior, and is deferred.

The mode is a **deterministic discrete-event simulation**:

- A virtual clock and a priority queue ordered by `(sim_timestamp, seeded_tiebreak)`
  drive execution. Agents self-schedule wake-ups and resolved actions enqueue
  reactive triggers (for example, a mention wakes the mentioned agent).
- Each agent observes live state at its own wake time (just-in-time observation),
  not a per-tick snapshot, so causal chains resolve within one logical window.
- Model latency is modeled as a *simulated* duration, not wall-clock time, so runs
  remain reproducible under a fixed seed.
- Events apply in timestamp order on a single apply loop, preserving the existing
  write-serialization guarantees (per-GM lock, single-writer backend).

The `Agent` interface is unchanged: `observe(event)` / `act()` is already
event-shaped, and backends already event-source their actions in
`action_events.jsonl`.

## Consequences

- Behavior is genuinely different: reactive, just-in-time agents instead of
  lockstep simultaneous moves, a closer model of a live social system.
- Reproducibility, checkpointing, and per-episode evaluation are preserved only
  because the scheduler is deterministic. A checkpoint must additionally persist
  the pending event queue and the virtual clock; probes and telemetry move from
  per-episode to time-windowed.
- The round-based engine remains the default. The async mode is opt-in and slots
  into existing extension points, so agents and backends need no changes.
- Interleaved reads can race (an agent acting on slightly stale state); event
  ordering by virtual timestamp plus idempotent resolution contains this.
