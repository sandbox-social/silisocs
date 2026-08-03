# Signaling Marketplace Replication

A faithful-mechanism SiliSocS port of Concordia's `examples/signaling` — the
multi-day marketplace-plus-first-date experiment from *"A Generative Model of
Conspicuous Consumption and Status Signaling"*. Agents shop each morning in a
clearing-house double auction, then meet a new partner each evening wearing
something they bought.

Both arms are pinned: see [docs/PINNED_COMMITS.md](docs/PINNED_COMMITS.md).
The design and its rationale are in [docs/DESIGN.md](docs/DESIGN.md); every
deliberate divergence from upstream is recorded in
[docs/port_notes.md](docs/port_notes.md).

**Fidelity target is mechanism, not numbers.** The port reproduces the causal
structure — clearing-house price formation, memory carry-over across days, the
consumption-visibility manipulation, the three conditions — not the paper's
Gemma-3-27B-specific magnitudes.

## Layout

```text
replications/signaling/
  components/     backends, agents, GM components, the day calendar, prompts
  conf/           Hydra config: three conditions + a smoke world
  input/          converted goods tables, 50 LA personas, generated sellers
  evaluators/     metrics derived from the committed action log
  tools/          the one-shot data conversion + the LLM-free run script
  docs/           design, pinned commits, fidelity deviations
```

## How a day is structured

SiliSocS's unit is the step; the experiment's unit is the day. A day is a fixed
block of steps, and `components/calendar.py` is the whole bridge:

| Steps in a day | Phase | Game master | Who acts |
|---|---|---|---|
| `0 … R-1` | market round | `market_gm` | buyers + sellers |
| `R` | market reflection | `journal_gm` | buyers |
| `R+1` | DIAL setup | *(nobody acts)* | the day-boundary ceremony (`market_gm`'s update component) |
| `R+2 … R+1+D` | date turn | `date_gm` | one speaker per dyad |
| last 4 | post-date reflections | `journal_gm` | buyers |

With the upstream defaults (`R=5`, `D=80`) a social day is 91 steps and a
5-day run is 455. Exactly one game master acts on any step — the others' next-
acting components return an empty roster — which is why the stock concurrent
`multi_gm` traversal is correct with no custom engine or step strategy.

## The three game masters

- **`market_gm`** — `MarketplaceApp`, a `SimultaneousRoundGame` subclass. Sealed
  simultaneous `BID`/`ASK`, cleared at the round boundary by logic transcribed
  verbatim from upstream. Owns cash, inventories, order books, price history,
  and the eating/wearing draws that form the signaling channel.
- **`journal_gm`** — `JournalApp`. Free-text reflections (this GM overrides
  `tool_calling: none`). The four post-date reflections carry the study's
  dependent variable, the 0-10 partner rating, which is parsed into the
  committed row.
- **`date_gm`** — `DateMessagingApp`, a thin `MessagingApp` subclass that scopes
  visibility to today's partner and today's date phase.

Two flows route them: `buyer: [market_gm, journal_gm, date_gm]` and
`seller: [market_gm]`. Role enforcement (buyers bid, sellers ask) is a per-flow
action filter in config, not backend code.

## Running

Conditions are world configs; the environment, agents, and game masters are
identical across all three.

```bash
# social (marketplace + personal events + dates)
uv run silisocs --config-path replications/signaling/conf env=signaling world=default

# marketplace only
uv run silisocs --config-path replications/signaling/conf env=signaling world=asocial

# marketplace + personal events, no dates
uv run silisocs --config-path replications/signaling/conf env=signaling world=asocial_personal
```

Model selection is `SIGNALING_MODEL` (default `gpt-4o-mini`) or
`sim.llm.name=...`; put `OPENAI_API_KEY` in `.env`.

### Without a language model

```bash
./replications/signaling/tools/run_scripted.sh smoke     # 4 buyers, 2 days, seconds
./replications/signaling/tools/run_scripted.sh asocial
```

`components/scripted_behavior.py` answers every prompt deterministically, so
each phase really executes — orders clear, dyads alternate, ratings parse —
with no provider calls. This is the fast loop for config changes and what the
end-to-end test drives.

### Measuring a run

```bash
uv run python -m replications.signaling.evaluators.metrics \
  --run-dir <run output dir> --output metrics.json
```

Reports the High-quality share of orders (overall and per day), mean clearing
price per good per day with its first-to-last change, the rating distribution,
and the rate at which date utterances name an owned item.

## Regenerating `input/`

`input/` is derived from the pinned Concordia checkout and is committed, so a
run needs no Concordia install. Re-run the converter only when re-pinning:

```bash
uv run python replications/signaling/tools/convert_concordia_data.py \
  --concordia-root /path/to/concordia@7779a4c
```

## Tests

```bash
uv run pytest tests/test_signaling_replication.py
```

The two load-bearing tests are differential: the clearing house and the dyad
schedule are compared against the *verbatim pinned upstream source*, executed
in-process by `tests/signaling_pinned.py` with Concordia's imports stubbed (no
Concordia install required). They skip automatically when the pinned checkout is
absent — set `CONCORDIA_SIGNALING_ROOT` to point at it.
