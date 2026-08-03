# Port notes — deliberate deviations from the pinned Concordia

Fidelity target is **mechanism**, not numbers (see [DESIGN.md](DESIGN.md) §1).
Every divergence below is deliberate; anything discovered later that is *not*
listed here is a bug.

Baselines: Concordia `7779a4c`, SiliSocS `v0.4.0` — [PINNED_COMMITS.md](PINNED_COMMITS.md).

## Structural

**One persistent agent instead of two prefabs plus memory copying.** Concordia
rebuilds its whole simulation object graph per day and per dyad, then hand-copies
`__memory__` state between the old and new entities (`simulation.py:316-329`,
`dial.py:216-227`). That is a lifecycle workaround, not mechanism: the consumer
and conversation prefabs differ only in which question chain runs before the
answer. SiliSocS agents persist for the whole run, so `SignalingAgent` switches
chain on the offered action surface and the copying layer disappears. Memory
carry-over — the actual mechanism — is preserved and tested across a checkpoint.

**Reflections are engine steps, not driver-loop calls.** Upstream issues the
one post-market and four post-date reflections as bare `agent.act` +
`agent.observe` calls between phases. Here they are turns at `journal_gm`, which
(a) lets ~N reflection calls per boundary run concurrently instead of serially,
and (b) puts every reflection — including the parsed 0-10 rating, the study's
dependent variable — in `action_events.jsonl` with no bespoke logging. The
prompts, their order, and the feed-back-into-memory step are unchanged.

**Dyads run concurrently within a step.** Upstream runs the ~25 dyads serially
because each needs its own simulation object. Dyads are causally independent —
upstream's own assumption — so the port advances them together, one utterance
per dyad per step.

**A `dial_setup` step exists in every condition.** The day-boundary ceremony
needs an anchor between the market reflection and the first date turn. Keeping
the (empty) slot even in the asocial arms costs one no-op step per day and keeps
day indices aligned across conditions.

## Interfaces

**Typed tool calls instead of JSON-in-prose.** Upstream's marketplace emits a
free-text answer containing a JSON blob and recovers the order with a regex
(`marketplace.py::_resolve`); its date turns emit `` `{name} -- "..."` ``
speech text. The port issues the same decisions as `BID`/`ASK`/`SEND_MESSAGE`
tool calls. Every sentence describing *the decision* is preserved verbatim —
the per-role marketplace CTAs and the date CTA are issued from `prompts.py`
constants — and only the output-format clauses are dropped (the JSON `Format:`
/ `Return only the JSON.` lines, the word "JSON" inside them, and the speech
template with its three examples), because the schema now supplies the
envelope. **Direction of the effect:** order submission is more reliable than
upstream's regex path, which silently drops unparseable answers. This narrows,
not widens, the gap between conditions.

**Missing wearable / failed generation is counted, not fatal.** Upstream
asserts when a buyer on a date owns nothing wearable (or an eating/wearing
statement is empty), killing the run at day N. The port substitutes "plain,
unremarkable everyday clothes" (a sentence upstream never contains) and skips
a failed scene/events generation, incrementing `signaling_missing_wearable` /
`signaling_dial_generation_failures`. Caveat: these are replication-local
counters — they appear in `sim_metrics.json`'s counter dict but are NOT in the
framework's `HEALTH_COUNTERS` registry, so they do not surface in the run-end
degraded warning or the manifest `health` block. Check `sim_metrics.json`
after a run.

**Window memory instead of associative retrieval.** Upstream uses an embedder-
backed associative memory plus an `ImportantMemories` component that filters by
tag. The port uses a large window (`observation_history: 400`,
`memory_history: 8000`) — enough to span several days. Carry-over is the
mechanism; retrieval is prompt engineering. This also removes the
`sentence-transformers` dependency entirely. The upstream tags
(`[Daily Personal Event N]`, `[Daily Shared Setup]`, `[Reflection]`) are still
stamped on injected observations, so switching to `sim.memory.built_in:
retrieval` later remains possible.

