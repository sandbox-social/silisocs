# Scenario: election

A small-town mayoral election (Storhampton) with two candidates on opposite sides
of economic vs. environmental policy. Studies partisan opinion dynamics, media framing
effects, and voter persuasion on social media.

## Setting

Storhampton — a post-industrial town of ~2,500 with economic anxiety and partisan
tension between native-born and immigrant communities.

## Agent roles

| Role | Count | Default agent class |
|---|---|---|
| `voter` | N-3 | `silisocs.agents.native` (external persona pipeline) |
| `candidate` | 2 | `silisocs.agents.native.NativeAgent` |
| `news_account` | 1 | `silisocs.agents.native.NativeAgent` with fixed news action plans |

Voter personas drawn from external HuggingFace dataset pipeline (`input/personas/`).
News agent posts real-time headlines from `input/news_data/` with configurable bias.

The default config is native. The files under `input/entity_lib/` are preserved
as optional Concordia-compatible world agents for legacy experiments. To use
them, configure the relevant persona-pipeline class with `compat: concordia`
and the explicit `class_path`; they are not imported by the native default run.

## Key dynamics

- Opinion trajectory under biased vs. unbiased news framing
- Partisan echo chambers and cross-partisan exposure
- Candidate strategy and voter mobilization

## Run

```bash
uv run silisocs --config-path worlds/election/conf num_steps=15
```

## Studies using this world

_(add links to `experiments/studies/` entries here)_
