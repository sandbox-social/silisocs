# ADR 0001: Native Core and Concordia Compatibility Boundary

## Status

Accepted.

## Context

SiliSocS originally borrowed Concordia-style object shapes for agents, game
masters, components, documents, and runtime dispatch. That made config-driven
simulation possible, but it also left the normal runtime coupled to Concordia
concepts such as component entities, concat-act components, interactive
documents, and string-shaped action prompts.

The target architecture is a smaller native core with four pillars: agents,
environment, evaluations, and engine. Concordia should remain usable only for
legacy modules that explicitly opt in.

## Decision

- The normal runtime is native and does not import `concordia.*`.
- Local Concordia-like copies under the native core are removed or turned into
  loud migration errors.
- The default agent is `NativeAgent`, which builds context and calls
  `Agent._call_model(context, action_spec)`.
- Native `ActionSpec` uses `prompt`, `output_type`, `options`, `tag`, and
  `extra_args`; it does not expose `call_to_action`.
- Native `env.gm.components` remains the config term, but native components are
  plain SiliSocS slot helpers. They expose direct slot methods and do not expose
  Concordia lifecycle hooks such as `pre_act`, `post_act`, `set_entity`, or
  `get_entity`.
- Concordia support lives only in `silisocs.adapters.concordia`, backed by the
  real optional `gdm-concordia` package.
- Legacy agents or game masters require explicit `compat: concordia` in config.

## Consequences

- New extensions target the native interfaces directly.
- Compatibility failures happen at construction time with clear migration
  messages instead of silently falling back to old string dispatch.
- Shipped result-sensitive legacy modules can keep stable import paths while
  routing through the adapter.
