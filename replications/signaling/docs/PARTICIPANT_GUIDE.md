# Participant Guide: The Signaling Simulation, Two Implementations

This guide is your map. It explains the experiment both codebases implement,
then walks through each implementation: how it is structured, where the code
and state actually live, how to run it, and where the outputs land. Read the
shared section first, then the section for the codebase you were assigned.
Keep it open while you work — it is meant as a cheatsheet.

---

## 1. The experiment (identical in both codebases)

The simulation implements a *conspicuous consumption* study. A population of
buyers drawn from a pool of 50 Los Angeles personas (a run uses `num_agents`
of them, default 10 — silisocs takes the first N in the file, Concordia draws
a seeded random sample), plus one seller per good, lives through a multi-day
cycle. Each **day**:

1. **Market phase.** Several rounds of a sealed-bid clearing-house market.
   Buyers submit bids (good, price, quantity), sellers submit asks; the market
   clears simultaneously each round (highest bids matched against lowest asks,
   trading at the midpoint of the matched bid and ask prices). Buyers hold
   cash and an inventory; each seller sells one good.
2. **Market reflection.** Each buyer reflects on the day's shopping.
3. **Day boundary.** Every buyer eats (consumes a random edible item from
   inventory). Buyers are paired into man/woman **dyads** — a fresh partner
   every day, scheduled for the whole run up front. Each dyad member gets
   generated "personal events" for the day and a shared **date scene**
   generated from what each partner is *wearing* (a wearable item drawn from
   what they own — this is the signaling channel).
4. **The date.** The dyad holds a many-turn conversation.
5. **Post-date reflections.** Four reflection prompts, the last of which asks
   for a **0–10 rating of the partner** — the study's headline dependent
   variable.

