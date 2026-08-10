# Configuration Reference

Complete reference for all YAML configuration options.

For a generated key-by-key table of every packaged default (kept in sync with
the shipped YAML by a drift test in CI), see
[config_reference.md](config_reference.md).

## Config Groups

Configuration is split across named groups, each with a base preset in
`src/silisocs/conf/`:

| Group | Base file | Controls |
|---|---|---|
| *(root)* | `experiment.yaml` | Hydra output paths, experiment label |
| `world` | `world/default.yaml` | Run parameters, setting, event, data |
| `agents` | `agents/default.yaml` | Persona pipeline, shared memories, initial observations |
| `sim` | `sim/base.yaml` | LLM (model, API, temperature), engine, tool-calling, checkpoint |
| `env` | `env/twitter_like.yaml` | Backend construction and GM component wiring |
| `eval` | `eval/base.yaml` | Probes and evaluation timing |

---

## Slots (`built_in` / `class_path` / `params`) {#slots}

Every pluggable piece of the framework — engines, policies, GM components,
backends, checkpoint strategies, memory, routers, intervention handlers — is
configured with the same three-key **slot**:

```yaml
some:
  slot:
    built_in: shipped_name   # pick one of the shipped implementations
    class_path: null         # ...or a fully-qualified path to your own class
    params: {}               # constructor arguments for whichever was chosen
```

The rules below hold for *every* slot in this document, so the per-knob sections
only list that slot's built-ins, its base type, and its params:

- **`class_path` wins** when both keys are set. It is imported at build time and
  must satisfy that slot's contract (usually a subclass of the slot's base
  class; routers are plain callables instead).
- **`params` are strict constructor arguments.** An unknown key fails before the
  simulation starts rather than silently running with defaults — a typo'd param
  is a config error, not a surprise at step 40. The exception is a target class
  that accepts `**kwargs`. Values the runtime injects itself (`model`,
  `agent_names`, `sim_roles`, app handles) are filtered out when the constructor
  does not accept them, so you never declare them in `params`.
- **`params: null` clears** a params block a merged config group supplied. Hydra
  merging cannot remove sibling keys, so `params: {}` leaves the group's params
  in place; `params: null` is the way to reset them.
- Slots override from the CLI like any other key:
  `sim.engine.turn_policy.built_in=open_ended sim.engine.turn_policy.params.count=3`.
- A slot is where you extend the framework without editing it. Writing a custom
  implementation and pointing `class_path` at it is the supported path for all of
  them; see [Simulation Extensibility API](simulation_extensibility_api.md).

The slots, and where each is documented:

