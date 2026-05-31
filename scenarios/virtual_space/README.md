# Scenario: virtual_space

A small virtual commons opens for a short collaborative session. Agents can move
between rooms, inspect their surroundings, leave notes for future visitors, work
on room tasks, and talk to other agents who are physically co-located.

This scenario is designed to exercise the `virtual_space` backend as a mutable
spatial environment. The interesting state is not a feed: it is room location,
co-presence, persistent notes, task progress, and room-specific observations.

## Setting

The commons has an atrium, a garden, and a workshop. The atrium starts with a
welcome-board task; other rooms include their own collaborative tasks.

## Agent Roles

| Role | Count | Agent |
|---|---:|---|
| `participant` | 4 | `silisocs.agents.native.NativeAgent` |

## Key Dynamics

- Spatial context: agents only see the room they occupy and nearby agents.
- Persistent local state: notes left in a room affect later observations.
- Collaborative tasks: multiple actions can advance or complete room tasks.
- Co-location: direct talk only succeeds when agents are in the same room.

## Run

```bash
uv run silisocs --config-path scenarios/virtual_space/conf scenario=virtual_space agents=virtual_space env=virtual_space num_steps=6
```

For a no-LLM smoke run:

```bash
uv run silisocs --config-path scenarios/virtual_space/conf scenario=virtual_space agents=virtual_space env=virtual_space sim.llm.provider=scripted sim.llm.name=scripted num_steps=1
```