Sellers exist only in the market: they do not date, reflect, or eat. **Each
day sellers start fresh** — full stock, starting cash, no memory of previous
days — while buyers carry everything forward. (This matches the original
study's design.)

**Conditions.** `social` runs everything above. `asocial_personal` keeps the
personal events but drops the shared date scene, the date conversation, and
the post-date reflections (so no ratings). `asocial` drops all generated
day-boundary content and the dates — a market-only control. (Eating happens in
every condition.)

One name to know: the day-boundary machinery (eating, dyad pairing, scene and
event generation) is called **DIAL** — "day in the life" — and the name
appears in both codebases.

**Data.** Both codebases use the same 50 personas, the same goods tables
(food/clothing/accessories/gadgets in Low/Mid/High quality tiers), and one
seller per good.

---

## 2. The Concordia implementation

### 2.1 File map

Everything scenario-specific lives in `examples/signaling/`; about half the
machinery it uses lives in the Concordia library (`concordia/...`). You will
work across both.

| File | Role |
|---|---|
| `examples/signaling/README.md` | Upstream's own overview and quick start. (Heads-up: its quick-start uses the module path of an installed package, `concordia.examples.signaling.run`; from a repository clone, run `python -m examples.signaling.run` as shown in §2.4.) |
| `examples/signaling/run.py` | CLI entry point. Parses flags, builds the model + embedder, calls `run_experiment`, writes outputs. |
| `examples/signaling/simulation.py` | **The orchestrator.** `run_experiment()` contains the day loop that drives everything (§2.2). Also builds the per-day marketplace simulation config. |
| `examples/signaling/dial.py` | The "day in the life" (DIAL) machinery: builds one conversation simulation per dyad, generates personal events and the shared date scene, runs the post-date reflections and parses the 0–10 rating. |
| `examples/signaling/agents/consumer.py` | The buyer entity prefab — the recipe that builds the market-side agent (plus its evaluation questions). The per-agent cash/inventory ledger is NOT here; see §2.3. |
| `examples/signaling/agents/convo_agent.py` | Builds the conversational entity used on dates. |
| `examples/signaling/configs/goods.py`, `configs/personas.py` | The data, as Python dicts: goods tables, personas, dyad scheduling (`generate_mixed_sex_dates`). (Sellers are generated inline in `simulation.py`, one per good.) |
| `concordia/contrib/components/game_master/marketplace.py` | **The market engine** (library). Order collection, the clearing algorithm, trade/price history, per-agent ledgers (`_agents`), and market observations. |
| `concordia/prefabs/game_master/marketplace.py` | Game-master prefab wrapping the above. |
| `concordia/contrib/components/game_master/day_in_the_life_initializer.py` | DITL initializer (library): per-dyad scene/event injection (`_process_dyad`). |
| `concordia/contrib/prefabs/game_master/dial_dyad_initializer.py` | Prefab wrapping the DITL initializer for dyads. |

### 2.2 The day loop — what actually happens

`run_experiment()` in `simulation.py` (search for `for day in range`) executes,
per day:

1. **Build a brand-new marketplace `Simulation` object.** Yes — every day. On
   day 0 all agents are created fresh from the persona/seller configs. On
   later days the builder is passed the previous day's *buyer* market ledgers
   and the price history.
2. **Carry buyers forward.** On later days, each buyer's memory is copied from
   yesterday's entity into today's freshly built entity:
   `entity.get_component('__memory__').get_state()` → `set_state(...)` on the
   new one. Only buyers are in the carried set; sellers are new objects every
   morning.
3. **Play the market**: `market_simulation.play()` runs the simultaneous
   rounds; the marketplace component collects bids/asks and clears each round.
4. **Harvest logs** from the component: `trade_history` and `.history`
   (which the loop stores as `price_history`).
5. **End-of-day marketplace reflection**: for each buyer, the loop builds a
   free-text action spec (`entity_lib.free_action_spec(call_to_action=...)`),
   calls `entity.act(action_spec)`, and observes the answer back into the
   entity's memory. This act-then-observe loop is the file's idiom for asking
   an agent a question.
6. **Eating**: `dial.get_eating_statement(...)` per consumer (draws from
   inventory, pushes an outcome statement onto the consumer's queue).
7. **Dates** (skipped only under `asocial`): for each of today's dyads,
   `dial.run_dyad_simulation(...)` builds a two-person conversation simulation
   (the DITL initializer injects personal events and the shared scene generated
   from both partners' wearing statements), runs the conversation, then runs
   the four post-date reflections. The rating reflection is parsed with a
   regex and the result is observed back into the rater's memory as
   `[Reflection] X rated Y as N/10`. (Under `asocial_personal` the dyad
   simulation still runs but injects personal events only — no conversation,
   no reflections.)

### 2.3 Where state lives

- **Entity memories** — inside each entity's `__memory__` component
  (associative memory; needs the embedder). Read/write via
  `get_state()`/`set_state()`; append via `entity.observe(text)`.
- **Market ledgers** — inside the marketplace *component*, not the entities:
  `marketplace_component._agents[name]` is a `MarketplaceAgent` record (a
  small dataclass at the top of the marketplace component) holding cash,
  inventory, and the outcome queue.
- **Nothing else persists across days by itself.** The `Simulation` is rebuilt
  each morning; only what the day loop explicitly carries (buyer memories,
  buyer ledgers, price history, the dyad schedule computed on day 0) survives.
- **Ratings and reflections** exist only as text inside the rater's memory and
  in the HTML logs.

### 2.4 How to run

One-time setup, from the repository root (Python **3.12 or newer**):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . sentence-transformers
```

(`sentence-transformers` is imported by the entry point even for no-model
runs.)

Run from the repository root. `--condition` is one of
`social | asocial | asocial_personal`.

```bash
# No-cost dev loop — no API key, checks MECHANICS. Agents produce placeholder
# text, so anything that depends on model output (sensible bids, parseable
# ratings) may be empty; use this to verify that your code runs, fires at the
# right time, and writes what it should — not to verify content.
python -m examples.signaling.run \
  --disable_language_model --use_dummy_embedder \
  --condition=social --num_days=3 --num_agents=4 \
  --num_marketplace_rounds=2 --num_dial_rounds=2 --seed=1

# Real model (slow, costs money — not needed to demonstrate the tasks):
python -m examples.signaling.run \
  --api_type=... --model_name=... --api_key=... \
  --condition=social \
  --num_days=5 --num_agents=10 \
  --num_marketplace_rounds=5 --num_dial_rounds=80 \
  --item_list=original --seed=1
```

**Dev loop tip:** shrink `--num_days`, `--num_agents`,
`--num_marketplace_rounds`, `--num_dial_rounds` to make iteration fast.

**Outputs** (written to `/tmp`): per-day marketplace logs
(`signaling_marketplace_day_N.html`/`.json`), per-dyad date logs
(`signaling_dial_day_N_<dyad>.html`), `signaling_trades.json`,
`signaling_prices.json`. Everything else (reflections, ratings, memories) is
inside the HTML logs — open them in a browser; they are per-entity/per-GM
transcripts of everything said and observed.

### 2.5 Navigation anchors and idioms

Grep for these when orienting: `run_experiment` and `for day in range`
(simulation.py — the loop), `run_dyad_simulation` (dial.py — one date),
`_process_dyad` (DITL initializer — scene/event injection), `free_action_spec`
(the ask-an-agent pattern), `__memory__` (the memory carry-over),
`trade_history` (the market component's log surface).

The codebase's extension idioms: give an agent information with
`entity.observe(text)`; ask an agent something with `free_action_spec` +
`entity.act(...)`; read or move agent state through the `__memory__`
component and the marketplace component's `_agents`; change orchestration by
editing the day loop in `simulation.py` (there is no configuration layer —
behavior lives in code, flags in `run.py`).

---

## 3. The silisocs implementation

### 3.1 The mental model: steps, a calendar, three game masters

Silisocs runs a step loop; the framework calls your components — you do not
write a driver. The experiment's day is mapped onto steps by
`components/calendar.py`, the single source of truth for "what happens on step
N":

| Steps within a day | Phase | Game master | Who acts |
|---|---|---|---|
| `0 … R-1` | market rounds | `market_gm` | buyers + sellers |
| `R` | market reflection | `journal_gm` | buyers |
| `R+1` | day boundary ("dial setup") | *(nobody acts — see §3.3)* | — |
| `R+2 … R+1+D` | date turns | `date_gm` | one speaker per dyad |
| last 4 | post-date reflections | `journal_gm` | buyers |

Social defaults: `R=5`, `D=80` → 91 steps/day, 455 steps for 5 days. Exactly
one GM acts on any step (the others' turn-selection returns nobody).

The three game masters, each a backend + pluggable components:

- **`market_gm`** — `MarketplaceApp` (`components/marketplace_app.py` +
  `marketplace_clearing.py`): sealed simultaneous `BID`/`ASK` as typed tool calls, cleared
  at round boundaries; owns cash, inventories, order books, price history, and
  the eating/wearing draws.
- **`journal_gm`** — `JournalApp` (`components/journal_app.py`): free-prose
  reflections (this GM turns tool-calling off). The rating reflection is
  parsed into a structured row.
- **`date_gm`** — `DateMessagingApp` (`components/date_app.py`): the date
  conversation, visibility scoped to today's partner.

Buyers and sellers are two persona classes (`conf/agents/default.yaml`), each
carrying a **flow tag**. The tags do two jobs: they route buyers through all
three GMs and sellers through the market only, and they drive the
config-level action filter (buyers may only BID, sellers only ASK).

### 3.2 File map

Scenario code (all under `replications/signaling/`):

| File | Role |
|---|---|
| `components/calendar.py` | Step↔day/phase arithmetic (`phase_of`, `day_of`). Everything schedules off this. |
| `components/marketplace_app.py`, `marketplace_clearing.py` | The market backend and the clearing algorithm (transcribed from upstream). |
| `components/journal_app.py` | The reflections backend; parses the 0–10 rating into its committed rows. |
| `components/date_app.py` | The date-conversation backend. |
| `components/dial_handler.py` | The **day-boundary component** on `market_gm`'s update slot: daily seller reset, the eating draw, and the date-scene/personal-events ceremony. It also forwards the every-step backend update that resolves the finished market round — it owns the slot the stock update component would otherwise fill. |
| `components/next_acting.py` | Calendar-gated turn selection (which agents act, per GM, per step). |
| `components/action_prompt.py` | Per-role market prompts and the reflection prompts. |
| `components/prompts.py` | Every prompt/template, transcribed verbatim from upstream. |
| `components/dyads.py` | The dyad schedule (pure function of roster + seed). |
| `components/agents.py` | The buyer/seller agent class. |
| `components/resolve.py`, `goods.py`, `loop.py` | Journal resolve; goods table loader; a loop strategy that validates `num_steps` against the calendar. |
| `components/scripted_behavior.py` | Deterministic no-LLM behavior for the fast dev loop (§3.5). |
| `evaluators/metrics.py` | The metrics CLI (§3.6). |
| `conf/` | All configuration (§3.4). `input/` holds the converted data. |

**Where state lives** (the §2.3 counterpart): agent memory is each agent's
own state, persists across steps, and is checkpointed at every day boundary —
the checkpoints are the easiest place to inspect it. The market ledgers
(cash, inventories, order books, price history) are `MarketplaceApp` backend
state. Ratings and reflections exist twice: as structured rows in
`journal_gm`'s committed action log *and* as text observed back into the
rater's memory. The dyad schedule is a pure function of the roster and seed
(`dyads.py`) — computed, not stored.

Framework machinery lives in the installed package (`src/silisocs/`) — the
engine and step loop (`simulation_engines/`), game-master core and component
slots (`environments/gm/`), the backend base with the action catalog and
committed-event log (`environments/backends/base.py`), checkpointing and the
runner (`runtime/`). **`AGENTS.md` at the repo root is the map of all of it**,
and `docs/configuration.md` is the reference for every config key. When you
wonder "who calls this?", the answer is usually the engine — see §3.3.

### 3.3 What the framework does on every step

Order of operations per step (all framework-driven):

1. **Measurement probes** due at this step fire (if any are configured; by
   default they fire here, *before* anything changes — they can also be
   anchored after the step).
2. Scheduled **interventions** due at this step fire (if any are configured).
3. Every GM's **update component** runs, serially, before any agent acts
   (with the step's roster — everyone, in this scenario). This is the
   per-step housekeeping slot; in this scenario `market_gm`'s update
   component (`dial_handler.py`) is where the day-boundary work happens
   (which is why "nobody acts" on that step — the work is in update, not in
   turns).
4. The acting GM's **next_acting** component picks who acts (calendar-gated
   here).
5. For each actor: the **observe** component builds this turn's observation
   and delivers it to the agent → the **action_prompt** component builds the
   prompt/tool schemas → the agent acts → the **resolve** component validates
   and executes the action against the backend → the result message is
   **observed back** into the acting agent (this observe-back is how
   reflections re-enter memory).
6. A checkpoint is saved per the configured cadence (each day boundary here).

Every executed backend action is appended to that GM's
**`action_events.jsonl`** — the committed action log — and mirrored in memory
where backends can query it (`iter_committed_events`; see AGENTS.md §6).

### 3.4 Configuration: how it composes, and the one gotcha

- `conf/world/*.yaml` — one file per condition (`default` = social, `asocial`,
  `asocial_personal`, plus `smoke`, a 4-buyer 2-day social run for
  development). World files set run parameters: `num_agents`, `num_days`,
  `seed`, the calendar, the condition switches.
- `conf/env/signaling.yaml` — the three GMs, their backends, and every
  component slot (this is where `class_path`s point at `components/*.py`).
- `conf/agents/default.yaml` — the two persona classes.
- `conf/sim.yaml` — engine settings (merged over the framework's defaults).
- `conf/eval.yaml` — the probe/measurement plane (off in the baseline).

**The gotcha:** `num_steps` must equal `num_days × day_length`, and
`day_length` must match the calendar block. Config cannot do arithmetic, so if
you change `num_days` or any calendar field, update `num_steps` (and
`day_length`) in the same override. Get it wrong and the run stops at startup
with an error telling you the correct numbers — that check is
`components/loop.py`.

Any config key can also be overridden on the command line
(`world=smoke num_days=3 num_steps=30 seed=7`, `+key=value` to add a new key).
The exact composed config of every run is written to its output directory as
`effective_config.yaml` — diff it when in doubt.

### 3.5 How to run

One-time setup, from the repository root:
[`uv`](https://docs.astral.sh/uv/) installed, then `uv sync --group dev`
(creates the environment and installs everything).

```bash
# One-time, per shell, from the repo root: `replications/` is repository
# content (not part of the installed package), so the repo root must be
# importable for the scenario's class_paths to resolve:
export PYTHONPATH="$PWD"

# Real model — condition = world file; OPENAI_API_KEY in .env
uv run silisocs --config-path replications/signaling/conf env=signaling world=default
uv run silisocs --config-path replications/signaling/conf env=signaling world=asocial

# No-LLM fast loop (seconds; deterministic scripted answers; every phase
# executes). The script sets PYTHONPATH itself, so it works from anywhere:
./replications/signaling/tools/run_scripted.sh smoke
./replications/signaling/tools/run_scripted.sh smoke seed=7          # + any overrides
```

Forgetting the `PYTHONPATH` export fails fast at startup with a clear
`cannot import 'replications...'` validation error — if you see that, you are
missing the export or not in the repo root.

Model selection: `SIGNALING_MODEL` env var or `sim.llm.name=...`;
provider/key config is in `docs/configuration.md`. Use the scripted loop for
all structural work — it exercises the full pipeline (orders clear, dyads
alternate, ratings parse) without a provider call.

### 3.6 Outputs

Each run writes a self-contained directory — the run prints
`Output directory: ...` at startup (a timestamped directory under `outputs/`
unless `output_rootname` is set):

| Artifact | Contents |
|---|---|
| `market_gm/`, `journal_gm/`, `date_gm/` `action_events.jsonl` | The committed action log, per GM. One JSON row per executed action: `label` (action name), `source_user` (actor), `episode` (step), `data` (structured fields — e.g. journal rows carry `day`, `kind`, `partner`, `rating`). **This is the primary data surface.** |
| `checkpoints/step_N_checkpoint.json` | Full state (every agent's memory/observations, every GM/backend/component) at each day boundary. Resume is opt-in in this scenario (it sets `sim.checkpoint.auto_resume: false`): pass `sim.checkpoint.source_run=<dir>` or `sim.checkpoint.auto_resume=true`. Also the easiest place to inspect what an agent has in memory. |
| `sim_metrics.json` | Telemetry and run-health counters. |
| `run_manifest.json` | Status, artifact index, health summary. |
| `effective_config.yaml` | The exact composed config (API keys masked). |
| `prompts_and_responses.jsonl` | Every LLM call and reply. |

Metrics: `uv run python -m replications.signaling.evaluators.metrics
--run-dir <dir>` prints status-share, per-day prices, and the rating
distribution, straight from the action logs.

### 3.7 Navigation anchors and idioms

Grep for these when orienting: `phase_of` (calendar arithmetic), `@app_action`
(how backend actions are declared/catalogued), `agent.observe` (how text
enters an agent's memory), `iter_committed_events` (reading the committed
log at runtime), `class_path` in `conf/env/signaling.yaml` (which component
fills which slot).

The framework's extension idioms (all documented in `AGENTS.md` and
`docs/configuration.md`, which ship in your repo): behavior is configured, not
driven — GM component slots (`next_acting` / `observe` / `resolve` /
`action_prompt` / `update`) accept any `class_path`; a top-level
`interventions:` schedule can fire declared actions at chosen steps; the
`eval:` group configures measurement probes; participation and turn policies
gate who acts and how often; backends expose their actions and their committed
history. When extending, the first question to ask is "is there a config
surface or slot for this?" — the second is "which component owns the closest
existing behavior?".

---

## 4. Quick comparison table

| | Concordia | silisocs |
|---|---|---|
| Orchestration | hand-written day loop in `simulation.py` | framework step loop + calendar-gated components |
| A "day" | one loop iteration; the simulation object is rebuilt daily | 91 consecutive steps in social (calendar-defined) |
| Agent memory | `__memory__` component per entity; buyers hand-copied across days | agent state; persists across steps; checkpointed |
| Market ledger | marketplace component's `_agents` dict | `MarketplaceApp` backend state |
| Ask an agent | `free_action_spec` + `act` in the loop | the GM turn pipeline (or the probe plane) |
| Give an agent info | `entity.observe(text)` | `agent.observe(text)` / backend observation queues |
| Data out | HTML logs + JSON files in `/tmp` | per-GM `action_events.jsonl` + checkpoints + metrics CLI |
| Config | CLI flags in `run.py`; behavior in code | Hydra YAML (`conf/`), CLI-overridable, recorded per run |
| No-LLM dev loop | `--disable_language_model` (placeholder text) | scripted behavior (meaningful deterministic runs) |
