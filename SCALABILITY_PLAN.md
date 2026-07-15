# Scalability Analysis & Plan — 100 → 10k → 1M agents

Five parallel deep audits (engine/scheduling, backends, GM/components, agents/LLM,
orchestration/checkpointing/eval) produced the findings below. This document is the
synthesis: where the system breaks at each scale, whether the *structures* (seams)
survive, and a phased plan that keeps every seam we designed while removing the
walls behind them.

---

## 1. Executive verdict

**The seams are largely ready; the defaults behind them are not.** The
architecture's policy slots — `StepStrategy`, `TurnPolicy`, `ParticipationPolicy`,
`Router`, the GM component slots (`next_acting`/`observe`/`resolve`/`update`),
`SocialBackendApp` + `@app_action`, the LLM provider registry, the checkpoint
*restore* strategy, `EngineRecorder`, probe include-filters, study
`command_override` — all hide "how" behind "what", so scalable implementations can
drop in without redesign.

What actually blocks scale falls into three buckets:

1. **Outright defects** (O(N²) loops, unbounded thread pools, missing indexes,
   dead `active_user_ids` plumbing) — fixable in place, no structure change.
2. **Non-scalable default implementations** behind good seams (full-roster recsys
   recompute, monolithic JSON checkpoint, all-agent probes, whole-file replay,
   base64 DB snapshots) — replaceable behind the same seams.
3. **A small set of data-plane contracts that genuinely block 1M**: the fully
   materialized `list[Agent]` roster passed through every seam; sync-only
   `Agent.act()`/`LanguageModel.sample_*`; thread-per-turn execution under the
   GIL; the single per-GM lock around observe+resolve; single-writer SQLite; no
   checkpoint *save* seam. These need targeted structural additions (not
   rewrites): a duck-typed roster handle, optional async model methods, an
   O(active) step path, and a save-strategy slot mirroring the restore slot.

**Design target (maintainer decision, 2026-07-04):** the infrastructure must
assume **1M+ ACTIVE LLM-driven agents per step** as the best case, and
10k–100k active as the second-best case, single machine first, Postgres
approved behind the backend seam, with 10k as the first tier that must work
well. 1M active/step ≈ 10¹⁰–10¹¹ tokens/step, which presumes high-throughput
(likely local, vLLM-class) inference endpoints — already reachable through the
OpenAI-compatible provider seam (`api_base`). The framework-side consequence is
stark: **every per-turn serialization point must become batched/pipelined** —
no thread-per-call, no one-at-a-time lock sections, no row-at-a-time writes.
Two principles govern the plan: per-step cost is **O(active) with small
constants** (never O(population) overhead on top), and every stage of the turn
(observe → act → resolve) must have a **batch path**. Participation sampling
remains a knob for cheaper runs, not a crutch the architecture depends on.

---

## 2. Where it breaks, by scale

### Works today: N ≈ 100
Everything functions. Waste exists (per-agent dict copies, pool churn,
full-prompt debug logs) but is invisible at this size.

### Wall 1: N ≈ 1,000–10,000 — "the defect wall"
Hit in roughly this order:

