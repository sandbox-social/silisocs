# Study: public_goods_capability

Reproduces the late-2025/2026 **"more capable, less cooperative"** finding
(*More Capable, Less Cooperative? When LLMs Fail At Zero-Cost Collaboration*,
ICML 2026; *Corrupted by Reasoning: Reasoning LMs Become Free-Riders in Public
Goods Games*, COLM 2025) as a **config-driven, seeded, N-replicate** study — the
reproducible-harness capability those bespoke single-run papers lack.

## Design

- **Scenario**: [`public_goods_game`](../../../scenarios/public_goods_game) — a
  canonical repeated linear public-goods game (N=4, endowment=20, multiplier=1.6,
  T=10 rounds). Free-riding is individually dominant; full contribution is the
  collective optimum, so the average contribution rate is a clean cooperation
  measure.
- **Independent variable**: model capability. `h1_capability_ladder` sweeps a
  model ladder (`sim.llm.name`) — edit the conditions to your available models.
- **Replication**: 5 seeds per condition (`seed_start: 101`, `seed_repeats: 5`).
- **Metric** ([`eval.py`](eval.py), wired via `preset: builtin.study_eval`):
  `avg_contribution_rate` (headline), plus `efficiency`, `collective_payoff`,
  `pct_of_optimal`, and `free_rider_share`, derived deterministically from the
  committed `contribute` action log (no extra LLM calls).

## Run

```bash
# Preview the expanded conditions x seeds plan (no execution):
uv run silisocs-study --study experiments/studies/public_goods_capability plan

# Full run (needs API access for the model ladder):
uv run silisocs-study --study experiments/studies/public_goods_capability run \
  --max-concurrent 4 --yes
```

## Read the result

Cross-seed statistics (n / mean / stdev / ci95_low / ci95_high) per model rung are
written to:

```
experiments/studies/public_goods_capability/generated/organized/summary.json
  -> metrics_stats_by_condition["h1_capability_ladder"]["model=..."]["avg_contribution_rate"]
```

The reproduction target: `avg_contribution_rate` should **decrease** as the model
rung becomes more capable / reasoning-heavy.

## Notes

- Keep **one scenario** so the CI pools seeds only (a pure cross-seed interval).
- The `public_goods_game` scenario's `action_prompt` is neutral. To also test the
  collective-goal framing from *More Capable, Less Cooperative* ("maximize the
  group's payoff and cooperate"), add a second hypothesis whose conditions override
  `env.gm.components.action_prompt.params.action_prompt`, holding the model fixed.
- API access is required to reproduce the effect across a capability ladder. A
  `sim.llm.disabled=true` run validates config composition only — the no-op
  model emits no tool calls, so agent turns degrade and nothing is committed.
  The full structural gate (real engine, committed contributions, evaluator
  output) is `uv run pytest tests/test_scripted_backend_matrix.py -k public_goods`.
- The evaluator excludes an all-silent replicate (`None`) unless the run's
  manifest shows a healthy run, in which case total silence scores as full
  defection (0.0) — see `eval.py`'s docstring for the exact rule.
