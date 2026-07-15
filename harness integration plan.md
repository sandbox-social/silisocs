# Harness Agents Integration Plan (Hermes + OpenClaw)

Date: 2026-07-10. Builds on `harness agent import analysis.md` and
`moltbook replication analysis.md`. Goal: harness-backed agents as first-class
citizens of the existing architecture — configured through the persona
pipeline, working with ANY backend/GM/engine-policy combination, checkpointed,
probed, and telemetered — with Hermes and OpenClaw as the first two adapters.

## Design pillars (why this stays clean)

1. **Harness agents are just `Agent` subclasses** loaded via the established
   `persona_pipeline.classes.<class>.class_path` path. No engine, scheduler,
   participation, intervention, or checkpoint code changes — those layers are
   already duck-typed.
2. **One new deep module: the Tool Bridge.** A `ToolSurface` built per turn
   from the acting GM's backend (`generate_tool_schemas()` for discovery,
   `invoke_action_with_kwargs()` for execution, existing enabled/excluded and
   flow action filters applied by construction). Both adapters — and any
   future harness — consume only this surface. Backend-agnostic by definition:
   whatever the ActionCatalog exposes, the harness can do; nothing else.
3. **One adapter seam: `HarnessAdapter` (Protocol).** `HarnessAgent` (the
   silisocs-facing class) owns observe-buffering, ActionSpec dispatch, probe
   handling, state, and telemetry; adapters own only "run one harness turn".
   A deterministic `FakeHarnessAdapter` makes the whole integration testable
   with zero external dependencies — the contract tests ARE the public spec.
4. **GM slots, not GM forks.** A new `harness` action_prompt built-in (binds
   the ToolSurface to the acting agent, duck-typed via
   `bind_tool_surface()`) and a new `harness` resolve built-in (acknowledges
   already-executed turns; delegates non-harness outputs to a configurable
   fallback resolve so mixed native+harness populations share one GM).
5. **One model plane: the Model Proxy.** Both harnesses speak OpenAI-
   compatible HTTP to a configurable base_url (Hermes: constructor
   `base_url`/`api_mode`; OpenClaw: `models.providers[].baseUrl`, loopback
   supported). The runtime hosts a local pass-through proxy and points every
   harness agent at it. The proxy forwards request bodies byte-for-byte to
   the upstream provider (preserving harness behavior exactly — we do NOT
   terminate requests into our `sample_*` API, which would distort them) and
   observes everything: provider `usage` -> the same SimMetrics counters /
   `by_phase` buckets / pricing table as native agents (one unified
   `llm_usage`); 429/5xx retries handled with the same backoff + retry-stat
   machinery (adaptive worker cap sees harness traffic); every call logged to
   `prompts_and_responses.jsonl` with agent/episode/phase. Attribution:
   per-agent routing tokens as the harness-side "api_key" + a
   `(agent -> episode, phase)` table registered by HarnessAgent around each
   turn (same pattern as ToolSurface routing). Upstream selection reuses the
   existing per-class model config (`persona_pipeline.classes.<class>.model`)
   — one model-configuration language for native AND harness agents — and a
   scripted upstream gives DETERMINISTIC integration tests of real harness
   binaries with no API keys. Real provider keys live only in the proxy;
   harness configs/workspaces hold routing tokens, never real credentials.
   v1 scope: OpenAI-compatible protocol only (configure both harnesses to
   chat-completions mode); SSE streaming passed through with
   `stream_options: {include_usage: true}` injected so usage arrives in the
   final chunk.

## Turn flow (target state)

```
engine → GM.run_agent_step(agent)
  observe component      → agent.observe(timeline text)            [unchanged]
  harness action_prompt  → builds ActionSpec (call-to-action text)
                           + agent.bind_tool_surface(ToolSurface(backend, agent))
  agent.act(spec)        → HarnessAgent → adapter.run_turn(...)
                             harness loop: model → tool → ToolSurface.execute()
                             → backend.invoke_action_with_kwargs() → real result
                             → model → ... → done
                           returns ActionOutput(
                             output_type=TEXT, text=<final response>,
                             structured={"harness_turn": {executed, usage, finished}})
  harness resolve        → sees structured["harness_turn"] → records summary,
                           returns FINISHED signal when turn says so;
                           non-harness ActionOutput → delegate to fallback resolve
```

Action events land in `action_events.jsonl` exactly as today (logging lives at
the backend layer). Per-call harness detail goes to a new
`harness_events.jsonl`.

## Phase 0 — Decision gate: Hermes dependency strategy (small spike)

Hermes exact-pins `openai==2.24.0`; we pin `openai>=1.35.9,<2.0.0`.