| # | Bottleneck | Where | Cost |
|---|-----------|-------|------|
| 1 | Recsys recomputes **all users × 1000 posts every step**; full `DELETE FROM recommendations` + row-by-row INSERT; `lazy`/`active_user_ids` plumbing exists but is dead (`update.py:237` hardcodes `None`) | `twitter_like/engine.py:1205,1288,1301`, `reddit_like/engine.py:899,990`, `gm/components/social_media/update.py:147,236` | O(N·1000) score ops + ~N·max_posts single-row INSERTs per step; embedding modes add N transformer forwards |
| 2 | `agent_flow_tag` copies the whole `agent_flow_tags` dict **per agent per step** | `scheduling.py:193-196` ← `steps.py:24-32` | **O(N²)/step** |
| 3 | Duplicate-name check scans the agent list per insertion; per-agent `print` | `runtime/construction/assembly.py:93,67` | **O(N²)** startup |
| 4 | Init thread pool sized `max_workers=len(tasks)` — one OS thread per agent | `runtime/execution/concurrency.py:14` | O(N) threads (~8 MB stack each); hard fail ~10k |
| 5 | Per-GM `threading.Lock` serializes **observe + resolve** for every turn; global guard lock taken 2×/turn | `base_engines.py:103-116,173,186` | 2N serialized lock sections/step; timeline DB reads single-file |
| 6 | Single-writer SQLite queue: every action is a blocking round-trip through one thread/connection | `sqlite_engine.py:49,134-155,191-193` | O(total writes), zero write parallelism |
| 7 | Per-agent feed query per step; feed `ORDER BY p.id DESC` can't use `(user_id, created_at)` index; `get_user_id` SELECT per action | `twitter_like/engine.py:552-578,556`, `sqlite_engine.py:242` | N sequential fan-out reads/step |
| 8 | Monolithic JSON checkpoint: deepcopy + `json.dump` of ALL agent state, **every step** under studies (`every_n_steps=1`); N deep copies of identical `shared_memories`; full-DB base64 snapshot in memory | `checkpointing/state.py:39-89`, `run_study.py:308-324`, `sqlite_state.py:24` | O(N) deepcopy + full-tree dump per step; DB×1.33 in RAM |
| 9 | Probes deploy to **all** agents by default, every step | `evaluations/probes/deployment.py:171`, policy defaults `:30-36` | O(N·probes·steps) LLM calls |
| 10 | Participation: `_top_up` scans a list (O(N²)); Markov re-derives from step 0 (O(steps²)/run) | `policies/participation.py:79-87,162` | superlinear per step/run |
| 11 | Follow-network `random` generator is a double loop; setup does N+E serialized single-row writes | `backends/social/network.py:181-204`, `twitter_like/app.py:81-92` | O(N²) gen; O(N+E) blocking init writes |
| 12 | Seed-post init: **serial** LLM call per agent | `initialization/simulation/seed_posts.py:46-52` | O(N) sequential round-trips |
| 13 | Debug LLM logging writes the **full prompt** of every call to one JSONL; producers block when the 200k queue fills | `openai.py:60,249`, `io/jsonl.py:21,50` | GB/step at 10k; back-pressures workers |

### Wall 2: N ≈ 10,000–100,000 — "the throughput wall"
Even with Wall 1 fixed:
- **LLM concurrency ceiling**: sync blocking HTTP in threads, worker cap 256
  (`engine_metrics.py:65,93`) → wall-clock/step ≥ N·latency/256 (~40 waves at
  10k). `sample_choice` multiplies a turn by up to **20 sequential calls**
  (`openai.py:26,253-283`). No async path exists anywhere in the model ABC.
- **GIL**: all Python-side turn work (prompt render, parse, memory update) runs
  on one core regardless of thread count.
- **Full-history prompts**: `NativeAgent._context()` re-sends up to 100
  observations (~50k–200k tokens) per call (`native.py:91-105`) — provider TPM
  quotas saturate long before CPU does.
- **Per-step O(population) work regardless of active set**: `run_step` updates
  every GM with the **full roster** before participation filters
  (`base_engines.py:229-237`); `_refresh_context` rebuilds N-tuples every step
  (`game_master.py:380-390`); telemetry sorts all active names per step and
  retains them per episode (`scheduling.py:397`, `collector.py:114-116`).
- **SQLite single-writer** becomes the hard floor for any write-heavy step.

### Wall 3: N ≈ 1,000,000 — "the structural wall"
- **Memory**: ~1 MB steady-state per `NativeAgent` (100-obs window + 1000-entry
  memory, `native.py:51-52`) × 1M resident objects ≈ **1 TB** in one process.
  Everything passes a materialized `list[Agent]`.
- **Compute semantics**: 1M LLM calls/step is economically infeasible —
  population must be state-backed with a sampled active head (the `Agent` ABC's
  `get_state`/`set_state` already supports hydration; nothing exercises it).
- **Single process, single host**: threads + GIL + one SQLite file + one global
  `SimMetricsCollector` singleton.
