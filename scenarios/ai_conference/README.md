# Scenario: ai_conference

An AI industry conference and a simultaneous street protest share the same social
media platform. Studies echo chamber formation, spiral of silence among doubters,
and whether bridge characters transmit ideas across the insider/outsider divide.

## Setting

NeuraTech Summit 2025 — a downtown convention center with 200+ protesters outside,
both groups active on the same platform.

## Agent roles

| Role | Count | Prefab |
|---|---|---|
| `conference_attendee` | 4 | `silisocs.agents.native` |
| `protester` | 3 | `silisocs.agents.native` |
| `bridge` | 2 | `silisocs.agents.native` |

Bridge characters (tech journalist, conflicted ML engineer) follow both communities
and are the only cross-cutting information conduits.

## Key dynamics

- Echo chamber solidification within each group
- Spiral of silence: who suppresses private doubts in public posts?
- Bridge transmission: do insider doubters pick up protest ideas?

## Run

```bash
uv run silisocs --config-path scenarios/ai_conference/conf num_steps=8
```

## Studies using this scenario

_(add links to `experiments/studies/` entries here)_
