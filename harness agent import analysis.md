# Importing a Real Agent Harness (OpenClaw / Hermes Agent) into SiliSocS

Date: 2026-07-10. Ground truth verified against official docs + source (links
inline). Question: can we *import* an OpenClaw or Hermes agent and slot it into
our `Agent` interface so it acts in our designated environments — instead of
reimplementing a harness natively?

## Executive answer

- **Hermes Agent: YES, importable in-process** — pure Python, MIT, on PyPI
  (`hermes-agent`), with a documented embed path (`from run_agent import
  AIAgent`; `agent.run_conversation(msg, conversation_history=...)`). An
  adapter implementing our `Agent` ABC is ~30–50 lines. **One hard blocker
  today: dependency conflict** — Hermes exact-pins `openai==2.24.0`; silisocs
  pins `openai>=1.35.9,<2.0.0`. Co-install requires our openai 2.x migration,
  or an isolated venv + subprocess boundary.
- **OpenClaw: NO in-process, YES out-of-process** — it is Node/TypeScript
  (MIT), so it cannot be imported into Python. But it has clean programmatic
  seams: a one-shot CLI (`openclaw agent --local --json`, no gateway), a
  gateway WebSocket JSON-RPC API (`sessions.send`, `agent` + `agent.wait`,
  streamed tool events) that hosts many isolated agents in one daemon, and
  native MCP support. Integration = run one gateway per simulation, drive
  turns over WebSocket, and expose our backend as the agent's tool surface.
- **The harness loop aligns with our existing turn loop.** SiliSocS already
  runs act -> execute -> observe repeatedly inside one step (the `open_ended`
  turn policy); a harness agent runs the same loop internally — its tool
  executor plays resolve, its tool results play observations. The bridge is
  therefore direct: **make our backend's filtered ActionCatalog the harness's
  tool surface** (registered tool handlers for Hermes; a Python MCP server for
  OpenClaw), with handlers forwarding to `backend.invoke_action_with_kwargs()`
  and returning the REAL result into the harness loop. Deferred/recording
  execution is NOT viable: read tools (e.g. timeline retrieval) must return
  real state mid-loop or the harness reasons over fabricated data. The GM side
  becomes a trivial `harness` resolve component (act() returns a summary of
  what already executed + FINISHED). Resolve's guarantees relocate rather than
  disappear: action filters apply by construction (only the filtered catalog
  is exposed as schemas), arg validation happens per call in the bridge (a
  rejected call returns an error result the harness reacts to), and action
  events still land in `action_events.jsonl` because logging lives at the
  backend layer. Honest residue: harness model calls bypass our
  `LanguageModel`, so retry/usage/cost telemetry must be mapped in from the
  harness's own stats; and the harness's `max_iterations` supersedes our turn
  policy for those agents (ours degrades to `single_action` around one run).
- **Both break replay determinism.** Neither ships a determinism/replay story;
  their inner loops are nondeterministic (concurrency, compaction, timestamps).
  Harness-backed agents must be treated as stochastic: checkpoint by snapshot
  (Hermes: serialized `conversation_history`; OpenClaw: `OPENCLAW_STATE_DIR`
  file copy), never by action replay.

## Hermes Agent (Nous Research) — the low-lift path

Facts (source-verified):

- Python, MIT, PyPI `hermes-agent` (0.18.2); `requires-python >=3.11,<3.14`.
- Core `AIAgent` is explicitly designed for embedding: constructor takes
  `model`, `base_url`, `api_key`, `api_mode` (any OpenAI-compatible endpoint),
  `enabled_toolsets`/`disabled_toolsets`, `skip_memory`, `skip_context_files`,
  `session_id`/`session_db`, `max_iterations`, `ephemeral_system_prompt`
  (persona seam), `request_overrides` (temperature/seed), `quiet_mode`, plus
  per-tool-call observation callbacks (`tool_start_callback`, etc.).
- One `run_conversation()` call = one full agentic turn (inner tool loop runs
  to completion, bounded by `max_iterations`). Multi-turn state is carried
  explicitly by passing `result["messages"]` back in — ideal for an
  externally-owned round loop.
- Batch runner already runs many concurrent `AIAgent` instances in one
  process (one instance per thread; not shareable across threads).
- Tools: process-global registry; `registry.register(..., override=True)` can
  replace built-ins (terminal, web search, files) with simulated
  implementations. MCP also supported. Per-agent differentiation via toolset
  lists + handler-side dispatch on `user_id`/`session_id`.
- Skills: markdown packs under `~/.hermes/skills/`; memory under
  `~/.hermes/memories/` — disable with `skip_memory=True` or relocate via
  `HERMES_HOME` (profiles give full isolation at process granularity).

Adapter sketch (fits our threads executor; asyncio via the default
`asyncio.to_thread` path since Hermes is sync-only):