- **Checkpoint format**: one JSON file holding everything; restore replays the
  entire action log loaded as a whole list (`restore.py:78-96,311-325`).

---

## 3. The plan

Design rule for every item: **keep the seam, replace what's behind it; where a
contract must grow, grow it additively** (a new optional method, a new slot, a
duck-typed handle) — never a breaking redesign. Second rule: **every per-step
cost becomes O(active), never O(population).**

### Phase 0 — Defect removal (unlocks ~1k → 10k; no structural change)

**STATUS: LANDED (2026-07-04).** All items below are implemented, with 7 new
regression tests (`tests/test_scalability_phase0.py`) and a benchmark harness
(`benchmarks/scaling_benchmark.py`: real runner, LLM disabled, reports startup /
per-step wall / peak RSS). Measured on the dev host (shared cgroup cap of 512
pids, so runs use `sim.max_concurrent_actions=48`):

| N | before | after |
|---|--------|-------|
| 100 | 2.6s wall, 0.14s/step, 86 MB | 1.8s wall, 0.13s/step, 92 MB |
| 1,000 | **crash at init** ("can't start new thread") | 5.8s wall, 1.24s/step, 130 MB |
| 5,000 | — | 16.8s wall, 6.1s/step, 223 MB |

Per-step cost is now ~linear in N; the remaining per-step slope is dominated by
the full-roster recsys recompute and per-agent feed reads — exactly Phase 1's
O(active) targets.

Engine/scheduling:
- `agent_flow_tag`: read `flow_map.get()` off the live mapping — no dict copy
  (`scheduling.py:193`). Kills the O(N²).
- `_top_up`: membership via `set` (`participation.py:79`). Markov: cache
  per-agent chain state per step instead of re-deriving from step 0 (stays
  seed-derived and replay-stable) (`participation.py:162`).
- Pre-create per-GM locks/semaphores at engine init; drop the guard-lock-per-call
  (`base_engines.py:109`).
- Reuse one turn pool across a step instead of pool-per-batch
  (`scheduling.py:296`).

Construction/init:
- Set-based duplicate-name check; batch or debug-gate the per-agent `print`
  (`assembly.py:93,67`).
- Cap the init pool with the same `worker_limit` pattern the action phase uses
  (`concurrency.py:14`).
- Parallelize the seed-post provider through the bounded pool
  (`seed_posts.py:46`).

Backend:
- `executemany` + single transaction for recsys inserts, `setup_social_state`
  (bulk `create_user`/`follow`), and drop the per-update
  `SELECT COUNT(*) FROM recommendations` log query (`app.py:230`).
- Per-run username→id cache (`sqlite_engine.py:242`).
- Add `likes(post_id)` index; make feed `ORDER BY` align with
  `(user_id, created_at DESC)` or add a matching index; prune `post_emb` entries
  not in the current candidate set (`engine.py:1395,1090`).

Checkpoint/telemetry/logging:
- Serialize `shared_memories` once per class, reference by key in per-agent
  blocks (schema-internal; restore already goes through our own reader).
- Drop `indent=2`; stream-write the checkpoint file object-by-object.
- Debug prompt logging: default to sampled (e.g. 1-in-K) above a size threshold;
  keep full logging opt-in (`openai.py:60`).
- `_read_jsonl` → generator (both restore and evaluators consume it as a stream)
  (`restore.py:311`, `default_evaluators.py:69`).

Add a **scaling benchmark harness** (new `tests/benchmarks/` or a script):
`sim.llm.disabled=true` + fixed agents at N ∈ {1k, 10k, 100k}, asserting
per-step wall-clock and RSS envelopes. This is the regression gate for every
phase below.

### Phase 1 — O(active) everywhere (unlocks 10k population, ~1k active head)

**STATUS: LANDED (2026-07-04).** All items below are implemented, with 15
regression tests (`tests/test_scalability_phase1.py`) and an end-to-end
sharded-checkpoint save→resume exercise through the real runner. Benchmark
(same harness/host as Phase 0, `sim.max_concurrent_actions=48`), with a fixed
~100-agent active head (`active_probability=0.001, min_active_agents=100`):

