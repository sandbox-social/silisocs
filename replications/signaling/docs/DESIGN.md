# SiliSocS port of the Concordia signaling example — design

Status: **implemented** (this document is kept aligned with the code; see
`port_notes.md` for the deliberate deviations). Baselines pinned in
[PINNED_COMMITS.md](PINNED_COMMITS.md). Fidelity target is
**faithful-mechanism**: the port reproduces the causal structure of the
original (clearing-house price formation, memory carry-over across days, the
consumption-visibility manipulation, the three conditions), not its
model-specific numbers.

This design was derived from the two pinned codebases; §9 records the
load-bearing design decisions and their reasons.

---

## 1. The mechanism being ported (from the pinned Concordia source)

One experiment = `num_days` days. Each day:

1. **Marketplace phase** — `num_marketplace_rounds` (5) *simultaneous* rounds.
   Every buyer submits exactly one sealed bid `{good, price, qty}`, every
   seller one sealed ask; at the round boundary a clearing-house double
   auction per good matches bids (desc) against asks (asc) at the midpoint
   price, with cash/stock checks; per-agent outcome messages are queued and
   delivered with the next observation. Clearing prices append to a price
   history that carries across days. (Alternate market mode: `fixed_prices`,
   present in the library, unreachable from the upstream CLI.)
2. **Post-market reflection** — each buyer answers "Reflect on your
   marketplace experience today…" and the answer is observed back into memory.
3. **Eating** — one random edible is consumed from each buyer's inventory; if
   none, a starvation statement is generated instead.
4. **DIAL phase** (condition-dependent) — buyers are paired into fixed
   mixed-sex dyads (new partner every day, no pair repeats). Per dyad, an
   initializer generates via LLM: a *wearing statement* (random wearable from
   market inventory + its quality tier — **this is the signaling channel**),
   5 personal mundane events (conditioned on background, the eating statement,
   and the upcoming date), and 1 shared date-scene (conditioned on both
   wearing statements and a random theme). All are injected as observations.
   Then a dialogic GM runs `num_dial_rounds` (80) alternating utterances.
5. **Post-date reflections** — 4 sequential act+observe rounds per buyer:
   date summary, visual first impression, comparative reflection, and a 0–10
   rating (regex-extracted and re-observed as "`X rated Y as r/10`").

Conditions: `social` = all of the above; `asocial` = steps 1–3 only;
`asocial_personal` = steps 1–3 + personal-events injection, no conversation,
no shared scene, no post-date reflections.

**Crucial architectural fact:** Concordia rebuilds the entire simulation object
graph *per day* and *per dyad*, and hand-copies `__memory__` state between sim
objects (`simulation.py:316-329`, `dial.py:216-227`). Memory carry-over is a
workaround for its lifecycle, not a mechanism. In SiliSocS agents persist for
the whole run, so this layer — and the `entities`/`marketplace_agents`
bookkeeping and the per-day price-history threading — disappears entirely.

---

## 2. Time structure: the day is a step calendar

SiliSocS's unit is the **step** (= backend episode). There is no built-in
"day", and none is needed: a day is a fixed-length block of steps, defined by
a pure function of the step index (`components/calendar.py`).

Per day, in the `social` condition (defaults `R=5`, `D=80`):

| Steps (within day) | Phase | Acting GM | What happens |
|---|---|---|---|
| `0 … R-1` | `market` round r | `market_gm` | all buyers + sellers submit one sealed order each (simultaneous; buffered) |
| `R` | `market_reflection` | `journal_gm` | every buyer reflects on the day's purchases (free text, observed back) |
| `R+1` | `dial_setup` | *(nobody acts)* | the day-boundary ceremony, run by `market_gm`'s update component: resolve the final round, draw eating/wearing, generate and inject the personal events and date scene |
| `R+2 … R+1+D` | `date` turn k | `date_gm` | one utterance per dyad per step, speakers alternating |
| last 4 | `date_reflection` j | `journal_gm` | the 4 post-date reflections, one per step, verbatim prompts |

