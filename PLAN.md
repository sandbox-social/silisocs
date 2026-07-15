# PLAN.md — Memory Maturation + Intervention Swap Language

**Scope**: two follow-on workstreams agreed after the Tier 1/2 feature review
(all four Tier 1/2 features are implemented and green; this plan REPLACES the
completed Tier 1/2 plan that previously lived in this file). Every file/line
anchor below was verified on branch `feat/concurrent-chain-execution` with the
staged (uncommitted) Tier 1/2 work applied; full-suite baseline = **881 passed**.

- **Part A — Memory**: make `retrieval` a window+retrieval hybrid; make
  `summarizing` three-tier (summaries → retrieved → recent window). Generous
  defaults, leaning toward bigger windows (maintainer directive).
- **Part B — Interventions**: `set_turn_policy` (swap turn policies mid-run) and
  `swap_component` (swap GM components, **stateless-only guard**).
- **Explicitly excluded** (maintainer-agreed): mid-run swapping of step
  strategies, loop policies, the executor, backends/GM topology, per-agent
  models, and the `resolve`/`action_prompt` pair (coupled to per-GM tool-calling
  compat validation — deferred).

**Sequencing**: A → B1 → B2. Each lands with targeted tests, then the full gate.
Standing rules: `uv run` prefix everywhere; `taskset -c 0-7` on pre-commit and
full-suite runs; never commit without an explicit maintainer request; never
stage `PLAN.md` / `SCALABILITY_PLAN.md`.

---

## Part A — Memory policies (src/silisocs/agents/memory.py)

### Current facts (verified)

| Policy | Store cap | Rendered per turn | Notes |
|---|---|---|---|
| `WindowMemory` | `memory_history=1000` | last `render_count=10` | byte-identical legacy default; golden-tested |
| `RetrievalMemory` | `memory_history=1000` | top `k=10` by lexical overlap | **pure** retrieval — no guaranteed recency window |
| `SummarizingMemory` | `max_memories=200` verbatim + `max_summaries=20` | all summaries + last `render_count=10` | no retrieval tier |

Note on "the original window default was 100": the actual legacy defaults are
**1000 stored / 10 rendered** (memory.py:90). The generosity directive applies
to what is *rendered*; store caps are already generous and stay unchanged.

`NativeAgent` already passes `query=<last observation>` into `render()` and the
`sim.memory` slot already forwards arbitrary `params` through `_build_filtered`
(memory.py:247-275) — **no agent or factory changes are needed in Part A**.

### A1. `RetrievalMemory` → window + retrieval hybrid

Constructor becomes:

```python
def __init__(self, *, memory_history: int = 1000,
             window_count: int = 20, retrieved_count: int = 10) -> None:
```

- `window_count` — verbatim recent memories, ALWAYS rendered (the recency
  floor). `retrieved_count` — relevance hits from the *older* remainder.
  The old `k` param is renamed to `retrieved_count` (branch is unreleased; no
  compat shim — update the tests that use `k=`).
- **Defaults are a floor, not a suggestion**: 20 recent + 10 retrieved per the
  maintainer's "generous, lean bigger" directive. Do not shrink them. The token
  cost (~30 memory lines/turn) is accepted; users tune down via params.