| N (population) | step_mean before-P1 pattern | step_mean with fixed active head |
|---|---|---|
| 1,000 | 1.27s (active ≈ 50% of N) | **0.45s** |
| 5,000 | 6.7s | **0.60s** |
| 10,000 | — | **0.69s** |

Per-step cost now tracks the ACTIVE set, not the population: a 10× population
increase moves step time by ~0.25s (participation sampling itself is O(N) with
a tiny constant — it must consider everyone — plus agent-construction startup,
which grows as expected: 2.0s → 8.2s from 1k → 10k). Notes on deviations:

- The recsys O(active) path (scoped delete + upsert) is verified by unit tests,
  not the benchmark — the dev host has no sklearn/sentence-transformers, and the
  benchmark scenario ships with recsys off.
- The **batched `get_timelines` bulk read was deliberately NOT added**: the
  serial-read bottleneck was the per-GM lock around `make_observation`, which the
  `read_only` flag removes (reads now run concurrently on thread-local WAL
  connections). A bulk-read API with no caller would be dead plumbing — exactly
  what item 2 of this phase removed; true batched observation builds arrive with
  the Phase 3 batch pipeline (and Postgres fan-out-on-write in Phase 2).
- The DB sidecar in the `sharded` layout is extracted by the save strategy from
  the base64 the backend hands over — `get_state`/`set_state` contracts are
  unchanged, but the blob still exists in memory during save; the zero-copy
  snapshot (backend writes the file directly) is Phase 2 work.
- GM `update` now receives the ACTIVE roster (components opt back into the
  population via `requires_full_roster = True`); the GM's own roster/context stay
  bound to the full roster from `initialize()`, rebuilt only when it changes.

- **Reorder `run_step`**: compute participation FIRST, then pass the *active*
  roster to GM `update` (`base_engines.py:229-237`). The `update(step, agents,
  context)` signature is unchanged — only who's in `agents` changes; a
  full-roster flag stays available for components that need it.
- **Activate the dead recsys plumbing**: `SocialRecommendationUpdateComponent`
  passes the active set as `active_user_ids` (already a parameter of
  `update_recommendations`); engines score only those users and **upsert**
  instead of DELETE-all + reinsert. This alone removes the 10k wall.
- **GM context caching**: `_refresh_context` rebuilds tuples only when the
  roster *changes* (version/epoch counter), not every step
  (`game_master.py:380`).
- **Probe sampling**: add `sample_fraction`/`sample_k` to
  `ProbeDeploymentPolicy` (one field + one selection branch; the include-filter
  seam already exists) (`deployment.py:171`).
- **Batched observation prefetch**: a backend-level
  `get_timelines(user_ids) -> {user: feed}` bulk read used by the observe
  component for the step's acting set (one query wave instead of N serialized
  reads), and narrow the engine lock to resolve-only for components that declare
  themselves read-only (an additive `read_only` class flag on observe
  components; SQLite WAL reads via thread-local connections are already safe
  concurrent with the writer).
- **Checkpoint save seam**: introduce `sim.checkpoint.save.{built_in|class_path}`
  mirroring the restore slot. Built-ins: `monolithic_json` (today's format,
  default), `sharded` (per-agent-block NDJSON shards + manifest — streamable,
  parallel-writable, delta-friendly). Backend DB snapshot becomes a **file
  reference** (copy the SQLite file next to the checkpoint) instead of an
  embedded base64 blob (`sqlite_state.py:24`) — the `get_state`/`set_state`
  contract is unchanged, the payload is a path + hash.
- **Telemetry O(active)**: `StepResult` keeps counts by default and gates name
  lists behind a verbosity flag; `EngineRecorder` default stops retaining
  per-episode name lists (`recorders.py:72`, `collector.py:114`).

### Phase 2 — Throughput & storage (unlocks ~100k population, ~10k active)

