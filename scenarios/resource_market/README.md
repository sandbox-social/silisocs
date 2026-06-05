# Scenario: resource_market

A small pop-up exchange opens for one market day. Four agents have different
production capacities and different upkeep needs, so cooperation is useful:
farmers can make food, woodworkers can make wood, miners can make ore, and
merchants can bridge small gaps.

This world is designed to exercise the `resource_market` backend as a
state-changing environment rather than a social feed. Agent actions change
inventory, listings, cash, transfers, and satisfaction over time.

## Setting

The exchange is a local resource market with lightweight rules. Participants can
inspect the market, produce resources, list inventory, cancel listings, buy from
open listings, transfer resources directly, consume resources, or finish a turn.

## Agent Roles

| Role | Count | Agent |
|---|---:|---|
| `farmer` | 1 | `silisocs.agents.native.NativeAgent` |
| `woodworker` | 1 | `silisocs.agents.native.NativeAgent` |
| `miner` | 1 | `silisocs.agents.native.NativeAgent` |
| `merchant` | 1 | `silisocs.agents.native.NativeAgent` |

## Key Dynamics

- Role specialization: agents can produce different resources.
- Mutual dependence: upkeep needs make direct transfers and market listings useful.
- Market state: listings, purchases, and cancellations change what later agents see.
- World updates: the backend update hook applies upkeep and satisfaction changes.

## Run

```bash
uv run silisocs --config-path scenarios/resource_market/conf world=resource_market agents=resource_market env=resource_market num_steps=6
```

For a no-LLM smoke run:

```bash
uv run silisocs --config-path scenarios/resource_market/conf world=resource_market agents=resource_market env=resource_market sim.llm.provider=scripted sim.llm.name=scripted num_steps=1
```