- [ ] Spike: attempt `uv add --optional hermes hermes-agent` — enumerate the
      full conflict set (Hermes pins ALL deps exactly).
- [ ] Scope our openai 2.x migration: usage is concentrated in
      `runtime/language_models/openai.py` + `OpenAICompatibleLanguageModel`
      (chat.completions + tools APIs are stable across 1.x→2.x; verify
      retry/exception class names). If small → migrate, unlock in-process.
- [ ] Fallback design (kept regardless, as the escape hatch): a subprocess
      worker (`hermes_worker.py`) speaking JSON-lines over stdio from its own
      venv; the adapter API is identical either way (`HermesAdapter` hides it).
- Deliverable: decision recorded here; Phase 2 implements whichever mode won.
- OpenClaw has NO such gate (out-of-process by design; zero Python deps
  beyond `websockets` + an MCP server lib, or hand-rolled stdio MCP).

## Phase 1 — Core: bridge, base agent, GM slots, contract tests

The "works with any backend/GM" guarantee lands entirely in this phase, using
only the fake adapter. Hermes/OpenClaw become drop-ins afterwards.

New package `src/silisocs/agents/harness/`:

- [ ] `types.py` — `ExecutedToolCall(name, arguments, result, ok, error)`,
      `HarnessTurnResult(final_text, executed, usage, finished, raw)`,
      `HarnessProbeResult(text)`.
- [ ] `bridge.py` — `ToolSurface`:
      - `schemas()` → the backend's filtered tool schemas (reuse
        `generate_tool_schemas()`; flow action filters applied via the same
        params the resolve component uses today).
      - `execute(name, args) -> ExecutedToolCall` — validate name against the
        catalog + filters, call `backend.invoke_action_with_kwargs(name,
        {agent_name-injected args})`, catch backend errors into an
        error-result (`ok=False`, machine-readable hint text) so the harness
        loop reacts instead of crashing; append every call to the harness
        event log; increment existing failure counters on errors.
      - Never exposes `FINISHED`-adjacent internals; turn termination is the
        harness's own loop ending.
- [ ] `base.py` — `HarnessAgent(Agent)`:
      - ctor: `(model, *, name, adapter, probe_mode="model", persona="")` —
        `model` is the standard builder-injected LanguageModel, used for the
        probe path and nothing else.
      - `observe()` buffers; `bind_tool_surface(surface)` stores the current
        turn's surface (cleared after act).
      - `act(spec)`: CHOICE/structured probe specs → probe path
        (`probe_mode: model` = answer via our LanguageModel with a
        harness-state summary as context — fully telemetered; `probe_mode:
        harness` = `adapter.run_probe()`, usage mapped in). TEXT specs with a
        bound surface → `adapter.run_turn(name, buffered_obs, spec.prompt,
        surface)` → ActionOutput as in the flow diagram. TEXT specs with NO
        bound surface (e.g. seed-post initialization) → adapter one-shot text
        turn with no tools.
      - `act_async` override deferring to `asyncio.to_thread` initially
        (adapters are sync); loop-native path is a later optimization.
      - `get_state()`/`set_state()`: observation buffer + `adapter.snapshot()`
        / `adapter.restore()` (both harnesses are snapshot-restored, never
        replay-restored — matches the existing checkpoint contract).
- [ ] `fake.py` — `FakeHarnessAdapter`: scripted, deterministic
      (post-then-like behavior mirroring `TutorialBehavior`), records calls;
      used by all contract tests and available to users as a dry-run harness.

GM component slots (`environments/gm/components/`):

- [ ] `action_prompt.py`: `HarnessActionPromptComponent` (`built_in:
      harness`) — standard call-to-action text; binds the ToolSurface when the
      acting agent exposes `bind_tool_surface` (duck-typed; native agents in
      the same GM are untouched).
- [ ] `resolve.py`: `HarnessResolveComponent` (`built_in: harness`, params:
      `fallback: {built_in|class_path, params}` defaulting to
      `parsed_action`) — if `action.structured` carries `harness_turn`:
      record executed-count/usage into SimMetrics, return the FINISHED signal
      when `finished` (never blocks FINISHED); else delegate to the built
      fallback component. Registered in the component factory; per-GM resolve
      compatibility validation extended (a `harness` resolve is valid under
      any effective tool-calling mode).
- [ ] Config validation preflight (`configuration/validation.py`): if any
      persona class resolves to a `HarnessAgent` subclass, every GM that can
      own that class's flow must use `resolve.built_in: harness` — error with
      a migration hint otherwise (mirrors existing per-GM resolve checks).

Telemetry + artifacts:

- [ ] `harness_events.jsonl` (run root; per-GM dir in multi-GM runs, matching
      action-event isolation): typed rows `tool_requested/tool_executed/
      tool_failed/turn_completed/probe_answered` with agent/episode/gm.
      Resolver in `evaluations/action_events.py`
      (`resolve_harness_event_files`), exposed on `RunArtifact`
      (`iter_harness_events()`), listed in the run-manifest artifact index.
- [ ] `proxy.py` — the Model Proxy (design pillar 5): localhost HTTP server
      (thread in the runner process; port auto-allocated; loopback-only bind)
      exposing `/v1/chat/completions` (+ `/v1/models` stub). Pass-through
      forwarding with streaming; per-agent routing tokens; upstream resolved
      per agent from the effective per-class model config (same dedup as
      native models); retries via the shared backoff/telemetry helpers; usage
      recorded into the SAME `llm_usage` counters/by_phase/pricing as native
      agents (no separate `harness_usage` block); calls logged to
      `prompts_and_responses.jsonl` with agent/episode/phase. A `scripted`
      upstream routes to `ScriptedLanguageModel` for deterministic no-key
      tests. Lifecycle owned by session runtime (started lazily when any
      harness class is configured, stopped at teardown).
- [ ] Proxy tests: token->agent attribution, phase/episode tagging, usage
      aggregation parity with a native-agent run, retry counting on injected
      429s, streaming pass-through, scripted upstream determinism.

Contract tests (`tests/test_harness_agent_contract.py`, fake adapter only):

- [ ] E2E scripted run: harness class on `twitter_like` — actions land in the
      backend and `action_events.jsonl`; FINISHED honored; `harness_events`
      written; manifest lists it.
- [ ] Same scenario on `reddit_like` (proves backend-agnosticism).
- [ ] Mixed population: harness class + native class under ONE GM with
      `resolve: harness` + fallback — both act correctly in one step.
- [ ] Multi-GM flow chain with a harness flow (surface rebinds per GM hop —
      the agent acts on each backend in its chain).
- [ ] Checkpoint save/restore round-trip (adapter snapshot state survives;
      restore refuses on class mismatch as today).
- [ ] Probe deployment to harness agents (both probe modes).
- [ ] Filtered action denied → error result returned to the loop, failure
      counter incremented, run continues.
- [ ] Validation: harness class + non-harness resolve → config error.

## Phase 2 — Hermes adapter

- [ ] Optional extra `silisocs[hermes]` (or the worker-venv route per Phase 0)
      + `doctor` extras row.
- [ ] `hermes.py` — `HermesAdapter(HarnessAdapter)`:
      - One `AIAgent` per silisocs agent: `skip_memory=True`,
        `skip_context_files=True`, `quiet_mode=True`, per-agent `session_id`,
        `ephemeral_system_prompt=persona`, `max_iterations` from params;
        `base_url` = the Model Proxy, `api_key` = the agent's routing token,
        `api_mode` = chat_completions (upstream/model chosen by the per-class
        model config through the proxy).
      - Tool routing: register ONE global `silisocs` toolset at import; its
        handlers dispatch through a `ContextLocal` current-`ToolSurface` set
        by `run_turn` around `run_conversation` (solves the process-global
        registry + thread-safety in one move; same pattern as our model
        runtime context).
      - `snapshot()` = serialized `conversation_history`; `restore()` feeds
        it back via `conversation_history=`.
      - `run_probe()` = `run_conversation` with toolsets disabled,
        `max_iterations=1`, answer-only instruction.
      - Usage from the result dict → `record_external_usage`.
- [ ] `HermesAgent(HarnessAgent)` — thin: builds the adapter from params.
- [ ] Tests: mocked `run_agent` module via `sys.modules` injection (dashboard
      -cli test pattern): construction args, ContextLocal tool routing,
      history snapshot/restore, probe path. Deterministic integration test:
      REAL hermes-agent against the Model Proxy's scripted upstream (no API
      key; skipped when the extra is absent). Optional live test behind
      `HERMES_LIVE=1`.

## Phase 3 — OpenClaw adapter

- [ ] `mcp_server.py` — minimal stdio/HTTP MCP server exposing ToolSurfaces:
      tools named per catalog action; a routing registry keyed by agent name
      maps each MCP session/agent to its currently bound surface (set by
      `run_turn` before dispatching the WS turn, cleared after). Executed
      calls recorded server-side (authoritative — it is our server).