**STATUS: async slice LANDED (2026-07-07).** The first item below (async model
path + async turn executor) is implemented, selected by `sim.engine.executor:
threads|asyncio` (default `threads`, byte-identical behavior). Design points as
landed: sync `act`/`sample_text` remain the required contracts; `act_async` /
`sample_*_async` / turn-policy `run_async` are optional overrides whose base
defaults hop to helper threads via `asyncio.to_thread`, so mixed sync/async
rosters run in one step and a blocking sync agent can never stall the loop.
The executor swap happens entirely at the innermost drain seam
(`_drain_tasks_on_pool` → event-loop drain; the calling thread still blocks per
group), so every traversal/barrier/ordering semantic, per-GM lock/cap, failure
isolation, and the retry-telemetry envelope are shared code with the threaded
path — `worker_limit` becomes an asyncio in-flight semaphore. Turn-policy logic
exists once (a `_drive` generator; `run`/`run_async` are mechanical drivers);
model runtime context moved from `threading.local` to a ContextVar-backed
drop-in (task- AND thread-scoped). OpenAI providers get a lazy `AsyncOpenAI`
client with the same bounded retry/backoff (async sleep). Verified: 28
regression tests (`tests/test_scalability_phase2_async.py`), full suite green,
runner e2e parity threads↔asyncio (identical per-agent action multisets), and
an ad-hoc scale check — 500 async turns × 50 ms simulated latency drained in
0.16 s on 31 threads (~475 concurrently in flight). Adversarially reviewed
(3 agents); two real async-only bugs found and fixed with regression tests:
(1) `_ensure_loop_runner` lacked the double-checked lock the rest of the engine
uses, so concurrent multi-GM drivers could each build a separate event loop
(shared semaphores then awaited on two loops) — now guarded + eagerly created
single-threaded in `_run_action_phase`; (2) the lazy `AsyncOpenAI` client was
cached for the model's lifetime but bound to the first loop, stranding a model
reused across runs on a closed loop — now keyed by the running loop and rebuilt
on change. Not yet done from this item: `sample_structured_async` stays
thread-wrapped (no native async structured client path yet). The remaining
Phase 2 items below are NOT started.

- **Async model path (additive)**: `LanguageModel.sample_text_async(...)` etc.
  with a default implementation that wraps the sync method in a thread — every
  existing provider keeps working; `OpenAICompatibleLanguageModel` implements it
  natively on `httpx.AsyncClient`. An **async turn executor** drops in behind
  the same `execute_batches`/`execute_chain_groups`/`execute_staged_groups`
  public API (the `StepStrategy` contract is untouched — this is exactly the
  swap the SchedulingMixin split was designed for). Thousands of in-flight
  calls, single-digit threads. **[LANDED — see status above]**
- **`sample_choice` in one call**: constrained decoding / logit-bias /
  structured-output instead of up to 20 sequential escalations
  (`openai.py:253`).
- **Token-budgeted agent context**: `NativeAgent` gains a token budget +
  rolling summary for the observation window (pure `Agent`-seam change); enable
  provider prompt caching for the static persona/world prefix.
- **Postgres backend**: a new `SocialBackendApp` subclass that does NOT derive
  from `SqliteSocialEngineBase` — real connection pool, concurrent writers,
  proper indexes/FTS, fan-out-on-write timelines. The audit confirmed the seam
  (actions, `get_timeline_mode`, `setup_social_state`, recsys hooks,
  checkpoint state) fully supports this. SQLite remains the zero-setup default.
- **Multi-process step execution (optional, same seam)**: a process-pool turn
  executor for GIL-bound Python work, still behind the scheduling API.

### Phase 3 — Mass-active architecture (target: 1M+ ACTIVE agents per step)

The maintainer's target is 1M+ *active LLM-driven* agents per step, so this
phase is not a "small head over a dormant population" design — it is an
end-to-end **batch pipeline**: observe-batch → act-batch → resolve-batch, with
no per-turn serialization anywhere. Each piece still lives behind an existing
seam.

