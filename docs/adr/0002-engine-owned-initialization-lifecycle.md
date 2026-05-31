# ADR 0002: Engine-Owned Initialization Lifecycle

## Status

Accepted.

## Context

The native runtime previously hid startup behind a broad runtime initializer and
extra Game Master methods such as `initialize_backend` and `post_seed_posts`.
That made the lifecycle hard to explain: agent memory setup, backend graph
setup, and application seed content were all routed through a simulator-like
object or through GM/backend special cases.

## Decision

The Engine owns startup and runs exactly three phases before the main loop:

1. Agent initialization.
2. Game Master initialization.
3. Simulation initialization.

`sim.initializer` is removed. Config uses `sim.initialization.agents`,
`sim.initialization.game_masters`, and `sim.initialization.simulation`.

Native Game Masters expose only `initialize`, `update`, `acting_agents`,
`action_prompt`, `make_observation`, and `resolve_action`. Each GM owns a
required initializer slot, and the Engine-level game-master phase calls
`gm.initialize(agents, context)`.

Seed posts are simulation initialization. The simulation initializer generates
or loads seed content, maps it to typed tool calls, and posts it through
`GameMaster.resolve_action(...)`. Backend apps expose actions; they do not own
seed-post-specific helpers.

## Consequences

- Startup order is visible and testable.
- Backend setup stays with Game Masters, while seed content follows the same
  action resolution path as ordinary agent behavior.
- Old config shapes fail loudly.
- Native code no longer needs hidden initializer GMs or backend seed-post hooks.