```python
class HermesAgent(Agent):
    def __init__(self, *, model, name, hermes_model, base_url, api_key, persona=""):
        from run_agent import AIAgent
        self._name, self._history, self._pending = name, [], ""
        self._agent = AIAgent(
            model=hermes_model, base_url=base_url, api_key=api_key,
            quiet_mode=True, skip_memory=True, skip_context_files=True,
            enabled_toolsets=["silisocs"],   # our registered simulated toolset
            max_iterations=4, session_id=f"sim-{name}",
            ephemeral_system_prompt=persona)

    def observe(self, observation): self._pending += "\n" + observation
    def act(self, action_spec):
        result = self._agent.run_conversation(
            self._pending + "\n" + action_spec.call_to_action,
            conversation_history=self._history)
        self._history = result["messages"]; self._pending = ""
        # Tools already executed against the backend via the bridge; return a
        # summary of the turn for the pass-through `harness` resolve component.
        return summarize_executed_turn(result)  # -> ActionOutput (+ FINISHED)
    def get_state(self): return {"history": self._history}
    def set_state(self, state): self._history = list(state.get("history", []))
```

Friction, in order of pain:

1. **`openai==2.24.0` vs our `openai<2.0.0`** — hard resolver conflict. Options:
   (a) migrate silisocs to openai 2.x first (worth checking scope — our usage
   is behind `OpenAICompatibleLanguageModel`); (b) isolated venv + a thin
   JSON-over-stdio worker (loses in-process simplicity); (c) vendor. Hermes
   exact-pins *all* deps (supply-chain stance), so (a) may surface more pins.
2. Flat top-level modules (`run_agent`, `utils`, `cli`, `toolsets`) — real
   shadowing hazard in a shared venv; reinforces the isolated-venv option.
3. Process-global tool registry — all instances share replaced tools (fine for
   one simulated tool surface per run).
4. Import-time global state (`HERMES_HOME`, dotenv at import) — set env before
   import; per-instance home isolation inside one process is unsupported.
5. Weight: each instance carries assistant-grade machinery (prompt builder,
   skills, session store). Fine for tens of agents; for thousands, our native
   `NativeAgent` + a future HarnessAgent remains the scalable path.

## OpenClaw — the out-of-process path

Facts (source-verified):

- Node/TypeScript monorepo, MIT; agent loop lives in `packages/agent-core`
  (derived from badlogic/pi-mono); channels (WhatsApp/Discord/...) are optional
  gateway plugins — none required.
- Programmatic seams: (1) `openclaw agent --local -m "..." --json` = one agent
  turn, no gateway; (2) gateway WS JSON-RPC: `agents.list`, `sessions.send`,
  `agent` RPC returning `runId` + `agent.wait` for the terminal snapshot,
  streamed tool/assistant events; (3) MCP servers configured under
  `mcp.servers` (stdio or HTTP) appear as normal tools.
- Model layer: `models.providers[].baseUrl` accepts any OpenAI-compatible
  endpoint (loopback supported) — mockable.
- Tool policy: per-agent `tools.allow`/`tools.deny` enforced before the model
  call; plugins can't replace built-ins by name — pattern is deny built-in +
  register your own (or MCP).
- State: file-based under `$OPENCLAW_STATE_DIR` (config, per-agent session
  JSONL transcripts, workspaces with SOUL.md/AGENTS.md persona files). One
  gateway hosts N isolated agents; runs serialize per session lane, distinct
  agents run concurrently.
- No pause/step inside a run ("runs to completion or timeout"); no
  determinism/replay (open feature request); heartbeat/cron autonomy must be
  disabled so nothing fires outside our step loop.

Integration shape (recommended by the analysis):

1. One `openclaw gateway` per run, `OPENCLAW_STATE_DIR` inside the run output
   dir (checkpoint = file copy of that dir).
2. `agents.list` entry per persona; workspace persona files generated from our
   persona pipeline; `tools.deny` all real-world tools.
3. A **Python MCP server exposing our backend ActionCatalog** (post, reply,
   like, follow, ...) — handlers execute directly against the sim backend and
   return real results (reads included) into the agent's loop. This is the
   piece that makes the agent "act in our designated environment".
4. An `OpenClawAgent(Agent)` class wrapping a WebSocket client:
   `observe()` -> `sessions.send`; `act()` -> `agent` + `agent.wait`, then the
   recorded MCP tool calls become the turn's `ActionOutput`. Async-native fit
   for our asyncio executor (thousands of in-flight waits are cheap).

Friction: Node runtime + daemon lifecycle managed by the runner; per-turn
token cost (system prompt rebuilt every run: tool list + skills + up to 60KB
bootstrap context); session-lane write locks on rapid re-entry; no determinism.

## What this means for the v2 plan

1. **The shared prerequisite for both is a Tool Bridge, not an agent.** A
   module that exposes a GM/backend ActionCatalog as (a) in-process registered
   tool handlers and (b) an MCP server is the single piece of new
   infrastructure both integrations — and any future harness — need. It also
   directly serves roadmap V2.2 (Tool Module). Build this first.
2. **Order of attack:** Hermes adapter is the cheap first win *if* we resolve
   the openai-pin conflict (check our 2.x migration scope first); otherwise
   OpenClaw-via-MCP is more work but has zero dependency coupling with our
   venv and is closer to "replicating a deployed autonomous agent" (which is
   the stated goal).
3. **Determinism boundary:** harness-backed agents are snapshot-restored,
   never replay-restored — this already fits our per-GM restore contract
   (`provides_checkpoint_state=True` semantics on the agent side).
4. **The native HarnessAgent (V2.1) is complementary, not replaced:** real
   harness imports give *fidelity* (study the actual deployed artifact);
   the native harness gives *scale + determinism*. Both consume the same Tool
   Bridge.