**Role enforcement is an addition, not a transcription.** Buyers may only
`BID` and sellers only `ASK`, expressed as a per-flow action filter on the
market GM's resolver plus a backend-side check. Upstream branches on
`agent.role` only to choose which call-to-action and observation an agent
gets — it never rejects a submitted order, so a consumer ASK would be booked
there and is refused here. The port also validates orders more strictly than
upstream's `if not all([...])` (price must be a positive finite number, qty
≥ 1), which closes upstream's negative-quantity exploit (a negative-value
trade *increases* buyer cash there) at the cost of refusing orders upstream
would book.

## Randomness

**Seeded, per-agent-per-day streams instead of the global RNG.** Upstream calls
`random.choice` on the global RNG for the eating draw, the wearing draw, the
date theme, and the fixed-price rationing shuffle. A concurrent, resumable
engine cannot reproduce a global RNG's call order, so the port derives a stream
from `(seed, day, agent, purpose)` for each. Distributions are unchanged; exact
draws differ from upstream. Tested for replay stability.

**No numeric RNG parity for cash draws.** `generate_cash_values` is ported
verbatim (both `mixed` and `pareto`) but runs on a seeded
`numpy.random.default_rng(seed)` rather than the global numpy RNG, so the
distribution matches and the individual values do not.

**The buyer roster is a prefix, not a sample.** Upstream takes a seeded
`random.sample` of its 50 personas. The port takes the first `num_agents` in
file order, so the roster is a pure function of config and the dyad schedule is
reproducible from the config alone. The dyad *algorithm* is verbatim and is
tested for exact equality against upstream on the same roster.

**Model plane defaults differ from upstream's.** Upstream's `run.py` defaults
to `--model_name=gpt-4o` at the provider-default temperature 1.0 (Concordia's
`DEFAULT_TEMPERATURE`; nothing in the example overrides it). The port defaults
to `gpt-4o-mini` (`conf/sim.yaml`, `SIGNALING_MODEL` env override) at
temperature 0.5. These are the largest uncontrolled levers on any magnitude
comparison against the paper: for a faithful reproduction run set
`SIGNALING_MODEL=gpt-4o sim.llm.temperature=1.0`.

**The persona blob is context, not memory.** Upstream loads the raw
`[Persona] {json}` string into associative memory alongside the formative
memories; the port promotes its fields (description, traits, initial context)
into the agent's always-on `context` instead. Every persona fact still reaches
the agent — more salient than upstream's retrieval, not less.

## Timing

**The day's final market round resolves before the reflection — on both
sides.** Upstream's simultaneous engine resolves each round inside its own
step, so the day's last round is cleared before the reflection loop and the
wearing draw; only the outcome *messages* stay queued until the next market
observation. The port is identical: the every-step backend update resolves
each finished round at the next step, and the market queue drains only on
market-phase observations. No divergence.

**`dial_turns` counts utterances; upstream's `--num_dial_rounds` counts engine
steps.** Upstream's 80 is `default_max_steps` for the whole dyad simulation:
the initializer's observation delivery consumes ~2-3 of those steps before the
first utterance, and the dialogic GM retains a terminate seam that can end a
conversation early. The port schedules exactly `calendar.dial_turns`
conversational turns — a slightly longer conversation at the same number. Set
`calendar.dial_turns` lower to match a measured upstream effective length.

## Upstream is broken at the pin (the port implements the documented design)

Two defects in the pinned upstream mean its *executed* behavior differs from
its *documented* design; the port implements the design. Both confirmed by
live runs (2026-08-03, Python 3.12, `--disable_language_model
--use_dummy_embedder`, one day, four agents).

- **`--condition=social` crashes at the first dyad.** `dial.py`'s prefab
  registry has no `'dialogic__GameMaster'` key, but `dial.py:176` requests
  that prefab and the builder indexes the dict unguarded. Confirmed: the run
  exits 1 with `KeyError: 'dialogic__GameMaster'` raised from
  `dial.py:241 → create_simulation_for_dyad → generic.py:164` after the
  market completes. Upstream's flagship arm is unrunnable at the pin.
- **`--condition=asocial_personal` never generates personal events.** With
  the shared setup skipped the dyad sim has exactly one game master, the
  sequential engine short-circuits `NEXT_GAME_MASTER`, and the DITL
  initializer's `_process_dyad` only fires under a `NEXT_GAME_MASTER` spec —
  so nothing is ever generated or injected; the arm spins its steps on
  make-observation LLM filler. Confirmed: the run completes (it never
  requests the missing prefab — corroborating the one-GM path) and writes no
  dyad logs. The port's middle arm is content-bearing where upstream's is
  empty.