Day length `L = R + 1 + 1 + D + K` (= 91 with the upstream defaults);
`num_steps = num_days * L`. The asocial arms set `D = K = 0`, giving `L = 7`;
`asocial` disables the ceremony (`dial_setup.enabled: false`), `asocial_personal`
runs it in events-only mode. The `dial_setup` slot exists in every condition so day
indices stay comparable across arms. The calendar is a pure function of
`(step, R, D, K)` — stateless, replay- and resume-stable.

Why steps and not "one step = one day": the market rounds are
*simultaneous-move rounds with a resolve at each boundary* — exactly the
`SimultaneousRoundGame` step contract (buffer during the step, resolve in
`update()` at the next step's start). And the date conversation needs strict
alternation (B must see A's utterance before replying), which per-step
scheduling gives for free while different dyads still run **concurrently
within a step** — a large wall-clock win over Concordia, which runs the 25
dyads serially.

Why not enumerate phases as `flow_order`/chain tricks: phases are
*time*-based, not agent-based. The one seam that expresses "who acts at this
GM at this step" is the GM's `next_acting` component — see §4.

---

## 3. Runtime topology: three GMs, two flows, built-in multi-GM engine

`env.gm_orchestration` with `sim.engine.step.built_in: multi_gm` (the default
concurrent traversal — no custom engine, no custom step strategy):

```yaml
gm_orchestration:
  gms:
    - gm_name: market_gm    # sequence 0 — MarketplaceApp (SimultaneousRoundGame subclass)
    - gm_name: journal_gm   # sequence 1 — JournalApp (free-text reflections)
    - gm_name: date_gm      # sequence 2 — DateMessagingApp (MessagingApp subclass)
  flow_bindings:
    flow_to_gms:
      buyer:  [market_gm, journal_gm, date_gm]
      seller: [market_gm]
flows:  # via persona classes
  buyer:  flow_tag on the persona class for the 50 LA personas
  seller: flow_tag on the generated one-per-good seller class
```

On any given step, at most one GM's `next_acting` returns a non-empty set
(calendar-gated), so the chain hops for off-phase GMs cost nothing. All three
GMs and both flows are identical across conditions — **conditions differ only
in world config** (the `calendar` and `dial_setup` blocks), not in env.

Per-GM overrides used (all built-in knobs):

- `journal_gm`: `action_mode: custom`, `tool_calling: none`, resolve
  `JournalResolveComponent` (free text — reflections are prose, not tool
  calls; the agent's whole answer is the action, so neither shipped resolver
  fits).
- `market_gm`, `date_gm`: default `generic`/tool-calling single, resolve
  `tool_calling`.
- `flow_action_filters` on `market_gm`'s resolve: `buyer → {enabled: [BID]}`,
  `seller → {enabled: [ASK]}` — role enforcement is config, not backend code.

Engine: `executor: asyncio` (a date step can have ~25 concurrent turns; a
market step ~75), `turn_policy: single_action` everywhere, `participation:
all`.

---

## 4. New components (what actually gets written)

All replication-local under `replications/signaling/components/`, referenced by
`class_path`, zero core-framework edits.

### 4.1 `marketplace_app.py` — `MarketplaceApp(SimultaneousRoundGame)` (~250 lines)

The only substantial backend. Fields (= config `params`): `goods_path`
(JSON), `market_type` (`clearing_house` | `fixed_prices`), `show_advert`,
`cash_distribution` (`mixed` | `pareto`), `seed: ${seed}`, calendar params
the shared `calendar` block (to know which episodes are market rounds).

- `Good`/`Order` dataclasses and **`clear_auction` / `clear_at_fixed_prices`
  transcribed verbatim** (in their own import-free module,
  `marketplace_clearing.py`, so they can be read side by side with upstream) from the pinned `marketplace.py` (matching loop,
  midpoint price, cash/stock guards, VWAP trade logging, failed-order
  messages). Transcribe first, refactor never — this is the highest-risk
  correctness surface and the subject of the differential tests (§7).
- `@app_action BID(good, price, qty)` / `ASK(good, price, qty)` validate and
  call `record_choice` (the base class enforces one sealed order per agent per
  round and hides it until resolve).
- `resolve_round(rnd, choices)` builds the per-good order books from the
  round's choices and runs the ported clearing logic; returns clearing prices
  in `RoundResult.summary` (→ one committed `round_resolved` row per round in
  `action_events.jsonl` — the trade/price history is *in the log by
  construction*). Skips rounds with no choices (non-market episodes).
- `observe(actor)` ports `_handle_make_observation`: queued outcome messages
  from the last resolved round, round number, last clearing prices, cash,
  inventory, producer stock (buyers) / "submit your order" (sellers).
- Consumer cash drawn at init from the ported `generate_cash_values`
  distribution, seeded by `${seed}`; one random low-tier clothing item as
  initial inventory (as upstream). Producers derived from the goods table
  (one seller per good, cost = listed price, stock = listed inventory).
- Public read API for the DIAL handler: `consume_random_edible(agent) -> str`
  (eating statement or starvation statement; mutates inventory),
  `wearing_statement(agent) -> str` (random wearable + tier/category text) —
  verbatim ports of `dial.get_eating_statement` / `get_wearing_statement`.
- Checkpointing: `SimultaneousRoundGame` already round-trips choices, results,
  cumulative payoffs, and the committed-events mirror; the subclass extends
  `get_state`/`set_state` with cash/inventory/price-history (the base class
  flags `provides_checkpoint_state = True`).

### 4.2 `date_app.py` — `DateMessagingApp(MessagingApp)` (~40 lines)

`MessagingApp` already gives DM privacy by rendering, buffering, validation,
and checkpointing. The subclass overrides `observe()` to restrict visible
history to *this day's* messages with *today's partner* (calendar + dyad
schedule params) — mirroring Concordia's fresh-sim-per-dyad semantics, where
past raw dialogue is not in context (only reflections survive, via memory).
Enabled action: `SEND_MESSAGE` only.

### 4.3 `journal_app.py` — `JournalApp(BackendApp)` (~70 lines)

Minimal backend: one free-text action, `record_reflection(agent_name, text)`,
committed to `action_events.jsonl` with a calendar-derived label
(`market_reflection` | `date_summary` | `visual_impression` | `comparison` |
`rating`). On the rating step it regex-extracts the first number (the
upstream `\b\d+(\.\d+)?\b` pattern) into the row's `data` and, mirroring
upstream, the resolved text is observed back to the agent as
`[Reflection] {name} rated {partner} as {r}/10`. `observe()` returns the
current reflection context (partner name for date reflections).

### 4.4 `next_acting.py` — two calendar-gated components (~80 lines)

- `CalendarNextActing(phase=...)` for `market_gm` and `journal_gm`: returns
  the full roster (market: buyers+sellers; journal: buyers) when the calendar
  says the step is in its phase, else `[]`.
- `DyadTurnNextActing` for `date_gm`: on date step `k` of day `d`, returns the
  set `{dyad[k % 2] for dyad in schedule[d]}` — one speaker per dyad,
  alternating. Stateless (schedule + calendar are pure functions), so
  replay/resume-stable and safe under the concurrent traversal.

### 4.5 `dyads.py` + `calendar.py` — pure helpers (~100 lines)

`generate_mixed_sex_dates` ported **verbatim** (same `random.seed(seed)`,
same shuffle loop): with identical roster order and seed it reproduces
Concordia's exact schedule, which makes the dyad-schedule test a strict
equality test, not just a property test. Sex map comes from
`personas_la.json`. `calendar.py`: `phase_of(step) -> (day, phase, index)`.

### 4.6 `prompts.py` + `action_prompt.py` — verbatim prompt text (~120 lines, mostly strings)

Prompts are mechanism; every call-to-action and generation prompt is carried
over verbatim into one module: `GOAL_TEXT`, the buyer/seller order
call-to-actions, the conversation call-to-action, the 4 reflection prompts,
the DITL prompts (`SHARED_DIALOGUE_SETUP_PROMPT`,
`PERSONAL_MUNDANE_EVENTS_PROMPT`, `_DATE_THEMES`), starvation/eating/wearing
statement templates. Two small `action_prompt` components issue them:
`ReflectionActionPromptComponent` (`journal_gm`) selects the reflection
question by calendar index, and `MarketActionPromptComponent` (`market_gm`)
selects upstream's per-role CTA (consumer bids, producer asks) from the
backend's role map. The date GM uses the default component with the verbatim
`DATE_CALL_TO_ACTION` text in `params` (a test pins the YAML to the
constant), and a differential test pins every constant word-for-word against
the pinned upstream sources.

### 4.7 `dial_handler.py` — `DayBoundaryUpdateComponent(UpdateComponent)` (~250 lines)

The day boundary, mounted as `market_gm`'s `update` slot. The engine calls
every GM's update once per step, serially on the loop thread, before any agent
acts (`RuntimeEngine.run_step`), which gives the component the step index,
every agent object, and its own backend — the same reach an intervention has,
without an intervention's hand-written `at_step` schedule. On every step it
first runs the ordinary backend update (the slot's normal job, which it also
owns — this clears each finished market round before anything reads
inventory). Then, by calendar phase:

1. **Day-start steps (all conditions)**: reset every seller to the pristine
   state snapshotted at step 0 — `backend.restock_sellers()` for the ledgers
   (full stock, cash 100, empty queue) plus the duck-typed
   `get_state`/`set_state` seam for the agents' memory. This is upstream's
   daily fresh-seller regeneration; buyers carry everything over. The
   baselines are the component's only checkpointed state.
2. **The `dial_setup` step (all conditions)**: every buyer eats
   (`consume_random_edible` — eating/starvation, upstream's unconditional
   daily draw). With the ceremony disabled the statement stays queued and
   surfaces in the next market observation, as upstream's asocial arm does.
3. **The `dial_setup` step (ceremony enabled)**: for each of today's dyads,
   draw the partners' wearing statements, generate the shared date-scene
   (theme keyed by `(seed, day, dyad)`), generate each partner's 5 personal
   events — via the model with the verbatim prompts, conditioned on the
   agent's full memory (`Relevant Memories:`, upstream's `get_memories` dump)
   plus the eating/wearing statements, exactly as `DayInTheLifeInitializer`
   does — then inject via `agent.observe(...)` with the upstream tags
   (`[Daily Personal Event N]`, `[Daily Shared Setup]`), the same
   add-to-queue→observe semantics. Buyers not paired today receive nothing,
   as upstream's leftovers don't.

`asocial_personal` runs the ceremony with `shared_setup: false` (events only);
`asocial` sets `enabled: false` — no generated content, but duties 1 and 2
still run. Generation is serial at the boundary (~75 LLM calls/day at n=50) —
matching upstream, which is also serial here; everything turn-shaped
(reflections, conversation, orders) runs through the engine's concurrent
executor instead.

Because the schedule is derived from the calendar rather than declared, changing
`num_days`/`R`/`D` needs no second edit.

### 4.8 `agents.py` — `SignalingAgent(NativeAgent)` (~150 lines, half verbatim strings)

One persistent agent class per buyer covering all phases (this is where
Concordia needs two prefabs + memory copying). It keeps `NativeAgent`'s
observe/memory/checkpoint machinery and overrides `act`/`act_async` to run
the upstream question-chains before answering, switching pipeline on the
action spec (no config needed):

- ActionSpec offers `BID` → consumer pipeline: SituationPerception,
  SelfPerception, ConsumerEvaluation (verbatim questions, each one
  `sample_text` call), concatenated into the acting context — the
  three-questions architecture.
- ActionSpec offers `SEND_MESSAGE` → convo pipeline: SituationPerception,
  SelfPerception, PersonBySituation, LastSentence, PinkNoiseStrategy
  (verbatim converge/diverge question).
- Free-text reflection spec (journal; the spec carries its calendar
  `reflection_kind` in `extra_args`) → the market reflection runs the
  consumer pipeline and the four post-date reflections run the convo
  pipeline, matching which upstream entity answers each (upstream's
  switcher diverts only choice-type specs, so its FREE reflections get the
  full chain).

Sellers are plain `NativeAgent` with the verbatim seller goal — no subclass.

Memory: `NativeAgent` window memory (`observation_history`/`memory_history`
sized generously) instead of Concordia's associative retrieval +
tag-filtering (`ImportantMemories`). This is a deliberate, documented
deviation (§8): carry-over (day-N memory ⊇ day-N−1) is the mechanism; the
retrieval embellishment is prompt-engineering. It also removes the
sentence-transformers embedder dependency entirely.

---

## 5. Data (`input/`, one-shot conversion)

`tools/convert_concordia_data.py` imports the pinned worktree modules and
emits:

- `input/goods/{original,synthetic,subculture}.json` — the nested
  category→tier→item dicts, verbatim.
- `input/personas_la.json` — 50 records:
  `{name, sex, context (from the [Persona] blob), memories: [verbatim
  formative-memory strings]}`.
- `input/sellers_{original,synthetic,subculture,both}.json` — generated
  one-per-good seller records (`{name: Seller_i, goal: <verbatim seller goal>,
  good, cost, stock}`), so the seller persona class is data-driven too.

Wired through the persona pipeline with `data.source: local_json` +
`field_map` (`name: name`, `context: context`,
`specific_memories_field: memories`), buyer class `count: ${num_agents}`,
`flow_tag: buyer`; seller class from the sellers file, `flow_tag: seller`.
The script is kept in-repo: it *is* the documentation of the mapping.

---

## 6. Config surface (`conf/`, `study.yaml`)

Mirrors `replications/echo_chambers` layout:

```
conf/world/default.yaml        # social; num_days, calendar block (R, D, K),
                               # item_list, num_agents, seed, dial_setup block
conf/world/asocial.yaml        # L = R+1, dial_setup.enabled: false
conf/world/asocial_personal.yaml  # L = R+1, events-only ceremony
conf/agents/default.yaml       # buyer + seller classes (per-class model blocks)
conf/env/signaling.yaml        # the 3-GM gm_orchestration block (shared by all conditions)
conf/sim.yaml                  # multi_gm, asyncio executor, flow filters, llm block
conf/eval.yaml                 # probes disabled (surveys arrive later as extensions)
study.yaml                     # conditions social/asocial/asocial_personal × seeds
```

`num_steps`/`day_length` are written out per condition (OmegaConf does no
arithmetic, and the framework reads `num_steps` before any replication code is
importable) and validated against the calendar at startup by
`components/loop.py::CalendarLoopStrategy` — so a stale number, including one
introduced by a CLI override, fails the run instead of truncating it. Checkpointing: `every_n_steps: ${day_length}` by default (one
checkpoint per day boundary; 90 per-step checkpoints/day would be waste),
overridable by studies. Outputs: everything lands in the standard artifacts
(`action_events.jsonl` per GM dir, `probe_events.jsonl` when enabled), loaded
via `load_run`; `evaluators/metrics.py` derives the upstream
`signaling_trades.json` / `signaling_prices.json` equivalents (plus
per-dyad ratings) from `market_gm/action_events.jsonl` and
`journal_gm/action_events.jsonl` — no bespoke log files.

---

## 7. Definition of done — deterministic invariants (no LLM required)

Encoded as `tests/` for the replication before any real-model run:

1. **Clearing house, differential**: a fixture generator runs the *pinned
   Concordia* `MarketPlace._clear_auction` on a battery of synthetic order
   books (empty sides, partial fills, cash-short buyers, stock-short sellers,
   equal prices, multi-good) and asserts our `resolve_round` produces
   identical allocations, prices, cash/inventory deltas, and
   filled/partial/failed statuses. This is the strongest fidelity gate
   available and it is pure-Python on both sides.
2. **Fixed-price mode**: prices never exceed the listed price; random
   rationing only among affordable bids.
3. **Dyad schedule, exact-equality differential**: verbatim port + same seed
   ⇒ schedule identical to `generate_mixed_sex_dates` output; plus
   properties (mixed-sex, no repeats across days).
4. **Starvation**: empty-food inventory ⇒ starvation statement, and only then.
5. **Calendar**: phase partition is total, disjoint, condition-correct.
6. **Memory carry-over**: with a scripted model, day-N agent memory ⊇
   day-N−1 (trivial in-run, but asserted across a checkpoint save/restore).
7. **Reflection sequence**: each social day yields exactly 4 journal rows per
   buyer with the right labels, and one parsed rating.
8. **Checkpoint round-trip**: `MarketplaceApp` state (mid-buffered round,
   cash, inventories, price history) and `DateMessagingApp` survive
   save→restore byte-identically; run resumed at a mid-day step continues the
   calendar correctly.
9. **Scripted end-to-end**: `sim.llm.disabled: true` (or scripted provider)
   run of 2 days × 4 agents completes in all three conditions.

Sanity runs (Phase-4 analogue, reduced scale, 3 seeds): direction checks only
— status-good purchase share higher under `social`; prices move under
`social`, flat-to-declining under `asocial`; conversations mention worn items
at a non-trivial rate. Magnitude deltas vs. the paper are recorded in
`port_notes.md`, not chased.

---

## 8. Recorded fidelity deviations (→ `port_notes.md` at implementation time)

| Deviation | Reason | Risk |
|---|---|---|
| Tool-calling (`BID`/`ASK`/`SEND_MESSAGE` schemas) instead of free-text JSON + regex resolve | Idiomatic SiliSocS; upstream's JSON-in-prose was a parsing workaround, not mechanism. Call-to-action wording preserved. | Order *submission reliability* improves (fewer dropped orders than upstream's regex path) — direction-safe |
| Window memory instead of associative retrieval + `ImportantMemories` tag filter | Carry-over is the mechanism; retrieval is prompt engineering. Removes embedder dependency. | Context composition differs; mitigated by generous windows + tags preserved on injected events |
| Dyads run concurrently within a step (upstream: serial) | Engine parallelism; dyads are causally independent | None (independence is upstream's own assumption) |
| One persistent agent instead of consumer/convo prefab pair + memory copying | SiliSocS agents persist; the pair was a lifecycle workaround | None — pipelines preserved per phase |
| No numeric RNG parity (cash draws, shuffles other than the dyad schedule) | Different RNG call orders; distributions and seeds preserved | Mechanism-level parity only, as scoped |

---

## 9. Load-bearing design decisions, and why

- **No `date_initialize.py`/`date_observe.py` GM components** for the DIAL
  setup: generation + injection lives in one update component
  (`DayBoundaryUpdateComponent`, §4.7) at the day boundary, because (a) it
  needs cross-GM access (market inventory → wearing/eating) which the update
  slot provides by design and observe components do not, and (b) injection via
  `agent.observe` reproduces upstream's queue semantics exactly. (The update
  slot derives its schedule from the calendar rather than a hand-written
  `at_step` list.)