- [ ] `gateway.py` — per-run gateway lifecycle service (started lazily by the
      first OpenClaw agent, shut down by session teardown): writes
      `openclaw.json` (agents.list from persona pipeline, `tools.deny` all
      built-ins, `mcp.servers` → our MCP server, heartbeat/cron disabled,
      `models.providers[].baseUrl` → the Model Proxy with per-agent routing
      tokens, so real provider keys never enter the OpenClaw state dir),
      `OPENCLAW_STATE_DIR` under the run output dir, spawns/monitors the
      gateway process. Config under `sim.harness.openclaw: {binary, port}`;
      model/provider selection comes from the per-class model config via the
      proxy.
- [ ] Workspace seeding: persona pipeline params → generated `SOUL.md` /
      `MEMORY.md` / `AGENTS.md` per agent workspace (the memory-seeding seam
      from the MoltBook analysis).
- [ ] `openclaw.py` — `OpenClawAdapter`: WS JSON-RPC client, ASYNC-NATIVE
      (`run_turn_async` awaiting `agent.wait` — loop-native under the asyncio
      executor; sync `run_turn` wrapper as the required floor);
      `run_turn` = compose buffered observations + call-to-action into one
      message → `agent` RPC → `agent.wait` → final snapshot; executed calls
      collected from the MCP routing registry. `snapshot()` = bounded
      per-agent workspace + session-transcript file contents; `restore()`
      rewrites them before gateway start. `HarnessAgent.act_async` defers to
      `adapter.run_turn_async` when the adapter provides it, else
      `asyncio.to_thread` (the Hermes path).
- [ ] Tests: MCP server unit tests (dispatch, routing registry, error
      results); gateway config generation tests; adapter tests against a fake
      WS server. Integration test skipped unless `SILISOCS_OPENCLAW_BIN` set.
- [ ] `doctor`: report Node/openclaw binary presence when
      `sim.harness.openclaw` is configured.

## Phase 4 — Docs + polish

- [ ] `docs/harness_agents.md` (new page + properdocs nav): concepts, config
      recipes (single-GM, mixed population, multi-GM), probe modes,
      checkpoint semantics, determinism caveat (snapshot-restore only),
      cost warnings.
- [ ] AGENTS.md §5 addition (harness agents + adapter seam); §4.5 unaffected.
- [ ] `docs/configuration.md`: `harness` action_prompt/resolve slots,
      `sim.harness.*`, probe_mode; config_reference regenerates itself.
- [ ] `docs/building_agents.md`: "wrap your own harness" via HarnessAdapter.
- [ ] Dashboard (optional, small): Results tab shows harness turn/usage
      counts from the manifest.

## Explicit non-goals for this pass

- No event-driven engine (harness agents run on the round-based clock).
- No skills/marketplace modeling, no MoltBook facade (separate pass, sits on
  top of this one).
- No loop-native `act_async` for HERMES (its `run_conversation` is a blocking
  sync API — one instance per thread by contract; it rides the asyncio
  executor's `asyncio.to_thread` helper pool). OpenClaw is the opposite:
  its adapter is WS I/O, so Phase 3 builds it async-native (`act_async`
  awaiting `agent.wait`; sync `act()` wrapper as the required floor).
  Guidance to document: harness turns are long (multi-call tool loops), so
  large harness populations should prefer `sim.engine.executor: asyncio`;
  `max_concurrent_actions`/`gm_concurrency_caps` bound in-flight harness
  turns as usual.
- No live-internet tools — surfaces expose only backend catalogs.

## Risks / mitigations

| Risk | Mitigation |
|---|---|
| Hermes dep conflict unresolved | Worker-subprocess mode is a first-class design, not a hack; decided at Phase 0 gate |
| Hermes global registry cross-talk between agents | ContextLocal surface routing + contract test with two concurrent agents |
| OpenClaw gateway flakiness in CI | All CI tests use fakes; live integration behind env flags |
| Turn latency (harness loop = many model calls) | `max_iterations` param; per-GM concurrency caps already exist; document cost |
| Probe answers not reflecting harness state in `probe_mode: model` | State-summary context injection; `probe_mode: harness` for fidelity studies |
| Checkpoint size (OpenClaw workspaces) | Bounded snapshot (size cap + warning); sharded save layout handles large blobs |
| Mixed-GM resolve misconfig | Preflight validation with migration-hint errors |
| Harness configured in a non-OpenAI api_mode bypasses the proxy | Adapters/gateway config always pin chat-completions mode; preflight rejects overrides |
| SSE streaming edge cases through the proxy | Pass-through with `include_usage` injection; streaming covered in proxy tests; non-streaming fallback documented |

## Sequencing & effort

Phase 0 (½ session) → Phase 1 (~2 sessions, the bulk of new tests) →
Phase 2 (~1) → Phase 3 (~2) → Phase 4 (~½). Each phase lands as its own
commit(s) on a feature branch off the current one after it merges; full gate
(pre-commit + suite) per phase; committing only on explicit request, as
always.
