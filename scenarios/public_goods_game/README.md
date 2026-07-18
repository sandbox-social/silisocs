# Public Goods Game

A repeated **linear public-goods game** (Fehr & Gächter): a minimal, non-social,
config-driven reference world for studying multi-agent cooperation.

Each round every player privately receives `endowment` tokens and chooses how many to
**CONTRIBUTE** to a shared pool. Contributions are revealed simultaneously; the pool is
multiplied by `multiplier` and split equally among all `N` players:

```
payoff_i = (endowment - contribution_i) + (multiplier / N) * pool
```

With `1 < multiplier < N`, contributing is individually dominated (free-riding maximizes
your own payoff) while full contribution is the collective optimum — so the **average
contribution rate** is a clean, standard measure of cooperative behavior.

## Why it exists

This scenario is the reproduction vehicle for the late-2025/2026 "more capable, less
cooperative" findings (e.g. *More Capable, Less Cooperative? When LLMs Fail At Zero-Cost
Collaboration*, ICML 2026; *Corrupted by Reasoning: Reasoning LMs Become Free-Riders in
Public Goods Games*, COLM 2025). Sweep a model-capability ladder as study conditions and
read the cross-seed contribution rate: see
[`experiments/studies/public_goods_capability`](../../experiments/studies/public_goods_capability).

## Backend

`public_goods` (`silisocs.environments.backends.public_goods.app.PublicGoodsApp`), a
domain-neutral `BackendApp`. The single action is `CONTRIBUTE(amount)`; the referee
(reveal + payoff) runs in the backend's per-step `update()`, and every committed
`contribute` row carries `contribution`/`endowment`/`multiplier`/`group_size` so the
cooperation metric is derived from the action log alone.

Backend `params` (in `conf/env/public_goods_game.yaml`):

| Param | Default | Meaning |
|-------|---------|---------|
| `endowment` | 20 | Tokens each player receives per round |
| `multiplier` | 1.6 | Pool multiplier (`1 < multiplier < N` keeps free-riding tempting) |
| `num_rounds` | `${num_steps}` | Rounds (display; drive length via `num_steps`) |
| `history_window` | 0 | Resolved rounds shown in the observation (0 = all) |

## Run

```bash
uv run silisocs --config-path scenarios/public_goods_game/conf \
  world=public_goods_game agents=public_goods_game env=public_goods_game \
  num_steps=10
```

No-LLM structural smoke run:

```bash
uv run silisocs --config-path scenarios/public_goods_game/conf \
  world=public_goods_game agents=public_goods_game env=public_goods_game \
  num_steps=2 sim.llm.disabled=true
```

## Framing variants

The default `action_prompt` (`conf/env/public_goods_game.yaml`) is neutral. To mirror the
*More Capable, Less Cooperative* collective-goal framing, edit the `action_prompt` to
instruct players to "maximize the group's overall payoff and cooperate," and compare
contribution rates against the neutral baseline as a second hypothesis.