Consequence for any magnitude comparison: for the date ceremony,
conversation, and post-date reflections there is no *executable* upstream
baseline — fidelity claims for those surfaces are source-level (verbatim
prompts, transcribed logic, differential tests against the pinned sources),
not run-level.

## Known composition differences (deliberate, direction understood)

These do not change any prompt literal or event, but change how text
accumulates in context:

- **The date transcript is re-observed cumulatively.** Upstream delivers each
  utterance once, to both partners, as one memory entry. The port's date
  backend shows the acting agent the day's transcript-so-far at each of its
  turns (speaker's own line included in the transcript, not as a separate
  memory), so a date contributes O(n²) transcript lines to the context window
  vs upstream's O(n). Same information, heavier repetition.
- **The marketplace goal is standing context.** Upstream's conversational
  prefab carries no goal component, so dates and post-date reflections run
  without the "buyer at the marketplace" goal; the port's single persistent
  agent keeps it all day.
- **Journal steps are framed.** The port's journal backend delivers a short
  framing observation ("JOURNAL — day N", "reflection k of n") before each
  reflection; upstream reflections arrive with no preceding observation.
- **Ceremony observations arrive as separate entries.** Upstream joins the 5
  personal events + scene into one queued observation; the port issues them
  as separate `observe` calls. Identical text; different memory chunking
  (irrelevant for window memory, relevant under retrieval).
- **`trade_history` rows stamp the engine step as `round`.** Upstream's rows
  carry round-in-day (0..R−1, reset daily) with `day` stamped externally; the
  port's carry the global step plus `day`. Use the `round_resolved` action
  events (which carry `round_in_day`) for per-round analysis.
- **The dyad schedule is derived, not stored.** Upstream computes it once on
  day 0 and reuses it; the port derives it per ceremony from the live roster
  (memoized, and independently in the date GM's turn selection). Identical
  under the static rosters of every shipped condition; a mid-run roster
  mutation would desync the two derivations, which upstream structurally
  cannot do.

## Out of scope (no upstream baseline exists at the pin)

- **Part 2, non-monetary signaling.** The upstream README references
  `configs/signaling.py`; that file does not exist at the pinned commit.
- **Kerala personas.** `personas.py` is Los Angeles only.
- **Day-6 influencer confederates.** Described in the paper; no condition flag
  in the code.
- **`--item_list=neutral_tag`.** Documented in the README, rejected by `run.py`'s
  `choices`.
- **`add_sellers=False` (fixed prices).** Reachable in
  `simulation.run_experiment` but hardcoded `True` in `run.py`, so it is in the
  library and not in the product. The port mirrors this exactly: `MarketplaceApp`
  implements `market_type: fixed_prices` and no shipped condition selects it.

## Observed data differences

- The `subculture` goods table has **49** items (Clothing and Accessories carry
  6-7 per tier). `original` and `synthetic` have 25 each; `both` (their merge)
  has 50.
- Upstream is internally inconsistent about `item_list=synthetic`: the
  **market** uses the synthetic-only table (`simulation.py:143-144`), while
  the **eat/wear draws** merge original+synthetic (`dial.py:63` treats
  `synthetic` and `both` alike). The port's `input/goods/synthetic.json` is
  the synthetic-only table, so `item_list: synthetic` reproduces upstream's
  market and diverges on the eat/wear pool; `item_list: both` reproduces the
  eat/wear pool and diverges on the market. No shipped condition uses either.
- Upstream's starting-outfit draw has no `both` branch
  (`personas.py:4691-4697` falls through to `ORIGINAL_GOODS`), so under
  `item_list=both` upstream seeds outfits from original's 2 Clothing/Low
  items while the port draws from the active table's 4. Likely an upstream
  missing-branch bug; unreachable from any shipped condition.
- Upstream's per-round supply/demand `curve_history` (an analysis artifact,
  written to its logs and read by nothing at the pin) is not reproduced;
  the port's `round_resolved` rows carry prices, trades, and order counts.