- `render(query)` algorithm:
  1. `window = self._memories[-window_count:]`; `pool = self._memories[:-window_count]`
     (whole list is the window when `window_count == 0` is false — see 4).
  2. No `query`, empty `pool`, or `retrieved_count == 0` → render the last
     `window_count + retrieved_count` memories verbatim (volume-consistent
     fallback, mirrors today's no-query behavior).
  3. Else rank `pool` by `(token-overlap with query, recency)` — extract the
     existing lambda (memory.py:132-137) into a module-level
     `_rank_by_overlap(indexed_pool, query)` helper (A2 reuses it) — take top
     `retrieved_count`, restore chronological order, and render two labeled
     sections, retrieved first, window LAST (freshest context sits nearest the
     action prompt):

     ```
     Relevant past memories:
     <retrieved, oldest→newest>

     Recent memories:
     <window, oldest→newest>
     ```
  4. `window_count: 0` = legacy pure-retrieval (still supported, no label);
     `retrieved_count: 0` = pure window. Dedup is structural (pool excludes the
     window slice) — no content comparison needed.
- State schema unchanged (`{"memories": [...]}`); `set_state` untouched.

### A2. `SummarizingMemory` → three tiers

- New param `retrieved_count: int = 10` (generous default ON); bump
  `render_count` 10 → 20 (the recent window; generosity directive). Store caps
  (`max_memories=200`, `max_summaries=20`) unchanged.
- `render(query)` becomes (sections omitted when empty):
  1. `Summary of earlier events:` + all summaries (unchanged — they are few).
  2. `Relevant past memories:` + top `retrieved_count` from the pool =
     **summaries + verbatim memories excluding the recent window**, ranked by
     the shared `_rank_by_overlap` helper (summaries in the pool let abstracted
     old knowledge resurface by relevance). Skip the section when `query` is
     None or `retrieved_count == 0`. A summary already shown in section 1 must
     NOT repeat here — exclude summaries from section 2's *output* if kept in
     section 1, OR (simpler, preferred) keep summaries out of section 1 only
     when they appear in section 2 is over-clever: **decision — pool =
     verbatim-minus-window only; summaries stay solely in section 1**. They are
     always rendered anyway, so retrieving them adds nothing. This keeps dedup
     trivial and sections disjoint by construction.
  3. Recent window: last `render_count` verbatim memories (unchanged shape).
- `record`/`_summarize`/state schema unchanged — **no new state**, so restore
  round-trip tests must keep passing without edits.

### A3. Docs + tests

- Update: module docstring (memory.py:8-19), `docs/configuration.md` memory
  section (params table + defaults + the two-section render shape),
  `docs/building_agents.md` Runtime Memory section.
- Tests (`tests/test_memory_policies.py`):
  - UPDATE the three existing retrieval tests (exact-render assertions change
    with labels/params; `k=` → `retrieved_count=`, add `window_count=0` where
    the old pure-retrieval behavior is being asserted).
  - NEW: window always present even when irrelevant to query; retrieved never
    duplicates window items; section order (retrieved before window); no-query
    fallback volume = `window_count + retrieved_count`; `window_count=0` legacy
    parity; summarizing: retrieved section appears with query + pool, absent
    with `retrieved_count=0`; summarizing restore does not re-summarize
    (existing test must stay green).
  - `WindowMemory` golden-parity test must remain byte-identical — the DEFAULT
    `sim.memory` policy is untouched by Part A.

---

## Part B — Intervention swap language (src/silisocs/simulation_engines/interventions.py)

Both kinds are **persistent** state-setters (replayed on resume for
`at_step < start_step`) and follow the existing handler pattern:
`InterventionHandler` subclass + `_BUILTIN_HANDLERS` registration + docs table
row + AGENTS.md §4.5 mention.

### B1. `set_turn_policy`

Config:

```yaml
- kind: set_turn_policy            # persistent
  slot: {built_in: fixed_count, params: {count: 2}}
  # scope — at most ONE of:
  flow: burst_posters              # per-flow override
  gm: social                       # per-GM override
  # neither -> global turn policy
```

- **Why safe**: built-in turn policies are config-only objects with zero
  episodic state and no checkpoint footprint (verified: `_DrivenTurnPolicy`
  subclasses, policies/turns.py:158-246). Rebuild-from-slot is the same move
  `set_participation` makes.
- Apply:
  - Build via the existing `build_turn_policy(slot)` (policies/factory.py:101).
  - Global → `engine.turn_policy = policy` (attr at base_engines.py:86).
  - Per-GM → `engine.gm_turn_policies[name] = policy` (dict at
    base_engines.py:92).
  - Per-flow → the **step strategy's** `flow_turn_policies` dict (dataclass
    fields, steps.py:114/184, read live per batch via `.get(flow_name)` at
    steps.py:139/276/313). VERIFY at implementation time: (a) the engine
    attribute holding the strategy instance (grep `step_strategy` in
    base_engines.py) and (b) that per-GM/per-flow maps are read at batch time,
    not snapshotted at init — the steps.py `.get()` sites say yes for flow;
    confirm the gm map's read site the same way.
  - Fire-time errors: `flow:` under a step strategy without
    `flow_turn_policies` (base/sequential) — same effectiveness rule as the
    config knob; unknown gm name (reuse `_resolve_gm`-style matching against
    `engine.gm_turn_policies` keys is wrong — validate against
    `ctx.game_masters` names).
- Validate (config-load): `slot` is a mapping; `flow` and `gm` mutually
  exclusive; `gm` checked via `_validate_gm_target`; `flow` is type-checked
  only (flow tags are materialized at runtime, not declared in the interventions
  view — fire-time check covers it).
- Precedence is untouched: per-flow > per-GM > global is resolved at batch time
  from the same maps we mutate.
- Tests: global swap takes effect on the NEXT step (fixed_count=2 → two actions
  per turn, observable via a counting stub agent); per-GM entry lands in
  `engine.gm_turn_policies`; per-flow entry lands in the strategy map; mutual
  exclusion + bad-slot validation errors; replay on resume re-applies.

### B2. `swap_component` (stateless-only guard)

Config:

```yaml
- kind: swap_component             # persistent
  gm: social                       # null = single/default GM
  role: observe                    # v1 whitelist: observe | next_acting | update
  slot: {class_path: my_pkg.MyObserve, params: {...}}
```

