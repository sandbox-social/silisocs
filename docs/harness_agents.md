# Harness Agents

!!! warning "Experimental"
    Harness agents are a new, evolving capability. The deterministic core (Tool Bridge,
    agent/probe/checkpoint contract, Model Proxy, zero-config GM integration) is tested
    with the dependency-free `FakeHarnessAgent`, but **live Hermes/OpenClaw runs are
    opt-in and require external runtimes** (see [Installing the harnesses](#installing-the-harnesses)).
    The config surface and internals may change.

Harness agents let a **real agent harness** — [Hermes Agent](https://github.com/NousResearch/hermes)
or [OpenClaw](https://github.com/) — act as a first-class silisocs agent. Instead of the
native one-model-call-per-turn agent, a harness runs its *own* agentic loop inside one
turn (model → tool → model → … until it finishes), executing tools against **any**
silisocs backend through the Tool Bridge. They are configured through the ordinary
persona pipeline, work with any backend / game master / engine policy, are checkpointed,
probed, and telemetered — exactly like native agents.

!!! note "When to use a harness agent"
    Use a harness agent to study the *actual deployed artifact* (a real Hermes/OpenClaw
    agent, with its own memory and tool loop) inside a controlled environment — e.g. an
    agent-only social network. For scale and determinism, the native `NativeAgent`
    remains the right tool. Both consume the same backends and catalogs, so you can mix
    them in one run.

## How a harness turn works

A harness turn inverts where the loop lives. For a native agent, our turn policy owns the
loop (`open_ended` calls `act()` repeatedly). For a harness agent, the **harness** owns
the loop inside one `act()` call:

```
engine → GM.run_agent_step(agent)
  observe            → agent.observe(timeline text)          [unchanged]
  harness prompt     → ActionSpec(call-to-action) + a per-turn ToolSurface
  agent.act(spec)    → adapter.run_turn(...)
                         harness loop: model → tool → ToolSurface.execute()
                                       → backend.invoke_action_with_kwargs() → real result
                                       → model → … → done   (bounded by max_iterations)
                       returns ActionOutput(text=<final message>,
                                            structured={"harness_turn": {...}})
  harness resolve    → records the turn summary; signals completion
```

Consequences:

- The harness's `max_iterations` supersedes our turn policy; for a harness class the
  effective turn policy is `single_action` around one complete harness run.
- Every inner tool call still passes through the **Tool Bridge** individually, which
  forwards to the backend's own `invoke_action_with_kwargs` — so the backend's
  `enabled_actions`/`excluded_actions` catalog restriction, actor-argument injection,
  `action_events.jsonl` logging, and failure counters all behave exactly as for a native
  turn. (The Tool Bridge relies on the backend's catalog filtering; per-flow
  `flow_action_filters`, a native-resolve feature, do not apply to harness surfaces.)
- Every inner model call passes through the **Model Proxy**, so token usage, retries, and
  prompt logs land in the same `llm_usage` / `prompts_and_responses.jsonl` as native
  agents.

## Configuration

**Zero extra configuration.** Point a persona class at a harness agent class — that's it.
Harness agents work with the **default game master** and any resolve/action-prompt
components; no `harness` built-ins, no special GM config:

```yaml
# agents/default.yaml (@package agents)
persona_pipeline:
  classes:
    user:
      count: ${num_agents}
      class_path: silisocs.agents.harness.fake.FakeHarnessAgent   # or hermes.HermesAgent / openclaw.OpenClawAgent
      params:
        probe_mode: model      # "model" (default) or "harness"
```

How it works without any GM config:

- The **default action-prompt component** binds the per-turn Tool Bridge into the action
  spec for any acting agent that declares `wants_tool_surface` (harness agents do; native
  agents don't, so existing scenarios are byte-for-byte unchanged), regardless of the
  GM's tool-calling mode.
- A harness turn's `ActionOutput` is **self-describing** (it carries a `harness_turn`
  payload), so the shared resolve base records it uniformly — **any** resolve
  (`parsed_action`, `tool_calling`, …) handles harness output. No harness resolve
  component, no fallback wiring, no validation.

### Available harness agents

| `class_path` | Harness | Notes |
|---|---|---|
| `silisocs.agents.harness.fake.FakeHarnessAgent` | none (deterministic) | Dependency-free reference / dry run. Reads the feed then posts. |
| `silisocs.agents.harness.hermes.HermesAgent` | Hermes Agent (in-process) | Requires the `hermes` install (see below). |
| `silisocs.agents.harness.openclaw.OpenClawAgent` | OpenClaw (out-of-process) | Requires Node + the `openclaw` binary. |

The persona-pipeline fields (`context`, `bio`, `goal`, `style`, memories, …) are folded
into the harness's persona / `SOUL.md` — the same declarative data native agents use.

### Mixed native + harness populations

One game master hosts both harness and native agents with no special config: the shared
resolve base records harness output (self-describing) and passes native output through to
the normal resolve, and the default action-prompt gives each agent the right spec (a Tool
Bridge for harness agents, the standard prompt/tool-schemas for native agents).

### Multi-GM chains

The Tool Bridge is rebuilt per turn from the acting game master's backend, so a harness
agent hopping through a multi-GM flow chain acts on each backend in its chain
automatically — no extra configuration.

## Probes

Harness agents answer evaluation probes two ways, selected by `params.probe_mode`:

- **`model`** (default): the probe is answered through the agent's own `LanguageModel`
  with a summary of the harness's state as context — fully telemetered, deterministic
  with a scripted model.
- **`harness`**: the probe is answered by the harness itself (`run_probe`) — higher
  fidelity for studies that care about the harness's actual disposition, at the cost of
  another harness call.

## Checkpoints & determinism

Harness agents are **snapshot-restored, never replay-restored** — neither harness ships a
determinism/replay story, so their inner loops are treated as stochastic. This fits the
existing per-object checkpoint contract:

- Hermes: the serialized `conversation_history` round-trips through `get_state`/`set_state`.
- OpenClaw: a bounded snapshot of the agent's workspace files (`SOUL.md`, `MEMORY.md`, …).

Resume restores each harness agent's state directly from its checkpoint block.

## Telemetry — the Model Proxy

Both harnesses speak OpenAI-compatible HTTP to a configurable `base_url`. The runtime
starts a **loopback Model Proxy** (a thread in the runner process, loopback-only) and
points every harness agent at it. The proxy forwards each request byte-for-byte to the
upstream provider — preserving the harness's exact request — and observes everything:

- provider `usage` → the same `llm_usage` counters / `by_phase` buckets / pricing as
  native agents (one unified summary);
- every call logged to `prompts_and_responses.jsonl` with agent / phase attribution;
- per-agent routing tokens as the harness-side "api key", so **real provider keys live
  only in the proxy** — harness configs and workspaces never hold real credentials.

The upstream is resolved from the same per-class model config (`persona_pipeline.classes.<class>.model`)
native agents use, so one model-configuration language covers both. A scripted upstream
gives deterministic, no-key integration tests.

Per-call harness detail (each tool request / execution / failure, and per-turn summaries)
is written to a new **`harness_events.jsonl`** (per-GM in multi-GM runs), listed in the
run manifest and exposed on `RunArtifact.iter_harness_events()`.

!!! warning "Cost"
    A harness turn is many model calls (its whole tool loop), so harness populations are
    far more expensive than native ones. Bound cost with the harness's `max_iterations`,
    `sim.max_concurrent_actions`, and per-GM `gm_concurrency_caps`. For large harness
    populations prefer `sim.engine.executor: asyncio`.

## Installing the harnesses

### Hermes (in-process)

Hermes is pure Python but **exact-pins `openai==2.24.0`**, which conflicts with the
silisocs `openai<2.0.0` pin. Install it in an **isolated environment** (or after a future
openai 2.x migration):

```bash
# In a separate venv / container that imports silisocs and hermes-agent together.
pip install silisocs hermes-agent
```

`silisocs doctor` reports whether `run_agent` (Hermes) is importable.

### OpenClaw (out-of-process)

OpenClaw is Node/TypeScript. Install Node and the `openclaw` binary; the runtime writes
an `openclaw.json` (agents list, all real-world tools denied, our MCP tool server mounted,
model provider pointed at the Model Proxy, heartbeat/cron disabled) and drives each turn
over the gateway's WebSocket API. `silisocs doctor` reports Node + `openclaw` presence.

!!! note "Live-integration boundary"
    The deterministic core (Tool Bridge, agent/probe/checkpoint contract, Model Proxy, GM
    slots, gateway config generation, MCP tool routing) is fully unit-tested with the
    dependency-free `FakeHarnessAgent`. Live runs against real Hermes / OpenClaw are
    opt-in (they need the external runtimes) and covered by integration tests behind
    `HERMES_LIVE=1` / `SILISOCS_OPENCLAW_BIN`.

## Writing your own harness adapter

See [Building Agents → Wrapping your own harness](building_agents.md#wrapping-your-own-harness-experimental).
