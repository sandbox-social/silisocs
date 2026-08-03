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

**Missing wearable is counted, not fatal.** Upstream raises when a buyer on a
date owns nothing wearable, killing the run at day N. The port substitutes
"plain, unremarkable everyday clothes" and increments the
`signaling_missing_wearable` health counter, so the run degrades loudly instead
of dying. (Upstream text never contains that fallback sentence.)

**Window memory instead of associative retrieval.** Upstream uses an embedder-
backed associative memory plus an `ImportantMemories` component that filters by
tag. The port uses a large window (`observation_history: 400`,
`memory_history: 8000`) — enough to span several days. Carry-over is the
mechanism; retrieval is prompt engineering. This also removes the
`sentence-transformers` dependency entirely. The upstream tags
(`[Daily Personal Event N]`, `[Daily Shared Setup]`, `[Reflection]`) are still
stamped on injected observations, so switching to `sim.memory.built_in:
retrieval` later remains possible.

**Role enforcement is config, not code.** Buyers may only `BID` and sellers only
`ASK`, expressed as a per-flow action filter on the market GM's resolver.
Upstream branches on `agent.role` inside the component.

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

## Timing

**The day's final market round resolves before the reflection.** Upstream leaves
the last round's outcome queued until the next day's first observation, an
artifact of its queue-delivery timing. The port forces the resolve at the day
boundary so the "reflect on what you bought today" prompt and the wearing draw
both see the full day. Reflections therefore see one more round of outcomes than
upstream's do.

**`dial_turns` counts utterances; upstream's `--num_dial_rounds` counts engine
steps.** Upstream's 80 is `default_max_steps` for the whole dyad simulation:
the initializer's observation delivery consumes ~2-3 of those steps before the
first utterance, and the dialogic GM retains a terminate seam that can end a
conversation early. The port schedules exactly `calendar.dial_turns`
conversational turns — a slightly longer conversation at the same number. Set
`calendar.dial_turns` lower to match a measured upstream effective length.

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
  6-7 per tier), not the ~24 an earlier reading suggested. `original` and
  `synthetic` have 25 each; `both` (their merge) has 50.
- Upstream treats `item_list=synthetic` as the original+synthetic **merge**
  (identical to `both`) everywhere — market and eat/wear draws alike. The
  port's `input/goods/synthetic.json` is the synthetic-only table; use
  `item_list: both` to reproduce upstream's `synthetic` behaviour. No shipped
  condition uses either.
- Upstream's per-round supply/demand `curve_history` (an analysis artifact,
  written to its logs and read by nothing at the pin) is not reproduced;
  the port's `round_resolved` rows carry prices, trades, and order counts.