- **v1 role whitelist**: `observe`, `next_acting`, `update` only. `resolve` and
  `action_prompt` are a coupled pair validated per-GM against the effective
  tool-calling mode — excluded (documented), revisit only with mode-compat
  validation. `initialize` is meaningless mid-run.
- **Rebuild seam** (the real work): components are built inside
  `ComponentGameMaster._build_components` (game_master.py:356; MultiFlow
  override at 472) via the per-role builders in `components/factory.py`
  (`build_observe_component`, `build_next_acting_component`,
  `build_update_component`, ...). Add a GM method:

  ```python
  def rebuild_component(self, role_key: str, slot_cfg: Mapping[str, Any]) -> Any
  ```

  that (a) calls the same per-role factory builder with the same wiring the
  original construction used (the GM instance already holds backend, model,
  agent names, flow tags, tool_calling_mode — factor the per-role wiring kwargs
  out of `_build_components` rather than duplicating them), (b) asserts the
  result isinstance the role's ABC (`ObservationComponent` /
  `NextActingComponent` / `UpdateComponent` from components/base.py), and
  (c) re-points BOTH the typed slot attribute (e.g. `self.observe_component`)
  AND `self._component_registry[role_key]` — the intervention layer's
  `_gm_components` and checkpointing both read the registry. MultiFlow
  flow-specialized components are addressable by their registry key; if the
  key→slot-attr mapping is ambiguous for a flow-specialized key, v1 updates the
  registry entry only and documents that.
- **Stateless guard** (both directions):
  - Refuse when the OUTGOING component's `get_state()` is non-empty — error
    message points at `set_component_params` for retuning stateful components
    (the recsys update component is the canonical stateful case).
  - Refuse when the freshly built INCOMING component's `get_state()` is
    non-empty right after construction.
- **Checkpoint hardening** (small, additive, closes the residual hole where a
  swapped-in component accumulates state AFTER the swap): GM `get_state`
  (game_master.py:268-296) additionally records
  `"component_classes": {key: f"{cls.__module__}.{cls.__qualname__}"}` for keys
  whose state is non-empty; `set_state` (game_master.py:318-324) consults it
  when present and SKIPS + warns loudly on class mismatch instead of
  blind-applying foreign state. Old checkpoints lack the key → behavior
  unchanged (additive, no schema break).
- Resume semantics: guard means the outgoing component checkpointed `{}`, so
  build-from-config → restore (applies nothing) → `replay_persistent` re-swaps
  is consistent by construction.
- Validate (config-load): role in whitelist; slot shape via the shared
  `validate_component_slot_shape` (imported by construction/game_masters.py:21);
  gm via `_validate_gm_target`.
- Tests: swapped observe changes the next observation (stub GM or real
  ComponentGameMaster with two trivial observe components); stateful outgoing
  refused (use a component with non-empty `get_state`); stateful incoming
  refused; registry AND slot attr re-pointed; replay on resume; checkpoint
  class-mismatch skip+warn (unit test on GM get_state/set_state directly);
  validation rejections (unknown role, `resolve` role, bad slot).

### B3. Docs

- `docs/configuration.md`: two new rows in the interventions kinds table + YAML
  examples + a "what is swappable" paragraph (whitelist, stateless rule, the
  excluded families and why).
- `AGENTS.md` §4.5: add both kinds; one sentence each.
- `docs/environment_layer.md`: mention `rebuild_component` as the component
  hot-swap seam.

---

## Gates & verification (every part)

1. Targeted first: `uv run pytest tests/test_memory_policies.py -q` (A),
   `uv run pytest tests/test_interventions.py -q` (B).
2. Full gate: `taskset -c 0-7 uv run pytest tests/ -q` (baseline 881 passed,
   10 skipped) and `taskset -c 0-7 uv run pre-commit run --all-files` (ruff,
   ruff-format, uv-lock, mypy must all pass; ruff-format may reformat — rerun
   until clean).
3. Adversarial self-check before finishing each part: for A, assert the window
   golden test is untouched and no render path can KeyError on empty memories;
   for B, assert replay ordering (restore → replay) is exercised by a test, not
   just reasoned about.
4. Leave everything staged, uncommitted. Do not stage this file.

## Known verification points for the implementer (do these greps first)

- `grep -n "step_strategy" src/silisocs/simulation_engines/base_engines.py` —
  exact engine attr for the per-flow map mutation (B1).
- `grep -n "gm_turn_policies" src/silisocs/simulation_engines/` — confirm the
  per-GM map is read per batch (B1).
- Read `ComponentGameMaster._build_components` + MultiFlow override end-to-end
  before factoring the wiring kwargs (B2) — the factory signatures differ per
  role.
- `grep -rn "k=" tests/test_memory_policies.py` — the retrieval tests to update (A1).