- **Batched turn pipeline behind the scheduling API**: a new executor behind
  `execute_batches`/`execute_chain_groups`/`execute_staged_groups` that
  processes a batch as three waves instead of N independent thunks:
  (1) one bulk observation build for the whole acting set (Phase 1's
  `get_timelines` bulk read, backed by fan-out-on-write in Postgres);
  (2) all `act()` calls in flight simultaneously via the async model path
  (Phase 2) against high-throughput inference endpoints — tens of thousands of
  in-flight requests on a handful of threads;
  (3) a **batched resolve**: parse concurrently (pure CPU, process pool), then
  apply actions to the backend in bulk transactions per wave instead of
  one-at-a-time under the per-GM lock. The resolve component seam grows an
  additive `resolve_actions(batch)` (default = loop over today's
  `resolve_action`), so existing components keep working and the lock section
  becomes one-per-wave, not two-per-turn.
- **Roster handle (duck-typed, minimally invasive)**: introduce `AgentRoster` —
  a Sequence-compatible object (iter/len/index/lookup-by-name) backed by an
  agent **store** (SQLite/parquet of `get_state()` blobs + construction specs)
  with an LRU of hydrated `Agent` objects. Because every seam types the roster
  as `list[Any]`/`Sequence`, it flows through `StepStrategy`, `LoopStrategy`,
  participation, and GM `update` without signature changes. Even with 1M
  *active* agents, hydration matters: agent state lives in the store between
  turns, and the working set held as live Python objects is bounded by the
  in-flight window, not the population. Per-active-agent context must be
  token-budgeted (Phase 2) — 1M × 1 MB resident histories do not fit; 1M ×
  a few KB of hydrated state does.
- **Single-machine ceiling, distribution-ready shape**: on one box, 1M active
  is bounded by inference throughput, not the framework, once the batch
  pipeline exists. The same pipeline shards cleanly later — roster partitions
  across worker processes/hosts against shared Postgres, launched via the study
  layer's existing `command_override` (srun/sbatch); GM/component state is
  already externalized through `get_state`. No rework is required to add
  hosts — only a shard-assignment step strategy.
- **Event-sourced restore with compaction**: periodic authoritative snapshots
  (per-GM, already supported via `checkpoint_authoritative_gm_names`) + replay
  only the tail since the last snapshot, streaming (Phase 0's generator).
  At 1M active/step the action log grows ~10⁶ rows/step — snapshot cadence,
  not replay, must carry restore.
- **Metrics**: replace process-global `SimMetricsCollector` singleton usage
  with the `EngineRecorder` seam writing per-shard aggregates (counts and
  histograms, never per-agent name lists), merged post-hoc.

---

## 4. What we explicitly do NOT change

- The component-slotted GM, the policy-slot config grammar
  (`{built_in|class_path, params}`), the `Agent` ABC, the `Router` Protocol,
  the backend action catalog, the study schema. Every phase is implementations
  behind existing seams plus a small number of **additive** contracts
  (checkpoint save slot, async model methods, roster handle, `read_only`
  component flag, probe `sample_fraction`).
- Replay stability guarantees: all sampling (participation, routing, probe
  sampling, ambient tail) stays seed-derived per `(seed, step, agent)`.

## 5. Resolved decisions (maintainer, 2026-07-04)

1. **1M semantics** — design infrastructure for **1M+ active LLM-driven agents
   per step** (best case); 10k–100k active is the second-best case. Sampling
   remains a knob, not an architectural assumption.
2. **Deployment envelope** — **single machine first**; Phase 3 keeps a
   distribution-ready shape but nothing assumes a cluster.
3. **Storage** — **Postgres backend approved** behind the `SocialBackendApp`
   seam; SQLite stays the zero-setup default.
4. **Near-term target** — **10k agents must work well first** → land Phase 0
   then Phase 1 (both SQLite-compatible), then Phase 2/3.

## 6. Verification strategy

- The Phase 0 benchmark harness (LLM-disabled, N ∈ {1k, 10k, 100k}) runs in CI
  as a wall-clock/RSS regression gate per phase.
- Each phase lands as its own reviewed change-set with the full existing suite
  (759 tests) green — the seams' behavior contracts are the regression net.
- Replay-stability tests (same seed → same actions) extend to participation
  sampling, probe sampling, and the roster handle.
