# Scenario: misinformation

A small social media community where a false health claim circulates. Studies how
misinformation propagates, gets countered, or amplifies across a network of spreaders,
skeptics, and passive bystanders.

## Setting

A 6-user social media community with asymmetric follow structure and distinct personality types.

## Agent roles

| Role | Count | Prefab |
|---|---|---|
| `user` | 6 | `silisocs.agents.native` |

Archetypes: sensationalist spreader, laid-back bystander, skeptical fact-checker,
emotionally-driven sharer, passive observer, moderate engager.

## Key dynamics

- How a single seed post propagates (or dies) across a small heterogeneous network
- Interplay between emotional contagion and skeptical pushback
- Effect of network asymmetry on information reach

## Run

```bash
uv run silisocs --config-path scenarios/misinformation/conf num_steps=10
```

## Studies using this world

_(add links to `experiments/studies/` entries here)_