- **Reflections are steps, not handler code**: as `journal_gm` turns the
  engine parallelizes ~50 reflection calls per boundary instead of
  serializing them, and every reflection lands in `action_events.jsonl` with
  zero extra logging code.
- **`fixed_prices` ships in the backend but is CLI/config-unreachable as a
  condition** (same as upstream: in the library, not the product), preserved
  deliberately as extension fodder.
- **Three GMs, not two**: the journal GM costs ~70 backend lines and buys
  free-text reflections under a per-GM `tool_calling: none` override without
  contaminating the tool-calling market/date GMs.
- **No custom engine/step/loop policy at all**: phase exclusivity comes from
  calendar-gated `next_acting`, so the default concurrent `multi_gm`
  traversal is correct and simpler.

## 10. Implementation order

1. Data conversion + `dyads.py`/`calendar.py` + their tests (pure, fast).
2. `MarketplaceApp` + differential clearing tests (the risk concentrates
   here; everything else is thin).
3. `journal_gm` + `date_gm` backends + next-acting components; scripted-model
   end-to-end for `asocial`.
4. `DayBoundaryUpdateComponent` + `SignalingAgent` pipelines; scripted end-to-end for
   `social` / `asocial_personal`; checkpoint/resume tests.
5. Sanity runs (reduced scale, real model), `port_notes.md`, freeze tag.