| Slot | Base type | Reference |
|---|---|---|
| `env.gm.backend` (`type` \| `class_path`) | `BackendApp` | [Backends](#backends), [Environment Backends](backends.md) |
| `env.gm.components.{initialize,next_acting,observe,resolve,update,action_prompt}` | `Component` | [GM Components](#gm-components) |
| `env.gm_orchestration.gms[*].components.*` | `Component` | [Multi-GM Orchestration](#advanced-multi-gm-orchestration) |
| `env.gm_orchestration.flow_bindings.flow_to_gms.<flow>[*].branch.router` | callable | [Branch routing](#advanced-multi-gm-orchestration) |
| `env.gm_orchestration.gms[*].restore` | `CheckpointRestoreStrategy` | [Multi-GM layout](#multi-gm-layout) |
| `sim.memory` | `MemoryPolicy` | [Agent Memory](#agent-memory-simmemory) |
| `sim.initialization.{agents,game_masters,simulation}` | initializer | [Memory Initialization](memory_initialization.md), [Seed Posts](#seed-posts) |
| `sim.engine.loop` | `LoopStrategy` | [Interactive Stepping](#interactive-stepping) |
| `sim.engine.step` | `StepStrategy` | [Engine and Runtime](#engine-and-runtime) |
| `sim.engine.turn_policy` (+ `flow_turn_policies`, `gm_turn_policies`) | `TurnPolicy` | [Engine Turn Policies](#engine-turn-policies) |
| `sim.engine.participation` | `ParticipationPolicy` | [Social Setup and Participation](#social-setup-and-participation) |
| `sim.engine.control` | controller | [Interactive Stepping](#interactive-stepping) |
| `sim.checkpoint.save` | `CheckpointSaveStrategy` | [Engine and Runtime](#engine-and-runtime) |
| `sim.checkpoint.restore` | `CheckpointRestoreStrategy` | [Checkpoint Restore](#checkpoint-restore) |
| `eval.probes.schedule` | `ProbeSchedulePolicy` | [Probe schedule](#probe-schedule) |
| `interventions[*]` with `kind: custom` | `InterventionHandler` | [Mid-Run Interventions](#mid-run-interventions) |
| `agents.persona_pipeline.classes.<class>` (`class_path` + `params`) | `Agent` | [Persona Pipeline](#persona-pipeline), [Building Agents](building_agents.md) |
| `agents.builder` (custom agent builder) | `AgentBuilder` | [Building Agents](building_agents.md) |

Two near-slots differ in shape: `sim.engine.class_path` / `sim.engine.params`
replaces the whole engine and has no `built_in` (there is one built-in engine),
and `sim.engine.control` carries sibling scalars (`start_paused`,
`control_file`, `poll_interval`) alongside its `built_in` instead of putting them
in `params` — it is otherwise a normal slot: `sim.engine.control.class_path` +
`params` build a custom controller, constructed with the run's `StepGate` plus
strictly-checked `params` (an unknown key fails naming `sim.engine.control.params`).
`env.gm.backend` selects a shipped backend with `type` rather than `built_in`.

---

## Top-Level Config (`experiment.yaml`)

```yaml
defaults:
  - world: default
  - agents: default
  - sim: base
  - env: twitter_like
  - eval: base
  - _self_

hydra:
  job:
    name: ${scenario_name}_${now:%Y-%m-%d_%H-%M-%S}
  run:
    dir: outputs/${scenario_name}/${jobname_format}
  output_subdir: configs/${jobname_format}

experiment_name: independent
```

Override from the CLI:

```sh
uv run silisocs env=reddit_like num_agents=500
```

### Plugins

Every registry in the framework (`register_llm_provider`,
`register_replay_mapper`, `register_event_semantics`,
`register_health_counter`, analysis `register_panel`, ...) needs your module
imported before the run starts. Two mechanisms, no core edits:

```yaml
# Top-level config: load-bearing, fails the run if an import fails.
plugins:
  - mypkg.silisocs_setup
```

Installed packages may instead declare a `silisocs.plugins` entry point in
their own packaging metadata; those modules are imported automatically on
every run (a broken one warns and is skipped, so one bad installed package
cannot take down unrelated runs). The runner also puts the project root on
`sys.path`, so scenario-local modules (`scenarios/<name>/builders.py` →
`scenarios.<name>.builders`) are importable without any of this.

---

## Run Parameters (`world/default.yaml`)

Run parameters live in the `world` config group (placed at config root via
`@package _global_`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `num_agents` | `10` | Number of agents to create (any value works; personas recycle past the bundled 100, see below) |
| `num_steps` | `5` | Simulation steps to run |
| `run_name` | `run1` | Run identifier (used in output path) |
| `seed` | `1` | Random seed |
| `scenario_name` | `default` | Scenario identifier (used in output path) |
| `jobname_format` | *(template)* | Output directory name template |
| `experiment_name` | `independent` | Experiment label used in `jobname_format` |
| `output_dir` | `""` | Explicit run output directory. Empty (recommended) means "use Hydra's per-run path"; a non-empty value overrides it — see [Output Configuration](#output-configuration) |

Every scenario's `world/default.yaml` must declare all of these, including
`output_dir`: a scenario world group **replaces** the base one rather than
merging with it (Hydra searchpath shadowing), so an omitted key is missing, not
inherited.

---

## Sim Parameters (`sim/base.yaml`)

### LLM

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sim.llm.provider` | `openai` | Model provider: `openai`, `openai_compatible`, `scripted`, `disabled`, a built-in preset (see below), a registered name, or a class path |
| `sim.llm.name` | `gpt-4o-mini` | LLM model name (passed to the model factory) |
| `sim.llm.api_base` | `null` | Required base URL when `provider: openai_compatible`; also overrides the base URL of a built-in preset |
| `sim.llm.api_key` | `null` | API key (or set via the provider's environment variable) |
| `sim.llm.temperature` | `0.5` | Sampling temperature |
| `sim.llm.disabled` | `false` | Use a no-op model (no API calls). **Incompatible with tool-call parsing** — see the note below; for an offline smoke run use `sim.llm.provider=scripted` instead |
| `sim.llm.extra_kwargs` | `{}` | Provider request kwargs such as OpenAI-compatible `extra_body` settings |
| `sim.llm.pricing` | `null` | Optional `{input_per_1m, output_per_1m}` USD rate for cost reporting |

**The no-op model cannot be combined with tool-call parsing.**
`sim.llm.disabled: true` (equivalently `sim.llm.provider: disabled`) builds a
no-op model whose tool-call sampling returns an **empty** list, which the agent
layer rejects — so every agent turn would fail and the run would commit no
actions. Because the packaged default is `sim.tool_calling.mode: single`,
`sim.llm.disabled=true` **on its own now fails at build**, before any agent is
constructed:

```text
Config error: sim.llm.disabled=true cannot be combined with sim.tool_calling.mode='single'.
```

The error names the two ways out:

- **`sim.llm.provider=scripted`** (and drop `sim.llm.disabled`) — a deterministic
  offline model that *does* answer tool calls. This is the smoke-run option, and
  what every "dry run with no LLM calls" example in this page uses.
- **`sim.tool_calling.mode=none`** — keep the no-op model; agents then emit plain
  text read by the `parsed_action` / `generic_action` resolve components.

The check reads the **top-level** `sim.tool_calling.mode` only: a per-GM
`env.gm.tool_calling` / `env.gm_orchestration.gms.<name>.tool_calling` override
does not exempt a run from it.

**Token usage & cost.** OpenAI-compatible providers record `response.usage`
per call, split by phase — `probe` (evaluation spend; the loop brackets probe
deployment the same way scheduling brackets action turns), `action` (the
simulation itself), and `other` (initialization and out-of-band calls). Per-model
token totals ride into `sim_metrics.json` under
`episode_metrics[].retry_telemetry` (per-episode cumulative) and a run-level
`meta.llm_usage` summary with `per_model`, `totals`, and a `by_phase` split, so
instrumentation cost is separable from experiment cost. Providers that
omit `usage` are counted under `calls_without_usage` rather than guessed. Setting
`sim.llm.pricing` adds `estimated_cost_usd` per model, per phase, and overall — a
single rate applied to every model, so for a mixed-model run with differing real
prices read the per-model token counts and price them yourself. `pricing` is telemetry-only:
it never affects model construction or the effective-config dedup.

**Built-in provider presets.** Common providers that expose an OpenAI-compatible
API are available as named presets. Set `sim.llm.provider` to the name and supply
the key via the listed environment variable (or `sim.llm.api_key`); `sim.llm.name`
selects the model.

| Preset | Endpoint | API key env var |
|--------|----------|-----------------|
| `anthropic` | `https://api.anthropic.com/v1/` | `ANTHROPIC_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `together` | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `mistral` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| `fireworks` | `https://api.fireworks.ai/inference/v1` | `FIREWORKS_API_KEY` |
| `xai` | `https://api.x.ai/v1` | `XAI_API_KEY` |
| `ollama` | `http://localhost:11434/v1` | none (local) |

These presets route through the OpenAI-compatible client, so they inherit the same
retry, backoff, and telemetry support. Anthropic and Gemini are reached through
their OpenAI-compatible endpoints. For a provider not listed here, use
`provider: openai_compatible` with an explicit `sim.llm.api_base`, or register a
custom provider (see [Building Agents](building_agents.md)).

**Where model config is strict, and where it is open.** Two different surfaces
live in `sim.llm`, and they have opposite rules:

- **The config surface** — `provider`, `name`, `api_base`, `api_key`,
  `temperature`, `disabled`, `extra_kwargs` (plus telemetry-only `pricing`) — is
  **closed**. An unknown key under `sim.llm` (or under a per-class `model` block)
  fails validation naming the key. Building the model is strict too: if
  `api_base`, `api_key`, or `extra_kwargs` is *set* (their defaults are unset) and
  the selected provider's constructor cannot accept it, the run fails at build
  naming the provider and the offending field(s) rather than silently sending
  requests to the wrong endpoint. Only relevant to custom providers — every
  built-in and preset accepts all of them. `model_name`, `log_file`, `debug`, and
  `temperature` are framework-supplied with defaults, so a fixed-model or custom
  provider may ignore them.
- **`extra_kwargs` contents** are **open** for API-backed providers (`openai`,
  `openai_compatible`, every preset): the mapping is merged into the chat-completion
  request body, so its keys are the provider API's vocabulary, not the framework's.
  A key the API does not know surfaces as that API's error on the first call.
  The exception is `provider: scripted`, which has no request body — there
  `extra_kwargs` are the scripted model's constructor params (`text_response`,
  `tool_calls`, `behavior_class_path`, …) and a typo fails at build.

### Engine and Runtime

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sim.max_concurrent_actions` | `1000` | Max parallel LLM calls per step |
| `sim.engine.loop.built_in` | `fixed_steps` | Episode-loop strategy. `fixed_steps` (the only built-in) runs steps from `start_step` up to `num_steps`, firing probe anchors, interventions, and checkpoints at each boundary; `class_path` swaps in a custom `LoopStrategy` (see [Interactive Stepping](#interactive-stepping)) |
| `sim.action_mode` | `custom` | Prompt style: `custom` (world prompt) or `generic` (backend-generated) |
| `sim.tool_calling.mode` | `single` | Tool dispatch mode: `none`, `single`, or `multi` |
| `sim.prompt_additions.action_count_guidance` | `true` | Add `[ActNum]` marker and action count guidance to prompt |
| `sim.checkpoint.every_n_steps` | `null` | Save checkpoints every N steps when set |
| `sim.checkpoint.explicit_steps` | `[]` | Additional explicit checkpoint steps |
| `sim.checkpoint.source_run` | `null` | Previous output directory to restore from (explicit resume) |
| `sim.checkpoint.auto_resume` | `true` | Resume from this run's own output directory if it already contains checkpoints; ignored when `source_run` is set |
| `sim.checkpoint.save.built_in` | `monolithic_json` | On-disk checkpoint layout: `monolithic_json` (one JSON per step, the long-standing format) or `sharded` (a `step_N_checkpoint.json` manifest + NDJSON object shards + raw SQLite sidecar `.db` files with sha256; `params.objects_per_shard`, default 500). Restore reads both layouts transparently; `class_path` accepts a custom `CheckpointSaveStrategy` |
| `sim.checkpoint.restore.built_in` | `social_action_event_replay` | Checkpoint restore strategy when `source_run` is set |
| `sim.telemetry.record_active_agent_names` | `false` | Retain each episode's active-agent *name list* in `sim_metrics.json` (kept in memory for the whole run — O(active × steps)). Counts (`active_agents`) are always recorded |
| `sim.engine.step.built_in` | `base` | Engine step policy: `base`, `sequential`, `flow`, `multi_gm`, `multi_gm_serial`, or `multi_gm_staged`. The three `multi_gm*` strategies select the flow-chain traversal mode: `multi_gm` (concurrent, default — flows advance as independent pipelines, serializing only when two flows touch the same GM), `multi_gm_serial` (legacy row-major — each flow runs its full GM chain to completion before the next), `multi_gm_staged` (column-major with a global per-stage barrier — all flows advance one stage at a time) |
| `sim.engine.step.params.gm_turn_policies` | `{}` | Per-GM turn policy overrides keyed by GM name, each value a turn-policy [slot](#slots); applies under any step mode and is resolved per batch by GM name |
| `sim.engine.step.params.gm_concurrency_caps` | `{}` | Per-GM concurrency caps (`{gm_name: int}`); caps how many of that GM's agent turns run at once via a per-GM semaphore. Effective per-GM limit = `min(cap, sim.max_concurrent_actions)`; empty map = the global cap governs every GM. Applies under any step mode |
| `sim.engine.turn_policy.built_in` | `single_action` | Global turn policy — how many actions an agent takes per step: `single_action`, `fixed_count`, or `open_ended` |
| `sim.engine.executor` | `threads` | Turn executor: `threads` (one pool worker per in-flight turn) or `asyncio` (turns run as coroutines on one background event loop — thousands of LLM calls in flight on a handful of threads). Agents/models that provide `act_async` / `sample_*_async` (the shipped `NativeAgent`, `FixedAgent`, and OpenAI-compatible providers do) run loop-native; sync-only custom agents, models, and turn policies automatically run on helper threads, and both kinds mix freely in one step. `sim.max_concurrent_actions` keeps its meaning — max in-flight turns — under either executor; scheduling semantics (flow chains, barriers, per-GM caps and locks) are identical |
| `sim.engine.participation.built_in` | `all` (base.yaml) | Sim-level roster filter applied before scheduling, every GM `next_acting`, **and per-step GM `update`s** (so per-step backend work like the recsys refresh is O(active), not O(population); update components that need the full population declare `requires_full_roster = True`): `all` (pass-through), `activity_probability`, `activity_markov`, or a `class_path`. Effective acting = participation ∩ `next_acting`. **Default is `all`** (everyone acts every step — deterministic, matches the pre-participation behavior, and keeps bare `env=` preset runs whose roles aren't `user` from silently dropping to random participation). Activity gating is **opt-in**: a social scenario sets `built_in: activity_probability` (plus per-role `activity_transition_rates`) in its own sim config. Both `sim/base.yaml` and the in-code `build_engine` fallback default to `all` |
| `sim.engine.class_path` | `null` | Whole-engine swap seam: a fully-qualified path to a custom `RuntimeEngine` subclass. When set, the engine is that class, built with the standard kwargs (`config`, `loop_strategy`, `step_strategy`, `turn_policy`, `gm_turn_policies`, `gm_concurrency_caps`, `participation`, `seed`, `executor`); unset uses the built-in `RuntimeEngine`. Prefer the policy seams (loop/step/turn_policy/participation `class_path`) unless you must replace the engine lifecycle itself |
| `sim.engine.control.built_in` | `none` | Interactive run control — pause / step one episode / resume a running simulation. `none` (default) attaches no gate and the loop runs every episode uninterrupted (zero cost). `stdin` reads `n`/next `[k]`, `c`/continue, `p`/pause, `s`/stop from the terminal between episodes. `control_file` obeys a JSON file another process (Studio) writes: `{"target": <int\|null>, "stopped": <bool>}` — `target` is the first episode index to hold before (`null` = run freely). Control acts at episode boundaries; a paused loop still checkpoints per step, so it stays resume-stable if the process exits. See §"Interactive Stepping" below |
| `sim.engine.control.start_paused` | `false` | Hold before episode 0 until the controller advances the run. Studio's interactive launch defaults this to `true` |
| `sim.engine.control.control_file` | `null` | Path the `control_file` controller polls (default `<output>/run.control`). Studio passes an explicit path both it and the runner agree on. The file is a live command channel owned by the running process: any pre-existing file at that path is discarded at launch (a stale `stopped` from a prior run cannot end a fresh one) — pre-seed a held start with `start_paused` instead |
| `sim.engine.control.poll_interval` | `0.3` | `control_file` poll cadence in seconds |
| `sim.roleplaying_instructions` | *(template)* | System prompt injected into every agent. Use `{name}` placeholder. |

> **Migration — `multi_gm` is now concurrent by default.** `sim.engine.step.built_in:
> multi_gm` runs flows as independent pipelines that advance concurrently (serializing
> only on shared-GM overlap). A `main`-era config that expected the old serial
> row-major traversal (each flow runs its full GM chain to completion before the next
> starts) must set `sim.engine.step.built_in: multi_gm_serial`. The removed
> `sim.engine.step.params.chain_execution` knob now raises a `ValueError` with this hint.

### Interactive Stepping

`sim.engine.control` lets you drive a running simulation one episode at a time —
pause it, advance a single episode, resume free-running, or stop it — from the
command line or from Studio. It adds one thread-safe gate the episode loop
consults at each boundary; the default (`none`) attaches no gate, so ordinary
runs are unaffected.

**Command line** (`stdin` controller):

```bash
silisocs scenario=my_world num_steps=50 \
  sim.engine.control.built_in=stdin \
  sim.engine.control.start_paused=true
```

Between episodes, type: `n` / `next [k]` (advance one or `k` episodes), `c` /
`continue` (run freely to `num_steps`), `p` / `pause` (hold at the next
boundary), `s` / `stop` (end the run cleanly). Closing stdin (e.g. piping) runs
freely, so a non-interactive invocation never hangs.

**Studio.** An interactive launch shows Step / Play / Pause / End-run controls on
the live view; they write the run's `control_file` and the runner obeys it at
each boundary. See [studio.md](studio.md).

Control acts at **episode boundaries** (an in-flight episode always finishes),
and every paused loop still checkpoints per step, so a paused or stopped run is
resume-stable if the process exits — resume it later with the normal checkpoint
flow (`sim.checkpoint.auto_resume`).

**Custom loop strategies.** The episode loop (`FixedStepsLoopStrategy`) is
replaceable via `sim.engine.loop.class_path`, so interactive control is exposed
as a helper rather than welded into the built-in loop — the same arrangement as
probe timing (`loops.run_probe_phase`). A custom `LoopStrategy` inherits
play/pause/step/stop by calling it at its own episode boundary:

```python
from silisocs.simulation_engines.policies.loops import await_step_permission

while step < max_steps:
    if not await_step_permission(engine, step):
        break  # a stop was requested
    ...
```

It returns `True` immediately when no gate is attached, so a strategy that calls
it costs nothing on a non-interactive run.

### Per-GM action_mode and tool_calling overrides

`sim.action_mode` and `sim.tool_calling.mode` set the global defaults. A single
default GM (`env.gm`) and each orchestrated GM (`env.gm_orchestration.gms[*]`)
may additionally set optional per-GM overrides:

```yaml
env:
  gm_orchestration:
    gms:
      - name: social_gm
        action_mode: generic
        tool_calling: single        # scalar: none | single | multi
        # ... backend + components ...
```

Each unset key falls back to the global `sim.action_mode` / `sim.tool_calling.mode`
(today's behavior). The per-GM override is a **scalar** `tool_calling: <mode>`
(a sibling to the scalar `action_mode`). The retired `tool_calling_mode` key and the
`tool_calling: {mode: ...}` block form each raise a migration error pointing at the
scalar spelling — the block form survives only at the global `sim.tool_calling.mode`.

Resolve compatibility is validated **per GM** against each GM's *effective*
tool-calling mode: a GM whose effective mode is `single`/`multi` must pair its
`components.resolve.built_in` with `tool_calling`, and a GM whose effective mode is
`none` must not use the `tool_calling` resolve component.

---

## Running and Creating Scenarios

Scenarios live in `scenarios/{name}/conf/` and override package defaults.
The run command:

```bash
uv run silisocs --config-path scenarios/misinformation/conf
```

### Directory Structure

```
scenarios/
└── my_world/
    └── conf/
        ├── world/
        │   └── default.yaml             # Run parameters + setting/event/data
        ├── agents/
        │   ├── default.yaml             # Persona pipeline, shared memories
        │   └── thin.yaml                # Lightweight variant (optional)
        ├── agents.yaml                  # Partial persona-pipeline overrides (optional)
        ├── env.yaml                     # Platform/GM overrides (optional)
        ├── eval.yaml                    # Probe config overrides (optional)
        └── sim.yaml                     # LLM + engine overrides (optional)
```

### How Config Overrides Work

Two mechanisms layer on top of the package defaults:

**Layer 1, Hydra SearchPath Plugin**: a registered `SearchPathPlugin` prepends
the scenario conf dir to Hydra's search path before composition. This gives
`world/default.yaml` and `agents/default.yaml` from the scenario conf dir
higher priority than the package defaults, so they replace the package
`world/default.yaml` and `agents/default.yaml` entirely.

**Layer 2, Manual merge** (runs inside `main()` after Hydra composes,
`runtime/configuration/external.py`): handles partial-override files that don't
replace their group wholesale. Two families, merged in this order:

- **Four flat files**, merged into the group named by the filename, in the fixed
  order `agents.yaml` → `env.yaml` → `eval.yaml` → `sim.yaml`. Each must contain
  a YAML mapping (anything else fails naming the file), and each carries **no**
  `@package` directive — its top-level keys are placed under the group named by
  the file. So `sim.yaml`'s `engine:` key becomes `sim.engine`, and `eval.yaml`'s
  `probes:` key becomes `eval.probes`.
- **World-variant overlay files**, merged only when the run passes a
  `world=<variant>` override: `env/<variant>.yaml`, `sim/<variant>.yaml`, and
  `eval/<variant>.yaml` (in that order) are read from the scenario conf dir and
  merged into the `env` / `sim` / `eval` groups. This lets one scenario directory
  carry several paired worlds under matching filenames — running
  `world=public_goods_game` also pulls in
  `scenarios/public_goods_game/conf/env/public_goods_game.yaml`. Missing variant
  files are simply skipped.

  These are the same files Hydra would treat as **group options**, and the two
  routes differ: `env=<variant>` *replaces* the `env` group, while the
  world-variant overlay only *merges* on top of whatever `env` group is already
  composed (`env/twitter_like.yaml` by default). Merging cannot remove the base
  group's sibling keys, so a variant that swaps to a different backend should also
  be selected as a group — which is why the non-social scenarios document
  `world=X agents=X env=X` together.

**Priority order** (highest → lowest):

1. CLI overrides (`num_steps=1 sim.llm.provider=scripted`)
2. Scenario flat files merged in Layer 2 (`env.yaml`, `sim.yaml`, …)
3. Scenario `world/default.yaml` and `agents/default.yaml` (via plugin searchpath)
4. Package defaults in `src/silisocs/conf/`

CLI overrides are re-applied after the Layer-2 merge, so a key they set wins over
the scenario's flat files.

#### When a CLI override needs `++` {#cli-override-plus-plus}

A CLI override is **struct-validated by Hydra at compose time**, against the
config as it exists at the end of Layer 1 — the packaged defaults plus the
scenario's `world/` and `agents/` *group* files, but **not** its flat `sim.yaml` /
`env.yaml` / `eval.yaml` / `agents.yaml`, which are merged afterwards in Layer 2.
So a plain `key=value` override can only target a key that already exists in the
packaged config (or in a scenario group file). Most slot `params:` blocks in the
packaged config are empty (`params: {}`), which means **every key inside them is
"new"** as far as compose-time validation is concerned — even when the scenario
you are running fills that block in from a flat file.

Concretely, `src/silisocs/conf/sim/base.yaml` ships
`sim.engine.turn_policy.params: {}`, while `scenarios/misinformation/conf/sim.yaml`
sets `max_actions: 5` under it. Overriding it from the CLI fails:

```bash
# ✗ fails at compose time
uv run silisocs --config-path scenarios/misinformation/conf \
    sim.llm.provider=scripted num_steps=0 \
    sim.engine.turn_policy.params.max_actions=3
```

```text
Could not override 'sim.engine.turn_policy.params.max_actions'.
To append to your config use +sim.engine.turn_policy.params.max_actions=3
Key 'max_actions' is not in struct
    full_key: sim.engine.turn_policy.params.max_actions
    object_type=dict
```

Prefix with `++` (force-add-or-override) and it works — the value is re-applied
after Layer 2, so it lands on top of the scenario's `max_actions: 5`:

```bash
# ✓
uv run silisocs --config-path scenarios/misinformation/conf \
    sim.llm.provider=scripted num_steps=0 \
    ++sim.engine.turn_policy.params.max_actions=3
```

The rule is "does the **composed** config already declare this exact key?", not
"does my scenario's flat file set it?". Keys the packaged defaults declare —
`num_steps`, `sim.llm.provider`, `sim.engine.turn_policy.built_in`,
`env.gm.backend.type`, `env.gm.components.observe.params.timeline_posts`
(declared in `env/twitter_like.yaml`), … — take a plain `=`. Keys inside a
`params: {}` block the packaged defaults leave empty need `++`:
`sim.engine.{loop,step,turn_policy,participation}.params.*`,
`sim.memory.params.*`, `sim.checkpoint.{save,restore}.params.*`,
`eval.probes.schedule.params.*`, `env.gm.components.resolve.params.*`, and any
backend/component param a non-default `env=` preset introduces. `++` is
force-add-or-override and is always safe, so when in doubt use it; plain `+` is
add-only and errors when the key already exists after composition.

**A scenario's config-group files REPLACE their group; its flat files are
MERGED.** A scenario `world/default.yaml` shadows the base `world` group entirely
rather than layering onto it, so it must re-declare every universal run param the
base provided — `jobname_format`, `scenario_name`, `run_name`, `output_dir`,
`num_agents`, `num_steps`, `seed` — under a `# @package _global_` directive. The
same whole-group replacement applies to `agents/default.yaml` and to any `env/`,
`sim/`, or `eval/` group file. Flat `agents.yaml`/`env.yaml`/`sim.yaml`/`eval.yaml`
merge into their groups and need no re-declaration.

Only `world` files *need* a `@package` line, because they are the ones that
deviate from the default: `# @package _global_` puts their keys at the config
root instead of under a `world` key. Every other group file lands in the package
Hydra derives from its group directory, so the directive is optional there —
the packaged `agents/default.yaml`, `sim/base.yaml`, and `eval/base.yaml` write
it explicitly for readability, while **none of the packaged `env/*.yaml` files
carry one at all** (nor do the scenario `env/` group files); they rely on the
implicit `env` package, with the same net result.

Missing a param that `hydra.run.dir` interpolates (typically `jobname_format`)
fails before the run starts; the error names the key and this rule.

### Running a Scenario

```bash
# Run with scenario defaults
uv run silisocs --config-path scenarios/election/conf

# Override specific parameters
uv run silisocs --config-path scenarios/election/conf \
    num_agents=500 num_steps=100

# Use alternate agents variant
uv run silisocs --config-path scenarios/ai_conference/conf \
    agents=thin

# Dry-run with no LLM calls (for testing)
uv run silisocs --config-path scenarios/misinformation/conf \
    num_steps=1 sim.llm.provider=scripted

# View merged config before running
uv run silisocs --config-path scenarios/election/conf --cfg job
```

### Creating a New Scenario

**Option 1: Via Studio**

1. Start Studio: `silisocs-studio --output-root outputs --port 8765`
2. Open Scenarios and create or select a scenario
3. Edit the declarative form or its bidirectional YAML mirror
4. Run Preflight, then Save and Launch
5. Studio writes the source files under `scenarios/{name}/conf/`

**Option 2: Manual**

The step-by-step walkthrough — a worked world file, a worked agents file, and
the common patterns (multiple classes, broadcast accounts, scripted agents,
switching backends) — lives in the [Scenario Guide](scenario_guide.md). It is
the canonical version; this page is the key reference behind it.

The minimum is two files:

```bash
mkdir -p scenarios/my_world/conf/world scenarios/my_world/conf/agents
```

```yaml
# scenarios/my_world/conf/world/default.yaml   (run params + narrative)
# @package _global_
scenario_name: my_world
jobname_format: "N${num_agents}_T${num_steps}_${run_name}"
num_agents: 3
num_steps: 20
seed: 42
run_name: my_world
output_dir: ""
setting: { name: My Setting, background: [Background detail 1] }
event: { name: My Event, context: "Event description used in agent memories." }
data: {}
```

```yaml
# scenarios/my_world/conf/agents/default.yaml   (who is in the world)
# @package agents
persona_pipeline:
  classes:
    user:
      count: ${num_agents}
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: user
      data:
        source: inline
        records:
          - { name: Alex, persona: Alex follows local policy and posts updates. }
          - { name: Blair, persona: Blair follows tech news and debates concisely. }
          - { name: Casey, persona: Casey moderates and keeps threads on topic. }
      field_map: { name: name, context: persona }
```

`sim.yaml`, `env.yaml`, and `eval.yaml` are optional partial overrides merged on
top of the defaults; the sections below document every key they may set.

Every final agent spec must have a unique `name`. For most persona-pipeline
sources, map it with `field_map.name`. The default builder can derive names for
the known `nvidia/Nemotron-Personas-USA` persona dataset, and custom builders can
derive names as part of their own config-to-spec logic. Runtime construction
still rejects unnamed or duplicate specs before the simulation starts. Agent
names are the runtime identities used by GMs, backends, flows, probes, logs, and
checkpoints.

### Output Structure

With `output_dir: ""` (the recommended value), simulation output goes to
`outputs/{scenario_name}/{jobname_format}/{scenario_name}_{timestamp}/`:

```
outputs/
└── default/
    └── N10_T5_independent_run1/           # hydra.run.dir  ({jobname_format})
        ├── default_2026-05-01_12-30-00/   # hydra.job.name — the run directory
        │   ├── run_manifest.json          # Self-describing run index
        │   ├── effective_config.yaml      # Runtime-resolved config
        │   ├── sim_metrics.json           # Timing and run stats
        │   ├── action_events.jsonl        # Per-step action log
        │   ├── probe_events.jsonl         # Probe outputs
        │   └── checkpoints/               # Step checkpoints (if enabled)
        ├── default_2026-05-01_12-30-00.log
        └── configs/N10_T5_independent_run1/   # hydra.output_subdir
            ├── config.yaml                # Hydra-composed config snapshot
            ├── hydra.yaml
            ├── overrides.yaml             # CLI overrides for this run
            └── effective_config.yaml      # Runtime-resolved config
```

The timestamp is in the leaf, so repeated runs of the same parameters accumulate
side by side instead of overwriting each other. `hydra.output_subdir` is
redirected to `configs/{jobname_format}`, so there is no `.hydra/` directory.

### Preflight validation

Every run validates the composed config **before** any model is constructed or any
checkpoint is loaded. In addition to the structural and `class_path` checks, two
cross-cutting validators run against `agents.persona_pipeline.classes` as it is
actually composed (i.e. under the `agents` package, the way every scenario writes
it):

- **Cross-references.** `fully_connected_targets` (anywhere in the config,
  including per-GM `gm_orchestration` blocks) must name a declared **sim role** —
  a class's `sim_role_name`, defaulting to the class name (an explicit
  `sim_role_name: null` or `""` also means "use the class name", both here and in
  the builder) — not the class name when the two differ. Under a custom
  `agents.builder.class_path` the check is **skipped**: that builder owns the
  roles its agents carry, so nothing in the config can judge them.
  `fixed_action.action_set_ref` must name a known set. Each
  `eval.probes.probes.<id>` must be a mapping declaring a `probe_type`, so a
  mis-shaped probe block cannot silently deploy zero probes — except for an entry
  the probe deployer would skip anyway (`deployment.enabled: false`, globally or
  on the entry), so a disabled stub is not required to carry one.
- **Data files.** A class's `data.path` (for the file sources `local_json`, `csv`,
  `jsonl`), its `shared_memories.path`, and `fixed_action_sets.file` must resolve
  against the scenario directory. A typo fails preflight naming the path.

An unresolved interpolation in the agents config (e.g. `${event.contxt}`) is also
an error rather than a literal `${...}` pasted into every affected persona.

To run these checks **without launching a run**, use the `silisocs-config-dry-run`
CLI; `--config-path <dir>` (accepting either `scenarios/x` or `scenarios/x/conf`)
restricts it to one scenario. See [Usage](usage.md).

---

## World Config (`world/default.yaml`)

Defines run parameters and the narrative context. Uses `@package _global_` so
all keys are placed at the config root. Scenario-specific content lives in
`scenarios/*/conf/world/default.yaml`.

```yaml
# @package _global_
scenario_name: my_world
num_agents: 50
num_steps: 20
seed: 42
run_name: my_world
jobname_format: "N${num_agents}_T${num_steps}_${run_name}"

setting:
  name: My Community
  background:
    - A social media community with distinct user groups.

event:
  name: The Event
  context: |
    Full narrative context injected into agent memories.

data: {}   # Scenario-specific structured data (e.g. news_file)
```

`${event.context}` and `${setting.background}` are available as interpolation
targets in `agents/default.yaml` and other config files. The root `data:` block is
also passed to the agent builder, so a persona class with
`use_news_file_posts: true` reads `data.news_file` from here.

---

## Agents Config (`agents/default.yaml`)

Defines the persona pipeline, shared memories, and initial observations. Uses
`@package agents` so all keys are nested under `agents.*`. Scenario-specific
content lives in `scenarios/*/conf/agents/default.yaml`.

### Persona Pipeline

```yaml
# @package agents
persona_pipeline:

  defaults:                     # Applied to all classes
    params:
      world_context: ${event.context}
      seed_post: ""
      bio: ""
      style: ""
      goal: null
    shared_memories:
      - "A shared memory for all agents."
    field_map:
      name: name
      context: persona

  classes:
    <class_name>:
      count: ${num_agents}              # Number of agents in this class
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: user               # Role name for activity rates
      flow_tag: default                 # Optional class-level flow tag
      model: null                       # Per-class LLM override: scalar name OR
                                        # a full {name, temperature, provider,
                                        # api_base, api_key, extra_kwargs,
                                        # disabled} block overriding sim.llm
                                        # per-field (unset fields fall back to
                                        # global). Models are deduped by effective
                                        # config, so a no-override run still shares
                                        # one model while classes differing in any
                                        # field get distinct model objects.
      data:
        source: inline                 # inline | config_path | local_json | hf_dataset
        records:
          - name: Alex
            persona: Alex follows local policy and posts practical updates.
      field_map:
        name: name
        context: persona
      params:
        goal: "Have a productive discussion."
      shared_memories:
        - "Class-specific memory."

shared_memories:
  - ${event.context}

initial_observations:
  - "{name} is at home checking their social media feed."
```

**Class keys are a closed set.** Each `persona_pipeline.classes.<class>` mapping
accepts exactly the keys the builder reads:

`class_path`, `compat`, `count`, `data`, `derive_name_from_context`, `field_map`,
`fixed_action`, `flow_tag`, `include_news_images`, `model`,
`name_from_context_words`, `params`, `shared_memories`, `sim_role_name`,
`specific_memories_field`, `use_news_file_posts`.

Anything else fails validation naming the class, the bad key, and the nearest
valid key — so `flow_tags:` no longer silently leaves every agent in the default
flow. Per-agent constructor arguments belong under `params` (which stays open:
it is validated against the agent class's own constructor at build time). A custom
`agents.builder.class_path` defines its own class vocabulary, so the check is
skipped when one is configured.

### Fixed-action sets

A class with `fixed_action.enabled: true` draws its actions from a named set under
`fixed_action_sets` (`inline:` sets declared in the agents config, and/or a `file:`
loaded at build time). The block may be declared under `agents:` or at the config
root (the `@package _global_` world-file spelling); both are validated and both are
loaded, with the `agents:` spelling winning if a config carries both. Every
rejection here **raises**, naming the class and the
offending entry — a dropped or rescheduled fixed action silently changes what the
scenario does while the run still looks healthy:

- `enabled: true` with no `action_set_ref`, or an `action_set_ref` naming no
  inline set (when no `file:` is configured), fails **preflight** at config
  validation.
- An action entry that declares neither `action` nor `action_type` is an error,
  not a skipped entry.
- `args` must be a mapping, and `actions` must be a list.
- `episode` must be a non-negative integer. A malformed value is an error — it
  used to silently become `0` and fire the action at the very first step.

### Data Sources

| Source | Required Keys | Description |
|--------|--------------|-------------|
| `hf_dataset` | `dataset`, `split` | HuggingFace Datasets (cached after first download) |
| `inline` | `records` | Records defined directly in YAML |
| `config_path` | `path` | Dot-path reference into another config section (e.g. `candidates`) |

### Count vs. available records (persona recycling)

A class's `count` (commonly `${num_agents}`) sets how many agents the class
builds; the data source supplies the persona records. The builder reconciles the
two automatically:

- **`count` ≤ records**: the record list is truncated to `count`.
- **`count` > records**: the records are **recycled** to reach `count`, and each
  extra pass gets a numbered suffix so agent names stay unique
  (`Alex`, `Alex 2`, `Alex 3`, …). A single `WARNING` is logged naming the class
  and the shortfall.
- **`count` > 1 with no `data` block**: raises, naming the class. There is no
  record to recycle, so the class could only ever build one agent; building it
  silently would leave the run short of the agents the scenario asked for.
- **`count` must be a non-negative integer** (`0` disables the class). A negative
  or non-numeric `count` raises, naming the class.

This means any `num_agents` works out-of-the-box. There is no silent cap at the
record count. The bundled default agents config ships **100 distinct starter
personas**, so the default scenario scales to large agent counts before any
recycling happens. For fully distinct personas at larger scale, point the class
at a bigger data source (`csv`, `jsonl`, or `hf_dataset`) instead of relying on
recycling.

### `num_agents` vs. per-class `count`

There are two related knobs, and it is important to understand which one is
authoritative:

| Field | Scope | Role |
|-------|-------|------|
| `num_agents` | run param (config root) | **Declared total.** Convenience value used in the job name and run metadata, and commonly referenced as `count: ${num_agents}`. |
| `count` | per persona-pipeline class | **Authoritative.** How many agents that class builds. |

The **actual** number of agents is the **sum of every class's `count`**: it is
neither capped nor padded to `num_agents`. In the default config one class uses
`count: ${num_agents}` and the others use `count: 0`, so the totals agree. If you
add classes with explicit counts, make sure they **sum to `num_agents`** (set
unused classes to `count: 0`).

If the built total diverges from `num_agents`, a `WARNING` is logged at build
time, since this usually indicates the class counts were not kept in sync with
the declared total. The warning appears when the run starts (including in
Studio's run log) — Studio's pre-launch preflight does not sum class counts, so
it will not flag the mismatch before you launch.

### Alternate Agents Variants

Create additional files alongside `default.yaml` for lightweight or experimental
variants:

```
agents/
├── default.yaml    # Full persona set
└── thin.yaml       # Minimal personas for fast testing
```

Select at runtime using the Hydra config group override syntax:
```bash
uv run silisocs --config-path scenarios/ai_conference/conf \
    agents=thin
```

---

## Env Config (`env/twitter_like.yaml`)

### Backends

Seven backend types are registered by name in
`silisocs.environments.backends.factory`: `twitter_like`, `reddit_like`,
`mastodon`, `messaging`, `public_goods`, `resource_market`, and `virtual_space`.
Six of them ship a packaged `env` preset you can select with `env=<name>`
(`twitter_like`, `reddit_like`, `mastodon`, `messaging`, `resource_market`,
`virtual_space`); `public_goods` has no packaged preset and is configured by the
bundled `scenarios/public_goods_game`. Full descriptions of each backend's
actions and mechanics are in [Environment Backends](backends.md).

**Twitter-like (default)**
```yaml
gm:
  backend:
    type: twitter_like
    class_path: null
    params:
      perform_operations: false
      app_description: |
        Twitter-like social backend. Agents can create posts, reply, like, ...
```

**Reddit-like**
```yaml
gm:
  backend:
    type: reddit_like
    class_path: null
    params: {}
```

**Mastodon (remote)**
```yaml
gm:
  backend:
    type: mastodon
    class_path: null
    params:
      perform_operations: false
      reset_server_on_setup: false
```

Dry-run is the packaged default. Live mutation requires `silisocs[mastodon]`,
server URL/API credentials, and an explicit
`env.gm.backend.params.perform_operations=true` override. Server clearing is
separately gated by `env.gm.backend.params.reset_server_on_setup=true`.
See [Installation](installation.md) for `.env` setup.

**Messaging** (`env=messaging`) — the default agent-to-agent direct-message /
broadcast channel. In-memory, no database; compose it with a game backend through
a multi-GM flow chain ("talk, then move"), or run it standalone.

```yaml
gm:
  backend:
    type: messaging
    class_path: null
    params:
      history_window: 20          # delivered messages an observation shows (most recent kept)
      max_message_length: 2000    # longer submissions are REJECTED, not truncated
      app_description: |
        You are in a group of participants who communicate by direct message.
        You can send a private message to any named participant, or broadcast a
        message that everyone will see.
    enabled_actions: null
    excluded_actions: null
```

Actions are `SEND_MESSAGE` (private, to one named participant) and `BROADCAST`
(everyone sees it). Delivery is observational — a message appears in the
recipient's next observation, so ordering stays deterministic under any executor —
and privacy is a rendering rule: everything is stored once and the committed log
records all traffic, but `observe` shows an agent only what they sent, what was
sent to them, and broadcasts. The packaged preset pairs it with `app_initialize` /
`all_agents` / `app_observation` / `tool_calling` / `app_update` components and
names the GM `messaging_gm`.

**Public goods** — the reference game-theoretic backend (a repeated linear
public-goods game). It has no packaged `env` preset; the shipped configuration
lives in `scenarios/public_goods_game/conf/env/public_goods_game.yaml`.

```yaml
gm:
  backend:
    type: public_goods
    class_path: null
    params:
      endowment: 20            # tokens per player per round
      multiplier: 1.6          # pool multiplier (keep 1 < multiplier < N)
      num_rounds: ${num_steps}
      history_window: 0        # resolved rounds shown in the observation (0 = all)
      app_description: |
        You are playing a repeated public-goods game. ...
```

**Resource market** (`env=resource_market`)
```yaml
gm:
  backend:
    type: resource_market
    class_path: null
    params:
      initial_cash: 20
      initial_inventory:
        food: 1
        wood: 0
        ore: 0
      production_capabilities:      # what each sim role can produce per production action
        farmer: {food: 2}
        woodworker: {wood: 2}
        miner: {ore: 2}
        merchant: {food: 1, wood: 1, ore: 1}
      role_needs:                   # what each role must consume each upkeep interval
        farmer: {wood: 1}
        woodworker: {food: 1}
        miner: {food: 1}
        merchant: {food: 1}
      upkeep_interval: 2            # steps between upkeep rounds (0 disables upkeep)
      initial_satisfaction: 0       # starting satisfaction score per agent
      fulfilled_need_reward: 1      # satisfaction added per need met at upkeep
      shortage_penalty: 1           # satisfaction removed per need unmet at upkeep
      resources: [food, wood, ore]  # the tradable resource vocabulary
      perform_operations: false
      app_description: |
        You are operating in a resource market environment. ...
```

The packaged preset declares **four** roles — `farmer`, `woodworker`, `miner`,
and `merchant` — in both `production_capabilities` and `role_needs`. Both maps are
looked up by agent **name** first, then by sim **role**; `production_capabilities`
also accepts a `default` key as a catch-all, and an agent matching no `role_needs`
entry simply has no upkeep needs.

**Virtual space** (`env=virtual_space`)
```yaml
gm:
  backend:
    type: virtual_space
    class_path: null
    params:
      rooms: [atrium, garden, workshop]
      starting_room: atrium
      room_descriptions:
        atrium: A bright central hall with paths to every other room.
        garden: A quiet garden for private conversations.
        workshop: A practical room filled with tools and shared projects.
      connections: null          # null = fully connected; or a map of room -> [reachable rooms]
      room_tasks:                # the packaged preset ships all three
        - task_id: welcome_board
          room: atrium
          description: Prepare a shared welcome board for later arrivals.
          required_effort: 2
          completion_message: The welcome board now summarizes the group's first impressions.
        - task_id: garden_map
          room: garden
          description: Sketch a map of quiet places in the garden.
          required_effort: 2
          completion_message: The garden map now helps visitors find calm corners.
        - task_id: repair_bench
          room: workshop
          description: Repair the central workbench for future collaboration.
          required_effort: 3
          completion_message: The repaired workbench is ready for group projects.
      perform_operations: false
      app_description: |
        You are operating in a virtual space. ...
```

`resource_market`, `virtual_space`, `messaging`, and `public_goods` are in-memory
references with **no database** — `db_path` belongs to the SQLite social backends
only.

**`app_description`** is a param every shipped backend accepts (it backs the
backend's `description()`). It is the human-readable blurb the auto-generated
action catalog puts at the top of the prompt under `sim.action_mode: generic`, so
editing it is the no-code way to reframe what the platform *is* for the agents
without touching the per-action descriptions. Under `action_mode: custom` the
world's own `action_prompt` supplies that framing instead, and
`app_description` is only surfaced where a backend chooses to use it.

Custom backend apps can be loaded without editing the factory:

```yaml
gm:
  backend:
    type: custom
    class_path: my_pkg.apps.MyBackendApp
    params:
      custom_setting: value
```

`gm.backend` is a [slot](#slots), except that it selects a shipped backend with
`type` rather than `built_in`.

### Enabled Actions

By default agents can use all backend actions. Restrict to a subset:

```yaml
env:
  gm:
    backend:
      enabled_actions:
        - create_tweet
        - reply_to_tweet
        - like_tweet
        - FINISHED
      excluded_actions:
        - report_post
```

Action names may be canonical decorated backend function names or selectable
aliases such as `FINISHED`. Unknown names fail during backend construction. If
an action is matched by both `enabled_actions` and `excluded_actions`, the run
fails loudly instead of guessing which list wins.

`enabled_actions` distinguishes "no filter" from "an empty allow-list":

| Value | Meaning |
|---|---|
| `null` (or absent) | no filter — every `@app_action` is exposed |
| `[]` | an allow-list matching nothing — **no** actions are exposed |
| `[a, b]` | only `a` and `b` are exposed |

A filter that leaves no callable action fails during backend construction rather
than at the agent's first turn. `FINISHED` does not count: it only ends a turn,
so a catalog holding only it still leaves agents with nothing to do.

### Action Aliases (agent-facing renaming)

Give backend actions simpler/different agent-facing names without editing backend
code, to simplify the action vocabulary agents see and emit:

```yaml
env:
  gm:
    backend:
      action_aliases:
        create_tweet: post            # rename: agents see + call "post"
        like_tweet: [like, fav]       # "like" is displayed; "fav" also accepted
```

- The key is an existing action (its canonical method name or current selectable
  name); the value is a single new name (rename) or a list (the first is shown to
  agents, all are accepted by the parser).
- The renamed name appears in the auto-generated action catalog/prompt, and the
  canonical name plus every alias all resolve to the same action.
- Works across resolve modes: `generic`/`tool_calling` dispatch aliases directly;
  the custom `parsed_action` parser receives the token normalized to the
  canonical method name.
- Unknown actions, empty names, or a name that collides with another action fail
  loudly at backend construction. Aliases are applied before
  `enabled_actions`/`excluded_actions`, so filters may reference either vocabulary.

#### Per-flow action filters

`env.gm.backend.enabled_actions`/`excluded_actions` apply to every agent on that
backend. To restrict the action surface *per flow* (e.g. a `lurker` flow that
may only `like`/`repost` while a `poster` flow may publish), add a
`flow_action_filters` map on the resolve component. It is enforced at resolve
time (the disallowed action is rejected before the backend runs) and only ever
*further-restricts* the backend-wide filter:

```yaml
env:
  gm:
    components:
      resolve:
        built_in: parsed_action        # parsed_action | generic_action | tool_calling
        params:
          flow_action_filters:
            default:                    # fallback for any unlisted flow
              enabled_actions: null     # null => all backend actions
            lurker:
              enabled_actions: [like, repost]
            poster:
              excluded_actions: [follow_user]
```

Keys are flow tags (from `persona_pipeline.classes.<class>.flow_tag` or
`sim.engine.step.params.agent_to_flow`); values reuse the same
`enabled_actions`/`excluded_actions` vocabulary as the backend filter and match
both canonical and selectable names. This works on the default
`ComponentGameMaster` (no `MultiFlowGameMaster` needed). The terminal `FINISHED`
signal is never blocked, so open-ended flows can always terminate. Omitting the
key preserves current behavior. In `custom` (`parsed_action`) mode, specify
filters using the agent-facing verbs the parser emits (`post`, `like`, `reply`,
`repost`); in `generic`/`tool_calling` mode, use backend action names. The
strictness follows the RESOLVE COMPONENT (not `sim.action_mode`): under
`resolve.built_in: parsed_action`, names that match no backend action are
matched literally and logged as a warning (world-defined verbs may
legitimately be absent from the catalog); under `generic_action` or
`tool_calling` — the catalog-bound resolvers — such a name can never match a
real action, so it is a build-time `ValueError`. Per-flow filtering enforces; to also *hide* actions from a flow's prompt,
give that flow its own `action_prompt` instance via `MultiFlowGameMaster`.

| Backend | Common actions |
|---------|----------------|
| `twitter_like` | `create_tweet`, `reply_to_tweet`, `like_tweet`, `unlike_tweet`, `repost_tweet`, `quote_repost_tweet`, `follow_user`, `unfollow_user`, `mute_user`, `unmute_user`, `search_posts`, `get_trending_posts`, `report_post`, `update_profile`, `view_profile`, `do_nothing`, `FINISHED` |
| `reddit_like` | `create_reddit_post`, `create_comment`, `upvote`, `downvote`, `unlike_post`, `dislike_post`, `undo_dislike_post`, `get_home_feed`, `get_post_comments`, `search_subreddits`, `get_trending_posts`, `report_post`, `mute_user`, `unmute_user`, `update_profile`, `view_profile`, `do_nothing`, `FINISHED` |
| `mastodon` | `post_toot`, `reply_to_toot`, `like_toot`, `boost_toot`, `follow_user`, `unfollow_user` |

### Harness agents (no GM config needed)

**Harness agents** (embedded Hermes/OpenClaw agents — see
[Harness Agents](harness_agents.md)) need no special game-master components. Point a
persona class at a harness agent (`class_path: silisocs.agents.harness.fake.FakeHarnessAgent`
/ `hermes.HermesAgent` / `openclaw.OpenClawAgent`, optional `params.probe_mode` = `model`
default or `harness`) and run on the **default** GM. The default action-prompt binds the
per-turn Tool Bridge for agents that want one, and the shared resolve base records the
self-describing harness turn regardless of which resolve is configured — so harness and
native agents mix freely in one GM.

### Seed Posts

Initialize agent feeds with background posts before the simulation starts:

| Type | Description |
|------|-------------|
| `agent` | Ask agents for starting posts through their normal act path |
| `csv` | Pre-written posts from a CSV file (`agent_name,post_text`) |
| `json` | Pre-written posts from a JSON file (`{"agent_name": "post_text"}`) |
| `none` | Disable seed posts (organic growth only) |
| `fallback` | File values first, agent-generated posts for missing agents |

```yaml
sim:
  initialization:
    simulation:
      built_in: seed_posts
      class_path: null
      params:
        type: agent
        params:
          file_path: null   # Path to CSV/JSON file when type is csv/json/fallback
```

Agent and Game Master initialization are configured separately:

```yaml
sim:
  initialization:
    agents:
      built_in: raw_memory
      class_path: null
      params: {}
    game_masters:
      built_in: default
      class_path: null
      params: {}
```

Each native Game Master has an initialize component slot:

```yaml
env:
  gm:
    components:
      initialize:
        built_in: social_media   # social_media | app_initialize | none | disabled
        class_path: null
        params: {}
```

(`none` and `disabled` are aliases for the same no-op initializer.)

### Agent Memory (`sim.memory`)

`sim.memory` governs how a NativeAgent *records* observations and *renders* the
"Memory" section of its prompt at runtime — distinct from
`sim.initialization.agents`, which only SEEDS memories at step 0.

```yaml
sim:
  memory:
    built_in: window            # window | retrieval | summarizing
    class_path: null
    params: {}
```

| Built-in | Behavior | Params |
|----------|----------|--------|
| `window` (default) | Keep the last N memories, render the last `render_count`. Byte-identical to the pre-slot behavior. | `render_count` (10); store cap defaults to the agent's `memory_history` |
| `retrieval` | A recency window PLUS relevance recall: always render the last `window_count` memories verbatim, and prepend the `retrieved_count` OLDER memories most relevant to the current observation by deterministic lexical overlap (recency tiebreak) — replay-stable, no embedding API. `window_count: 0` recovers pure retrieval; `retrieved_count: 0` is a plain window. | `window_count` (40), `retrieved_count` (10) |
| `summarizing` | Three tiers: all rolling summaries, then the `retrieved_count` most relevant OLDER memories, then the recent `render_count` window. When memory exceeds `max_memories`, the oldest `chunk_size` are compressed into one summary via a model call. | `max_memories` (200), `chunk_size` (50), `max_summaries` (20), `render_count` (40), `retrieved_count` (10), `prompt`, `summary_max_tokens` (256) |

`summary_max_tokens` (default `256`) is the `max_tokens` passed to the
summarization call, i.e. the length budget for **one** rolling summary — raise it
when a `chunk_size` of dense observations is being compressed too aggressively,
lower it to cut summarization spend. Only `summarizing` reads it. Summaries are
capped at `max_summaries`, so the summary tier's contribution to every later
prompt is bounded by roughly `max_summaries * summary_max_tokens`. If the
summarization call fails, the policy falls back to truncating the chunk and warns
once, so a run never silently reinterprets itself as a `window` memory.

A custom policy is `class_path` to a `MemoryPolicy` subclass (an ordinary
[slot](#slots); `model` and `memory_history` are the framework kwargs the
runtime injects, so they are the only names filtered out of `params`).
Determinism:
`window`/`retrieval` are deterministic; `summarizing` is only as reproducible as
the model it calls (summarization runs at record/observe time, so its tokens are
counted under the caller's phase in [token usage](#llm): `action` inside a turn,
`other` for observes outside one — agent initialization,
`broadcast_observation`). Memory rides inside the agent's
checkpoint state, so it resumes without extra plumbing; `summarizing` persists
its summaries so a resumed run never re-summarizes. Applies to NativeAgent —
Concordia-compat agents manage memory through their own components.

### GM Components

```yaml
env:
  gm:
    components:
      next_acting:
        built_in: all_agents            # all_agents | fixed_order (activity models moved to sim.engine.participation)
      observe:
        built_in: timeline_every_turn   # app_observation | timeline_every_turn | episode_only
        params:
          episode_observation_flows: [fixed_pre]
      resolve:
        built_in: tool_calling          # parsed_action | generic_action | tool_calling
      update:
        built_in: app_update            # app_update | social_recommendation | disabled | none
```

Each component role is a [slot](#slots): `class_path` swaps in your own
implementation, `params` are strict constructor arguments, and `params: null`
clears an inherited params block. Observe components that explicitly accept
`observation_params` can use `params` as forwarded observation settings.

!!! warning "`episode_observation_flows` (plural) vs `episode_observation_flow` (singular)"

    The two observe built-ins spell this param differently, and `params` are
    strict — the wrong spelling fails at build:

    | Built-in | Param | Type |
    |---|---|---|
    | `timeline_every_turn` | `episode_observation_flows` | list of flow tags |
    | `episode_only` | `episode_observation_flow` | a single flow tag (string) |

    In both cases a matched agent receives the bare `EPISODE: <n>` observation —
    under `timeline_every_turn` that replaces the timeline it would otherwise
    get, and under `episode_only` an unmatched agent gets an empty observation.
    This is how the `fixed_pre` broadcast flows in the bundled social scenarios
    are wired (see `scenarios/*/conf/env.yaml`).

`initialize: social_media`, `observe: timeline_every_turn`, and
`update: social_recommendation` call `SocialBackendApp`-only methods; naming one
on a generic backend raises a `TypeError` at game-master build. Their generic
counterparts are `app_initialize`, `app_observation`, and `app_update`/`none`.
An omitted `observe` slot follows the backend automatically
(`timeline_every_turn` for a social backend, `app_observation` otherwise), so a
generic scenario need not spell it out. A custom component with the same
requirement declares `requires_social_backend = True`.

### Social Setup and Participation

Graph fields are owned by the GM initialize component (environment layer).
Activity selection is owned by the sim-level participation policy
(`sim.engine.participation`): it filters which agents are in each step's roster
before any scheduling and before every GM's `next_acting` component runs
(effective acting = participation filter ∩ next_acting output).

```yaml
env:
  gm:
    components:
      initialize:
        params:
          graph:
            network_type: barabasi_albert
            barabasi_albert_m: 10
            base_followership_probability: 0.3
            fully_connected_targets:
              - news_account
sim:
  engine:
    participation:
      built_in: activity_probability  # all | activity_probability | activity_markov
      class_path: null                # or a custom ParticipationPolicy class
      params:
        active_probability: null      # global override; null = per-role rates below
        min_active_agents: 1          # top up a too-small draw (deterministic)
        activity_transition_rates:
          <role_name>:
            inactive_to_active: 0.3
            active_to_inactive: 0.3
```

Participation policies are pure functions of `(agent_names, step_index, seed)` —
stateless, so runs replay and resume identically with nothing to checkpoint.
`activity_markov` re-derives its per-agent activity chain from step 0 on each
call. A custom policy subclasses
`silisocs.simulation_engines.policies.participation.ParticipationPolicy` and is
referenced via `class_path`; declare a `sim_roles` constructor param to receive
the agent→role mapping. Set `built_in: all` (pass-through) for deterministic or
turn-based runs (e.g. `fixed_order` environments), where every agent should stay
in the roster.

The default is `all` (pass-through: every agent stays in the roster every step),
in **both** `sim/base.yaml` and the in-code `build_engine` fallback used when the
`participation` slot is absent entirely (e.g. a programmatic call). This is
deterministic, matches the pre-participation behavior, and keeps a bare `env=`
preset run (e.g. `env=resource_market`, whose roles are `farmer`/`miner`/… rather
than `user`) from being gated by rates that never matched it. Activity gating is
therefore **opt-in**: a scenario that wants probability filtering sets
`built_in: activity_probability` (with per-role `activity_transition_rates`
matching its own roles) in its `sim.yaml`, exactly as the bundled social
scenarios do.

`activity_transition_rates` are keyed by agent name or sim role, and **every agent
must match an entry** that declares `inactive_to_active` or `active_to_inactive`
(the missing one of the pair mirrors the declared one). An agent that matches
neither is a configuration error: the run fails at its first step with a message
naming the unmatched agents and roles, instead of throttling them on an invented
default. The ways out are all explicit — add rates for those agent names or sim
roles, set `active_probability` for one global rate (`activity_probability` only),
or use `built_in: all` so every agent acts every step.

**Each rate is named, never positional.** An entry is always a mapping —
`{inactive_to_active: 0.8, active_to_inactive: 0.1}` — so neither number's meaning
depends on its order; a bare pair (`user: [0.8, 0.1]`) or a scalar is rejected at
config validation, not at the first step. What each rate means depends on the
policy reading it:

| Policy | `inactive_to_active` | `active_to_inactive` |
|--------|----------------------|----------------------|
| `activity_probability` | **the per-step probability that this agent acts at all** (draws are independent per step) | **not read** — it only serves as the mirror value when `inactive_to_active` is omitted |
| `activity_markov` | probability an *inactive* agent switches to active this step | probability an *active* agent switches to inactive this step |

So under `activity_probability`, `{inactive_to_active: 0.8, active_to_inactive: 0.1}`
means "each agent acts with p = 0.8 every step" — the `0.1` is inert. Under
`activity_markov` the same entry means "bursty: quick to wake, slow to go quiet",
with a long-run active share of `0.8 / (0.8 + 0.1) ≈ 0.89`. The preflight estimate
Studio shows (`expected_active_share()`) uses exactly these definitions: the mean
`inactive_to_active` for `activity_probability`, and the mean
`inactive_to_active / (inactive_to_active + active_to_inactive)` for
`activity_markov`.

### Recommendation Updates (`update: social_recommendation`) {#recommendation-updates}

The `social_recommendation` update component recomputes each user's recommended
posts between steps. The packaged `env/twitter_like.yaml` sets it up like this:

```yaml
env:
  gm:
    components:
      update:
        built_in: social_recommendation
        params:
          default_recsys_type: null
          update_every_n_steps: 1
          lazy: true
          max_posts: 10
          user_context_recent_posts: 10
          include_like_trace: true
          like_trace_window: 10
          like_trace_weight: 0.5
          include_like_trace_in_context: false
```

| Param | Default (preset) | Meaning |
|---|---|---|
| `default_recsys_type` | `null` | Which recommender to **compute**. `twitter_like` supports `twitter` (sentence-transformer embeddings), `twitter_tfidf` (TF-IDF, no model download), and `twhin`; `reddit_like` supports `reddit` and `twhin`; an unsupported name fails at init. `null` means *no recommender is configured* — the component logs a `recsys_update_skipped` / `no_recsys_types` event and does nothing, so feeds fall back to follower-based content. Swappable mid-run via the `set_recsys` intervention. |
| `update_every_n_steps` | `1` | Steps between recommendation refreshes. |
| `lazy` | `true` | Scope each refresh to agents that have acted since the last one, instead of the whole population. With `update_every_n_steps > 1` the scoped set accumulates across skipped steps, so nobody active is missed. Backends whose `update_recommendations` does not accept `active_agent_names` fall back to a full recompute. |
| `max_posts` | `10` | Maximum recommended posts stored per user per refresh. |
| `user_context_recent_posts` | `10` | How many of a user's own recent authored posts are folded into the textual user profile the recommender embeds (`0` = none). |
| `include_like_trace` | `true` | Use the user's recent **likes** as an additional ranking signal. |
| `like_trace_window` | `10` | How many recent liked posts form that trace. |
| `like_trace_weight` | `0.5` | Blend weight in `[0, 1]` between the like-trace similarity and the profile similarity (clamped). |
| `include_like_trace_in_context` | `false` | Whether the liked posts' text is also appended to the textual user context (as opposed to only being used as a similarity signal). |

The last five are forwarded verbatim to the backend's `init_recsys(...)`, so they
configure the recommender itself; `update_every_n_steps` / `lazy` / `max_posts`
govern the refresh cadence. `recsys_type`, `update_every_n_steps`, `lazy`, and
`max_posts` are `runtime_tunable`, so a `set_component_params` intervention can
retune them mid-run. A refresh that raises is **counted**, not fatal: the run
continues on the previous recommendation rows and the failure surfaces as a
run-health counter.

### Timeline Observation

```yaml
env:
  gm:
    components:
      observe:
        built_in: timeline_every_turn
        params:
          timeline_mode: hybrid_recsys_follower   # packaged twitter_like default
          recsys_type: null
          timeline_posts: 10
          timeline_config:
            recsys_ratio: 0.6
            follower_ratio: 0.4
```

| Mode | Backends | Description |
|----------|-----------|-------------|
| `follower_chronological` | All | Recent posts from followed users, no algorithm |
| `pure_recsys` | Twitter, Reddit | Algorithm-selected posts only |
| `hybrid_recsys_follower` | Twitter, Reddit | Blend of recommendations + followed posts |
| `curated_global` | Twitter only | Trending posts + personalized recommendations |

| Param | Default (preset) | Meaning |
|---|---|---|
| `recsys_type` | `null` | Which recommender's rows an agent **sees**. Distinct from the update component's `default_recsys_type`, which controls what is *computed* — a run that computes several can show one. `null` falls back to the backend's default. The `set_recsys` intervention swaps both. |
| `timeline_posts` | `10` | How many posts one observation shows (the `limit` passed to `get_timeline_mode`). |
| `timeline_config` | `{recsys_ratio: 0.6, follower_ratio: 0.4}` | Free-form mode-specific settings, splatted as keyword arguments into the backend's timeline builder. Under `hybrid_recsys_follower` the two ratios size the two sub-feeds (`limit * ratio`, at least 1 each); recommended posts come first, the follower feed is appended, duplicates are dropped by post id, and the result is truncated to `timeline_posts` — so the ratios are a priority split, not an exact quota. Modes that take no extra settings ignore it. |

#### Exposure logging

The timeline observe component records what each agent SAW — the post ids +
per-post `source` (`follower` / `recsys:<type>`) shown each turn — to
`exposure_events.jsonl` (mirroring `action_events.jsonl`, per-GM directories and
all). Exposure→action is the unit of analysis for recommender/platform studies;
`silisocs.evaluations.exposure.exposure_action_join(run_dir)` computes per-agent
engagement of shown posts. On by default; disable with:

```yaml
env: {gm: {components: {observe: {params: {log_exposures: false}}}}}
```

It logs ids/source only (not content — recoverable by id), so the payload stays
small, and it no-ops for backends without a SQLite timeline (e.g. Mastodon).

---

## Evals Config (`eval/base.yaml`)

The packaged `src/silisocs/conf/eval/base.yaml` in full:

```yaml
# @package eval

probes:
  schedule:
    built_in: step_schedule
    class_path: null
    params: {}
  # deployment also accepts sampling caps applied AFTER the include/exclude
  # filters (seed-derived per (seed, step, agent), replay/resume stable):
  #   deployment:
  #     sample_k: 50          # probe at most 50 filtered agents per due step
  #     sample_fraction: 0.1  # or a fraction (ceil), in (0, 1]; not both
  # Unset (the default) probes every filtered agent.
```

So out of the box a run declares only the probe **schedule** slot and no probes:
`eval.probes.deployment` and `eval.probes.probes` are supplied by the scenario
(its flat `conf/eval.yaml`) or by CLI overrides. See [Probes](#probes) below.

!!! note "`eval`, `evaluations:`, and `silisocs.evaluations` are three different things"

    | Name | What it is | Where it lives |
    |------|-----------|----------------|
    | the `eval` **config group** (`eval.probes.*`) | *in-run* measurement: probes the engine fires at loop boundaries during a simulation, writing `probe_events.jsonl` | `src/silisocs/conf/eval/base.yaml`, a scenario's `conf/eval.yaml`, or `eval.probes.*` overrides |
    | the `evaluations:` **study key** | *post-run* analysis: evaluator specs a study runs over finished run directories, writing study metrics | `study.yaml` (see [study_schema.md](study_schema.md)) — never a scenario config |
    | the `silisocs.evaluations` **Python package** | the code implementing both of the above, plus the `load_run`/`load_study` artifact loaders | `src/silisocs/evaluations/` |

    A scenario config has no `evaluations:` key, and a study's `evaluations:`
    list is not merged into the `eval` group. If you want a scenario to measure
    something *while it runs*, you want `eval.probes`; if you want to score runs
    *after* they finish, you want a study's `evaluations:`.

### Probes

**Probes live at `eval.probes` — never at the config root.** In a scenario that
means the flat `conf/eval.yaml`, whose top-level keys are merged under `eval`, so
the file's `probes:` key becomes `eval.probes`. Writing a `probes:` block into
`conf/world/default.yaml` does **not** work: that file carries
`# @package _global_`, so the block lands at the config *root*, where nothing
reads it. That used to produce a run that completed normally and emitted zero
probe events; it now **fails at build**:

```text
Config error: a root-level `probes:` block is not read by anything.
Probes are configured at `eval.probes`, never at the config root.
```

The error shows the flat-`conf/eval.yaml` shape to move the block into. The
snippet below is written as that file:

```yaml
# <scenario>/conf/eval.yaml   (no @package directive — merged under `eval`)
probes:
  probe_lib_module: null   # Optional custom probe type module

  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
    include_agents: []   # Empty = all agents
    exclude_agents: []
    include_classes: []  # Filter by persona class / sim role
    exclude_classes: []
    include_flows: []    # Filter by flow tag; empty = all flows
    exclude_flows: []
    sample_k: null       # After the filters: probe at most K agents per due step
    sample_fraction: null  # ...or ceil(fraction * filtered), in (0, 1]; not both

  probes:
    favorability:
      probe_name: favorability
      probe_type: NumericRatingProbe
      probe_data:
        name: Favorability
        question: "Return a single rating from {lo} to {hi}."
        lo: 1
        hi: 10
      deployment:          # optional per-probe overrides (see below)
        every_n_steps: 5   # this probe runs every 5th step...
        at: run_end        # ...plus a final measurement after the run
```

The `deployment:` block under `probes.deployment` is the **global** default. Any
probe entry may carry its own `deployment:` block that **overrides the global
per field** (unset fields fall back to the global value — the same overlay as a
per-class model override falling back to `sim.llm`). This lets one study mix, for
example, an expensive belief probe every 5 steps on a 10% sample with a cheap
sentiment probe every step — impossible with a single shared schedule. Every
block (global and per-probe) is validated for unknown keys, `every_n_steps >= 1`,
mutually-exclusive `sample_k`/`sample_fraction`, and a valid `at`; a per-probe
error names the probe. `hold_last_response` is read from the **global** block
only. Probes that share a resolved target set on a given step are still batched
into **one** questionnaire LLM call per agent, so per-probe schedules don't cost
extra calls.

**Loop anchors (`at`).** A deployment block's `at` chooses *when in the loop* its
probes fire: `pre_step` (default — before a step runs, measuring the
pre-intervention world), `post_step` (after a step, measuring what it produced),
or `run_end` (once after the whole run — the terminal measurement of the final
world, which `pre_step` never reaches). `run_end` is one-shot: it ignores
`start_step`/`every_n_steps` **and** the engine-level `probes.schedule` cadence
(`fixed_interval` etc.), which gate only the per-step anchors — so disable a
`run_end` probe via its own `deployment.enabled: false`, not the schedule. Every
probe row in `probe_events.jsonl` records its `anchor`, so `post_step` at step *N*
and `pre_step` at step *N+1* (the same world state) stay distinguishable in
analysis. `run_end` rows are logged with `anchor=run_end` at the last executed step
but are not folded into the per-episode `sim_metrics` probe telemetry (there is no
episode to attach to); it also does not re-fire on a resume of an already-complete
run (the loop body never re-executes).

Deployment filters select which agents receive probes and are applied as a
sequential `AND`: `include_classes` → `exclude_classes` → `include_agents` →
`exclude_agents` → `include_flows` → `exclude_flows`. `include_flows`/
`exclude_flows` target by flow tag: e.g. `include_flows: [treatment]` deploys
probes only to agents whose materialized flow is `treatment`, ideal for measuring
a treatment cohort. Flow tags come from the same source as scheduling
(`persona_pipeline.classes.<class>.flow_tag` + `sim.engine.step.params.agent_to_flow`)
and are resolved from the game master's authoritative `agent_flow_tags`, so they
apply uniformly to native and fixed agents. Empty/omitted flow lists preserve
current behavior (deploy to all selected agents).

`sample_k` / `sample_fraction` cap how many of the *filtered* agents are probed
each due step (mutually exclusive; unset probes them all). Selection is a
deterministic hash ranking per `(seed, step, agent)` — independent of roster
order and stable across replay/resume — so each due step probes a fresh but
reproducible subset. Use this to keep probe cost bounded as populations grow
(e.g. `sample_k: 100` at 10k agents). Two probes that each set their own
`sample_k` draw **independent** subsets (the ranking is additionally keyed by the
probe name); probes sharing the global cap draw the same subset.

#### Probe schedule (`eval.probes.schedule`) {#probe-schedule}

`eval.probes.schedule` is an ordinary [slot](#slots) holding an **engine-level
coarse gate** in front of the deployment rules above. It answers one question per
step — "run the probe phase at all?" — *before* the deployment block's
`enabled` / `start_step` / `every_n_steps` / filters are consulted. It is the only
part of probe configuration that lives in the packaged `eval/base.yaml`.

```yaml
eval:
  probes:
    schedule:
      built_in: step_schedule   # step_schedule | fixed_interval | disabled
      class_path: null
      params: {}
```

| Built-in | Params | Behavior |
|---|---|---|
| `step_schedule` (default) | *(none)* | Always run the probe phase; the deployment rules alone decide cadence. Byte-identical to having no gate. |
| `fixed_interval` | `start_step` (`0`), `every_n_steps` (`1`) | Run the phase only when `step >= start_step` and `(step - start_step) % max(1, every_n_steps) == 0`. An engine-level cadence that applies to *every* probe at once, on top of each probe's own deployment schedule. |
| `disabled` | *(none)* | Never run the per-step probe phase. |

The gate applies to the per-step anchors (`pre_step`, `post_step`) only:
`run_end` is a one-shot terminal measurement with no step cadence and bypasses it
(disable a `run_end` probe with its own `deployment.enabled: false`). Because both
the gate and the deployment block must agree, the effective cadence is their
intersection — a probe with `every_n_steps: 1` under a `fixed_interval` gate of
`every_n_steps: 5` fires every 5 steps.

`class_path` accepts any object implementing
`should_run_probe_phase(*, step, orchestrator) -> bool`
(`silisocs.simulation_engines.runtime_base.ProbeSchedulePolicy`); its `params` are
strict constructor arguments like any other slot. Note that per the rule in
[When a CLI override needs `++`](#cli-override-plus-plus), `params` keys need
the `++` prefix from the CLI (`++eval.probes.schedule.params.every_n_steps=5`).

---

## Action Prompt Configuration

### Prompt Additions

| Flag | Default | Effect |
|------|---------|--------|
| `sim.prompt_additions.action_count_guidance` | `true` | Add `[ActNum]` marker and action count guidance |

### The `action_prompt` component's `output_style`

Every packaged `env/*.yaml` configures its GM's `action_prompt` component with two
params:

```yaml
env:
  gm:
    components:
      action_prompt:
        built_in: default
        params:
          action_prompt: |
            You are operating on a Twitter-like social backend.
            ...
            [OUTPUT STYLE]
          output_style: |
            ## OUTPUT FORMAT
            Answer: {name}
            ...
```

- `action_prompt` is the world-facing instruction text. `{name}` is substituted
  with the acting agent's name.
- `output_style` is the **response-format** half, kept separate so it can be
  dropped whole. The compiler splits `action_prompt` at the literal
  `[OUTPUT STYLE]` marker: everything before it is the prompt head, and anything
  after it is a *fallback* style used only when `output_style` is unset. The
  chosen style is then appended at the end of the compiled prompt, after the
  `[ActNum]` action-count guidance, re-prefixed with the marker.
- Under `sim.tool_calling.mode: single | multi` the output-style section is
  **dropped entirely** — the tool schemas define the response format, and
  free-text formatting instructions would only conflict with them. This is why the
  packaged presets can ship both a rich `output_style` and a `tool_calling`
  resolve component: the style text only appears in the non-tool-calling modes.
- The whole compilation applies to `sim.action_mode: custom`. Under `generic`
  these params are unused: the prompt is generated from the backend's action
  catalog (headed by `app_description`) instead.

### How Action Prompts Are Constructed

1. **Runner startup**: `build_action_prompt_with_app_instance()` compiles the base prompt from the world config or backend action catalog (`action_mode: custom` vs `generic`)
2. **GM (`GameMaster.action_prompt`)**: returns a typed `ActionSpec` and includes tool schemas in `extra_args` when `tool_calling.mode != none`
3. **Agent**: calls the LLM in tool-calling or free-text mode from typed `ActionSpec.output_type` and `extra_args`

Tool-calling output style is automatically stripped from the base prompt when
`sim.tool_calling.mode` is not `none`.

---

## Engine Turn Policies

| Policy | Option | Behavior |
|--------|--------|----------|
| Single action | `single_action` | Each agent acts once per episode |
| Fixed count | `fixed_count` | Each agent gets N action turns per episode |
| Open-ended | `open_ended` | Agent acts until outputting a done token |

```yaml
sim:
  engine:
    step:
      built_in: base
      params:
        flow_order: [fixed_pre, default]
        agent_to_flow: {}
    turn_policy:
      built_in: fixed_count
      params:
        count: 3
        observe_before_act: first   # first | always | never
        count_committed: false      # true = count only committed actions
        max_attempts: 0             # 0 -> 2*count (only used when count_committed)
```

Loop, step, and turn policies are [slots](#slots).
`observe_before_act` controls whether repeated-action policies refresh the GM
observation only before the first action, before every action, or never.
Omit it to preserve the default `first` behavior.

`fixed_count` counts EMITTED actions by default: every action an agent produces
consumes from `count`, whether or not it committed a backend change. Set
`count_committed: true` to count only actions that COMMITTED (validated and
executed) — a tool call that fails resolve (bad arguments, unknown action,
execution error, or a flow-filtered action) no longer burns the budget, so the
agent still gets `count` real actions. In that mode `max_attempts` bounds the retry
loop (default `2 * count`) so an agent that keeps emitting invalid actions cannot
loop forever. The commit signal comes from the resolve component; a resolver that
cannot report per-call outcomes falls back to emitted counting. Idempotent no-ops
(re-liking, re-voting) count as committed — "committed" means the action was
accepted and executed, distinguishing it from a rejected or failed emission.

Flow scheduling (requires `engine.step.built_in: flow`):

```yaml
sim:
  engine:
    step:
      built_in: flow
      params:
        flow_order: [fixed_pre, default]
        agent_to_flow: {}
        # Optional per-flow turn policy overrides; each value is a turn-policy
        # slot. Flows not listed here use the global sim.engine.turn_policy.
        flow_turn_policies:
          fixed_pre:
            built_in: single_action
          default:
            built_in: open_ended
            params: {max_actions: 3}
    turn_policy:                 # global default; applies to unlisted flows
      built_in: single_action
```

`sim.engine.turn_policy` is the global default applied to every agent. With
`flow` or `multi_gm` scheduling you may additionally override the policy per
flow via `sim.engine.step.params.flow_turn_policies`, keyed by flow tag, each
value a turn-policy [slot](#slots). Flows absent from the map fall
back to the global policy, so omitting the key reproduces current behavior
exactly. Per-flow overrides are ignored under `base`/`sequential` scheduling
(which do not group agents by flow). For a multi-GM flow chain the same per-flow
policy applies at every GM hop. Hop scheduling itself follows the chosen
`multi_gm*` step strategy — under the default `multi_gm` (concurrent), a flow's
next hop starts as soon as its own previous hop resolves, serializing only when
two flows touch the same GM; select `multi_gm_serial` or `multi_gm_staged` via
`sim.engine.step.built_in` for the other traversal modes (see
[Advanced: Multi-GM Orchestration](#advanced-multi-gm-orchestration)).

You may also override the turn policy per GM via
`sim.engine.step.params.gm_turn_policies`, a `{gm_name: turn_policy_slot}` map.
This lets a GM/backend
set its own per-step action cadence — e.g. `single_action` in a "world" GM but
`open_ended` in a social GM, or a different cadence at each hop of a multi-GM
flow chain. The per-GM key disambiguates hops that share one flow, which
`flow_turn_policies` cannot (it is keyed by flow, so the same per-flow policy
applies at every GM hop). The policy for a batch is resolved by most-specific
wins: per-flow (`flow_turn_policies[flow]`) > per-GM
(`gm_turn_policies[gm_name]`) > global (`sim.engine.turn_policy`). Unset (an
empty map) means the global turn policy applies everywhere, unchanged. Unlike
`flow_turn_policies` (which only takes effect under `flow`/`multi_gm`
scheduling), `gm_turn_policies` applies under any step mode because it is
resolved per batch by GM name.

A sibling key, `sim.engine.step.params.gm_concurrency_caps`
(a `{gm_name: int}` map), caps how many of *that* GM's agent turns run
concurrently via a per-GM semaphore. It is orthogonal to `gm_turn_policies`:
the turn policy controls how *many* actions a single turn takes, while the cap
controls how many turns run *at once*. The global `sim.max_concurrent_actions`
remains the overall ceiling and the default for every GM, so the effective per-GM
limit is `min(cap, sim.max_concurrent_actions)`; an empty map means the global
cap governs everything (unchanged). This lets you throttle a rate-limited backend
(e.g. a live Mastodon server) below the global limit while other GMs keep running
concurrently. Like `gm_turn_policies`, it applies under any step mode.

Keep a cap well below `sim.max_concurrent_actions`: a capped GM's turns block on
its permit *while holding a worker thread*, so a heavily-capped GM with many
queued turns can occupy up to `worker_limit` threads waiting on its own permits
and starve other GMs of throughput (a slowdown, not a deadlock).

Sequential scheduling:

```yaml
sim:
  engine:
    step:
      built_in: sequential
```

`sequential` uses the same GM actor selection as `base`, but executes each
selected agent in its own batch so turns are strictly ordered.

---

<a id="checkpointing"></a>

## Checkpoint Restore

```bash
uv run silisocs \
  --config-path scenarios/my_world/conf \
  num_steps=200 \
  sim.checkpoint.every_n_steps=10 \
  sim.checkpoint.source_run=outputs/my_world/run1 \
  sim.checkpoint.restore.built_in=social_action_event_replay
```

Checkpoints are written to `.../outputs/.../checkpoints/step_<N>_checkpoint.json`.
Restore selects the latest checkpoint in the source run, initializes the runtime
object scaffolding, and then applies checkpointed agent, game-master, component,
and backend state. Built-in local backends restore their world state directly
from the checkpoint. `sim.checkpoint.restore` is still required for source runs
that need a restore strategy, such as older social runs that must rebuild backend
state from `action_events.jsonl`.
Checkpoint runtime metadata records artifact ownership for every Game Master
rather than relying on one representative GM for the whole run.

### Backend checkpoint capability

A backend supports either (or both) of two restore paths (see
`src/silisocs/environments/backends/base.py`):

- `provides_checkpoint_state` (class flag): the backend round-trips authoritative
  state via `get_state`/`set_state`, so restore is a direct snapshot apply through
  the default checkpoint loader. **True for every shipped backend.** Setting it
  while inheriting either of `BackendApp`'s no-op state methods is rejected when
  the backend is built — the flag would otherwise assert an authority the methods
  do not deliver, and restore would quietly apply nothing.
- Action-event replay — a restore *mechanism* owned by the `sim.checkpoint.restore`
  strategy, **not** a backend method. The built-in `social_action_event_replay`
  strategy keeps its per-backend event→action mappings in a registry keyed by
  `backend_type` (`runtime/checkpointing/replay_mappers.py`); a backend "supports
  replay" exactly when a mapper is registered for its `backend_type`. Any backend
  routed to replay whose `backend_type` has no mapper fails loudly. The registry
  ships `twitter_like` → `microblog_event_to_replay_action`; `reddit_like` has none
  (no valid microblog mapping) and relies on its snapshot instead.

Every shipped backend self-restores via `set_state`. The SQL backends snapshot
their database; **`mastodon`** can't snapshot its external live server, so its
checkpoint state *is* its action history: `get_state` embeds the logged actions
and `set_state` rebuilds the server by re-running them through a private mapper (as
their original users, in order). It therefore restores through the same default
`set_state` path, no special strategy required:

- **Server reset**: the server must be wiped first (`reset_server_on_setup=true`),
  otherwise replay duplicates the original run's content. Replay logs a warning
  when reset is not configured.
- **Toot-id remapping**: re-creating a post yields a *new* server toot id, so
  `like`/`boost`/`reply` events are remapped from their logged (pre-resume) id to
  the new one; an unmapped reference is skipped.
- **Caveat**: replay re-posts to the live server and reproduces a *similar*, not
  byte-identical, state (timestamps, ordering, federation differ). Set
  `perform_operations=true` for the actions to actually reach the server.

A **custom** non-snapshot backend (`provides_checkpoint_state=False`) can either
do the same (implement `get_state`/`set_state`) or call
`register_replay_mapper(backend_type, mapper)` (before the resume runs) to let the
built-in strategy replay its logged events. A backend that supports neither fails
loudly; supply a custom restore strategy:

```yaml
sim:
  checkpoint:
    restore:
      class_path: my_pkg.MyRestore   # subclass of CheckpointRestoreStrategy
      params: {}
```

This is an ordinary [slot](#slots); the class must subclass
`silisocs.runtime.checkpointing.restore.CheckpointRestoreStrategy`.

### Restore robustness

- **Identity reconciliation**: restoring a checkpoint object onto a runtime
  object of a different `class_path`/`compat` is rejected, and an object that
  saved non-empty state but only inherits the no-op `set_state()` raises rather
  than silently dropping that state. Objects present in the runtime but absent
  from the checkpoint are left freshly initialized and logged as a warning.
- **Recsys self-heal**: after restore the in-memory recsys engine is rebuilt
  empty; the recommendation-update component reconciles configured types against
  the backend's live `recsys_active_types()` and lazily re-initializes them on
  the first post-resume update, so algorithmic feeds resume automatically.
- **Flow scheduling**: the agent->flow tag assignment is re-materialized from the
  resume-time config (not the checkpoint); a divergence from the checkpointed
  agent->flow fingerprint is logged as a warning, since it can mis-route replay.
  The flow->GM routing topology (flow chains) is engine config, re-materialized
  from config and not part of the GM checkpoint at all.

### Multi-GM layout

When more than one Game Master is configured, each GM's backend database and
`action_events.jsonl` are isolated under a per-GM subdirectory
(`<output>/<gm_name>/...`) so same-type GMs cannot clobber one another on
checkpoint restore. Single-GM runs keep the flat layout. Two GMs that would
resolve to the same backend database path are rejected at build time.

**Per-GM restore**: the authoritative-vs-replay decision is made per game
master, not all-or-nothing: each GM that carries a backend snapshot restores from
it directly, and only the remaining (non-authoritative, e.g. Mastodon) GMs are
handed to the restore strategy. A mixed run (e.g. a `twitter_like` GM and a
`mastodon` GM) restores the snapshot GM from disk while replaying the Mastodon GM.

For multi-GM replay, restore discovers **every** per-GM `action_events.jsonl`
(the same flat-or-per-GM lookup eval uses), so multi-GM resumes locate their
logs. Each event is routed back to the GM that logged it (its `gm_name`) and
mapped to a backend action by that GM's own backend; events owned by an
already-restored (snapshot) GM are skipped.

**Per-GM restore override**: a GM may override the global `sim.checkpoint.restore`
with its own strategy (same schema), for a backend that needs custom loading
logic rather than the default replay/snapshot:

```yaml
env:
  gm_orchestration:
    gms:
      - gm_name: mastodon_gm
        backend: { type: mastodon }      # plus components: { ... }
        restore:
          class_path: my_pkg.MyMastodonRestore   # subclass of CheckpointRestoreStrategy
          params: {}
```

GMs without a `restore` block use the global default. The key is additive: omit
it and multi-GM restore behaves exactly as before. Authoritative (snapshot) GMs
ignore their `restore` override because `set_state` already restored them.

**Evaluation/analysis** read every per-GM `action_events.jsonl` (via
`silisocs.evaluations.action_events.resolve_action_event_files`), so the default
evaluators, activity summary, and Studio cover all game masters, not just a
flat root log.

---

## Mid-Run Interventions

An optional top-level `interventions` schedule fires actions at step boundaries
(after probes measure the pre-intervention world, before the step runs), turning
a "controlled experiment" — swap the recommender at the midpoint, ban an agent,
inject a breaking-news post — into config instead of a manual
checkpoint / edit / resume cycle. Absent = no interventions (default).

```yaml
# top-level (world config root, @package _global_)
interventions:
  - at_step: 5
    actions:
      - kind: set_participation          # persistent
        slot: {built_in: activity_probability, params: {active_probability: 0.1}}
      - kind: ban_agents                 # persistent
        agents: [Alice, Bob]
      - kind: set_recsys                 # persistent (sugar for set_component_params)
        recsys_type: twitter_tfidf
        gm: null                         # null = the single/default GM; a name for multi-GM
      - kind: set_component_params       # persistent (the generic form)
        params: {update_every_n_steps: 3, max_posts: 5}
        gm: null
      - kind: set_turn_policy            # persistent
        slot: {built_in: fixed_count, params: {count: 2}}
        flow: burst_posters              # scope: at most one of flow / gm; neither = global
      - kind: set_router                 # persistent (re-point a flow's branch router)
        flow: choose_platform            # the flow whose branch node to re-point
        slot: {built_in: random, params: {weights: {gm_a: 3, gm_b: 1}}}
      - kind: swap_component             # persistent (stateless components only)
        role: observe                    # observe | next_acting | update
        slot: {built_in: episode_only}
        gm: null
  - at_step: 8
    actions:
      - kind: inject_post                # one-shot (sugar for inject_action)
        author: NewsBot                  # an existing agent / backend user
        text: "BREAKING: ..."
      - kind: inject_action              # one-shot (the generic form)
        agent: Moderator
        action: follow_user              # any backend catalog action name
        args: {target_username: NewsBot}
      - kind: broadcast_observation      # one-shot
        text: "You hear a rumor that ..."
        agents: []                       # empty = every agent
      - kind: unban_agents               # persistent
        agents: [Alice]
```

**Action kinds.** Each is either *persistent* (changes live engine/component
state) or *one-shot* (a single event):

| kind | class | effect |
|------|-------|--------|
| `set_participation` | persistent | rebuild the participation policy from `slot` (keeps any active ban) |
| `ban_agents` / `unban_agents` | persistent | exclude/re-include `agents` from every step's active roster (a *soft* ban — a banned agent still exists in the world, can be mentioned/followed, and still receives `requires_full_roster` updates) |
| `set_component_params` | persistent | retune declared component parameters on a GM (`params` mapping, applied to every component that declares the name; raises if a name lands nowhere) |
| `set_recsys` | persistent | sugar for `set_component_params` with `params: {recsys_type: ...}` — swaps the recommender on a GM's observe + update components (the new type initializes on the next recsys refresh) |
| `set_turn_policy` | persistent | rebuild the turn policy (how many actions a turn takes) from `slot`; scope is `flow` (needs a flow-aware step strategy), `gm`, or neither = the global default. Batch-time precedence is unchanged: per-flow > per-GM > global |
| `set_router` | persistent | re-point the router at `flow`'s branch node with `slot` (a stateless plain callable, rebuilt via `build_router`); needs a `multi_gm*` step strategy whose chain for `flow` contains a branch |
| `swap_component` | persistent | hot-swap a **stateless** GM component — `role` ∈ `observe` / `next_acting` / `update` — with `slot` (rebuilt with the GM's live wiring); refused when the outgoing OR incoming component has non-empty `get_state()` (retune stateful components with `set_component_params` instead) |
| `inject_action` | one-shot | invoke any backend catalog action as `agent` (a typed tool call — `action` name + `args` — resolved through the GM's resolve component, which validates against the catalog and injects the runtime actor) |
| `inject_post` | one-shot | sugar for `inject_action`: post `text` as `author` via the backend's canonical post action (the same per-backend mapping the seed-post initializer uses; `action_mapping` extends it for custom backends, `subreddit` targets reddit-likes) |
| `broadcast_observation` | one-shot | deliver `text` to targeted agents' memory (`agents: []` = all) |
| `custom` | declared by the class | `class_path` to an `InterventionHandler` subclass, built with `params` |

**Component tunables.** `set_component_params` is the extension seam for
mid-run retuning: a component opts a parameter in by listing it in its
class-level `runtime_tunable` frozenset (`BaseComponent.set_params` then routes
each name through a `set_<name>()` setter when the component defines one,
otherwise assigns the same-named attribute). The shipped social-media
components declare `recsys_type` + `timeline_mode` (observe) and
`recsys_type` / `update_every_n_steps` / `lazy` / `max_posts` (update); a custom component —
recsys or otherwise — declares its own names and is immediately addressable
from config, no new intervention kind required. Only declare parameters that
are safe to reassign at a step boundary.

**Turn policies, routers, and stateless components.** `set_turn_policy` rebuilds
a turn policy from its `slot` and re-points the map the scheduler reads each step
(global, per-`gm`, or per-`flow`); turn policies carry no checkpoint state, so
the swap is replay-safe (per-`flow` scope needs a flow-aware step strategy —
`flow`/`multi_gm*`). `set_router` similarly re-points a flow's branch `router` —
also a stateless plain callable rebuilt from a slot — in the step strategy's flow
chain (needs a `multi_gm*` strategy with a branch node in that flow's chain).
`flow` targets on both are preflight-validated against the flows statically
declared in config (class `flow_tag`s, `flow_order`, `agent_to_flow`,
`flow_to_gms`), so a typo'd flow fails at config validation rather than mid-run;
when no flows are declared (e.g. a custom step strategy) the check defers to
fire time, mirroring the `gm` target rule.
`swap_component` replaces a whole GM component (`observe` /
`next_acting` / `update`) from its `slot`, but ONLY when both the outgoing and
the freshly built incoming component are stateless (empty `get_state()`) — a
stateful component (e.g. the recsys updater, or a `fixed_order` next-acting
cursor) is retuned with `set_component_params`, never replaced. The GM's
`rebuild_component` seam reuses the same per-role factory and live wiring as
first construction, and a checkpoint records each stateful component's class so a
resume after a swap skips (rather than blindly applies) state saved for a
different class. `resolve` / `action_prompt` (a pair coupled to the GM's
tool-calling mode) and `initialize` (meaningless mid-run) are out of scope.

**Resume semantics.** Persistent actions with `at_step < start_step` are
*replayed* on resume (their effect isn't in the checkpoint); one-shot events are
never replayed (their effect — a post, an observation — is already in restored
backend/agent state). Fired-ness is a pure function of `(schedule, step)`, so
resuming reproduces the exact intervention state with no checkpoint-schema
change. Interventions are recorded in `sim_metrics.json`
(`meta.interventions` + the `interventions_fired` counter).

**Scope.** Only the whitelisted kinds above are hot-swappable; model/agent
construction, GM topology, backend schema, the executor / step-strategy / loop
policies, and the `resolve`/`action_prompt` pair are not mid-run mutable.
Injections (`inject_action` / `inject_post`) emit typed tool calls, so — like
seed-post initialization, which shares the path — they require the target GM's
resolve component to execute tool calls (effective `tool_calling: single|multi`,
the default); on a `tool_calling: none` GM the text-parsing resolve records a
parse failure instead of executing the injection. Note that
any intervention that changes what agents see also changes downstream LLM output,
so a live-LLM run is only as reproducible as the model it calls (as with the
`agent_choice` router).

---

## Output Configuration

Output paths are controlled by Hydra in `experiment.yaml`:

```yaml
hydra:
  job:
    name: ${scenario_name}_${now:%Y-%m-%d_%H-%M-%S}
  run:
    dir: outputs/${scenario_name}/${jobname_format}
  output_subdir: configs/${jobname_format}
```

The simulation writes artifacts into the directory resolved by `hydra.run.dir` +
`hydra.job.name` — that is,
`outputs/{scenario_name}/{jobname_format}/{scenario_name}_{timestamp}/`. See
[Output Structure](#output-structure) for the tree and
[Usage Overview: Output](usage.md#output) for the complete list of output files.

**`output_dir` takes precedence over all of it.** The runner resolves the run
directory in this order:

1. An explicit `run_simulation(cfg, output_dir=...)` argument (the Python API).
2. A **non-empty** `output_dir` run parameter — used verbatim (relative paths
   are made absolute against the working directory). `jobname_format`,
   `hydra.job.name`, and the timestamp are all bypassed, so two runs pointed at
   the same `output_dir` write into the same directory.
3. Otherwise, Hydra's per-run path above.

This is why every scenario ships `output_dir: ""`: the empty value is what
selects behavior 3. Set a value only when you want to name the directory
yourself — which is exactly what `silisocs-study` does for each run in a grid
(`++output_dir=...`), giving studies their own deterministic, timestamp-free
layout (see [Study Schema](study_schema.md#directory-layout)). The resolved
directory is stamped back onto the config as `output_dir` and printed as
`Output directory: ...` at startup.

---

## Advanced: Multi-GM Orchestration

See [Multi-GM Architecture](multi_gm_architecture.md) for configuring multiple
game masters, flow-based scheduling, and per-flow component routing.

Use `sim.engine.step.built_in: multi_gm` with `env.gm_orchestration.gms` when
one run needs multiple Game Masters or backends. Every orchestrated GM must
declare its own `backend` and `components` blocks; those nested blocks use the
same strict key surface as `env.gm.backend` and `env.gm.components`.

`env.gm_orchestration.flow_bindings.flow_to_gms` maps flow names to GM chains.
Each chain must reference known GMs, contain at least one GM, avoid duplicate GM
names, and follow increasing GM `sequence` values when more than one GM is in
the chain. Flows without an explicit binding fall back to the earliest-sequence
GM. At runtime, every GM updates once at the start of each step before flow
routing and actor selection.

`sim.engine.step.params.agent_to_flow` is validated against final Agent names
and materialized before runtime. The Engine and Game Masters both read the same
final `agent_flow_tags`, so component routing cannot drift from Engine flow
scheduling.

`sim.engine.step.params.flow_order` is the serial prefix these strategies (and
`flow`) run first. It distinguishes ABSENT from EMPTY:

- Omitted (or `null`) → the default prefix `[fixed_pre, default]`.
- `flow_order: []` → **no serial prefix**: every flow runs in the concurrent /
  staged group. This is honored exactly as written; it is not collapsed back to
  the default.
- Anything that is not a list (e.g. `flow_order: default`) fails at engine build.

`sim.engine.step.built_in` selects how flow chains traverse their GMs, via three
`multi_gm*` step strategies:

- `multi_gm` (DEFAULT, concurrent): Flows run as independent pipelines through
  their GM chains. Distinct flows advance concurrently and a flow's next hop
  starts as soon as its own previous hop completes — turns serialize ONLY when
  two flows touch the same GM at the same time (enforced by the engine's existing
  per-GM lock). Flows listed in `flow_order` run first as a strict serial prefix,
  preserving declared precedence such as seed-then-act (`fixed_pre` before
  `default`); every other flow runs as the concurrent group. A single agent's
  own chain hops always stay serial, since each hop observes the prior hop's
  resolution.
- `multi_gm_serial` (legacy row-major): each flow runs its full GM chain to
  completion before the next flow, one batch at a time, in a deterministic
  flow-by-flow order.
- `multi_gm_staged` (column-major with a global per-stage barrier): `flow_order`
  flows run first as a serial prefix (same as `multi_gm`); then every remaining
  flow advances ONE STAGE AT A TIME — all flows' stage-N hops run concurrently,
  and stage N+1 does NOT begin until ALL of stage N's turns finish. A flow's
  chain may contain an empty slot (a `null` entry in `flow_to_gms`) so it idles
  at that stage and resumes at its next non-null hop, letting flows with
  different chain shapes stay stage-aligned (see
  [Advanced: Multi-GM Orchestration](#advanced-multi-gm-orchestration)). The
  barrier can leave the worker pool idle at stage tails (a fast flow waits for
  slow flows); use `multi_gm` when you don't need stage alignment.

`multi_gm` is the default; its behavior is unchanged from prior releases (it was
formerly selected by the now-removed `sim.engine.step.params.chain_execution:
concurrent`, and `multi_gm_serial` was `chain_execution: sequential` — a config
that still sets `chain_execution` now raises a `ValueError` with a migration
hint). Independent of the mode: each GM's `update()` still runs once before any
acting in a step; checkpoint replay is still per-agent-flow-chain; and
`flow_turn_policies` and per-flow component routing still apply at every hop.

#### Branch nodes: routing one flow across alternative GMs

A chain entry may be a **branch node** — `{branch: {router, choices}}` — instead of
a GM name or `null`. At that stage each of the flow's agents is routed by the
configured `router` to exactly one of `choices` (real GM names); agents that pick
the same GM are batched together. The branch is a single chain stage, so the GMs
before and after it still run once on every agent (shared pre/post hops are not
split), and under `multi_gm_staged` the branch occupies one stage column so
alignment is preserved.

```yaml
env:
  gm_orchestration:
    flow_bindings:
      flow_to_gms:
        social_flow:
          - seed_gm
          - branch:
              router: { built_in: random, params: { weights: { twitter_gm: 0.7, reddit_gm: 0.3 } } }
              choices: [twitter_gm, reddit_gm]
          - wrapup_gm        # shared tail — both branches re-converge here
```

The router is a [slot](#slots), like a turn policy. Built-ins:

- `random` (`RandomChoiceRouter`): a weighted random pick, deterministic per
  `(seed, flow, step, agent)` — so a run reproduces and replays identically.
- `agent_choice` (`AgentChoiceRouter`): the **agent itself** picks its GM (see below).

**Custom routers — any callable.** A `class_path` router is the "a custom function
chooses" seam. A router is just a callable
`route(agents, gms, ctx) -> {agent name: chosen gm name}` — no base class, no
registration. It receives the flow's agent objects (call `agent.act(...)` freely),
`gms` (`{gm name: game master}`, one per choice, in config order — read `gm.backend`
freely), and `ctx` (`RouteInfo(flow, step, seed)` for a replay-stable decision), and
returns each agent's chosen GM. `class_path` may point at a plain function (config
`params` are bound as keyword arguments) or a class (built with `params`, instances
callable).

**When it runs.** The engine runs the router when the flow's chain reaches the branch
stage — after the flow's earlier hops have drained, so the router sees live backend
state and may involve the agents. This holds in all three `multi_gm*` traversals
(under `multi_gm_staged`, after the prior stage's barrier). The router call runs
unlocked; only the follow-up per-chosen-GM turn selection is serialized under that
GM's lock; and the engine validates the returned assignment (every agent covered,
every GM one of `choices`). An LLM-driven router is only as reproducible as the model
it calls. Routing calls run serially on the flow's chain driver, outside
`sim.max_concurrent_actions` and `gm_concurrency_caps` — budget one sequential model
call per routed agent per branch stage, and prefer the `random` router for large
flows (see [Multi-GM Architecture](multi_gm_architecture.md) for this and the
concurrent-mode replay caveat).

`agent_choice` params: `prompt` (a template; placeholders `{choices}`, `{flow}`, `{step}`,
`{agent}`) and `on_invalid` (`random` — the default, a replay-stable fallback;
`first`; or `raise`). `on_invalid` covers both an answer naming no choice and a
routing call that raises (provider outage, retry exhaustion), so one agent's
transient model failure aborts the run only under `raise`. Each fallback increments
the `routing_fallbacks` [run-health counter](usage.md#run-health) — the run
continues, but never quietly. It matches the agent's
answer with the shared `match_choice`
helper (exact → case-insensitive → contained-once), which custom routers can import.
Example:

```yaml
        social_flow:
          - branch:
              router:
                built_in: agent_choice
                params:
                  prompt: "You can act on {choices} this round. Reply with exactly one."
                  on_invalid: random
              choices: [twitter_gm, reddit_gm]
```

Constraints (validated at config/engine build): at most one branch per chain; at
least two distinct, known choices; the branch's choice sequences must sit strictly
between its chain neighbours; a branch requires a `multi_gm*` step mode; and a branch
may not sit in a `flow_order` (serial-prefix) flow.

---

## Related

- [Usage Overview](usage.md): End-to-end workflow and output format
- [Building Agents](building_agents.md): Persona pipeline details
- [Environment Backends](backends.md): Generic apps, social platforms, and visualizers
- [Evaluation Probes](probes.md): Probe type reference
- [Multi-GM Architecture](multi_gm_architecture.md): Advanced GM orchestration
