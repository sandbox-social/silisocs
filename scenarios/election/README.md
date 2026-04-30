# Scenario: election

A small-town mayoral election (Storhampton) with two candidates on opposite sides
of economic vs. environmental policy. Studies partisan opinion dynamics, media framing
effects, and voter persuasion on social media.

## Setting

Storhampton — a post-industrial town of ~2,500 with economic anxiety and partisan
tension between native-born and immigrant communities.

## Agent roles

| Role | Count | Prefab |
|---|---|---|
| `voter` | N-3 | `silisocs.agents.entity` (external persona pipeline) |
| `candidate` | 2 | custom `input/entity_lib/candidate.py` |
| `news_account` | 1 | custom `input/entity_lib/` (fixed posting schedule) |

Voter personas drawn from external HuggingFace dataset pipeline (`input/personas/`).
News agent posts real-time headlines from `input/news_data/` with configurable bias.

## Key dynamics

- Opinion trajectory under biased vs. unbiased news framing
- Partisan echo chambers and cross-partisan exposure
- Candidate strategy and voter mobilization

## Run

```bash
uv run silisocs --config-path scenarios/election/conf num_steps=15
```

## Studies using this scenario

_(add links to `experiments/studies/` entries here)_
