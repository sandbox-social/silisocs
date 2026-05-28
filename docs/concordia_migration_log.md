# Concordia Migration Log

Silisocs began with several Concordia-shaped runtime concepts: agents built
from component entities, game masters driven through string dispatch, local
copies of Concordia helper classes, and initializer game masters. Those shapes
made early experiments possible, but they made the code harder to install,
extend, and explain.

The native migration replaced that structure with four direct pillars:
Agents, Environment, Evaluations, and Engine.

## What Changed

- Native agents now implement the Silisocs `Agent` contract and route model
  calls through `Agent._call_model(context, action_spec)`.
- Native action requests use `ActionSpec(prompt, output_type, options, tag,
  extra_args)` and native responses use typed `ActionOutput` values.
- Native game masters own one `backend` plus typed component slots:
  initialize, update, next acting, action prompt, observation, and resolution.
- Backends own domain state and executable `@app_action` methods. Social
  timeline behavior lives in `SocialBackendApp`; generic backends subclass
  `BackendApp`.
- Engine startup is explicit: agent initialization, game-master initialization,
  simulation initialization, then the main loop.
- Seed posts moved into simulation initialization and are posted through normal
  typed actions.
- Checkpoint restore moved under `sim.checkpoint.source_run` plus a restore
  strategy.

## Compatibility Boundary

Concordia support remains available only through explicit opt-in:

```yaml
compat: concordia
```

Adapter code lives under `silisocs.adapters.concordia` and uses the real
optional `gdm-concordia` package. Native runtime code must not import
`concordia.*`, local Concordia-like component copies, or Concordia lifecycle
methods.

## Removed Public Shapes

- Concordia-style native component lifecycle hooks such as `pre_act` and
  `post_act`.
- Native `call_to_action`; the native field is `prompt`.
- Native prefab/entity construction for normal agents.
- Initializer game masters.
- Whole-config reads from runtime objects.
- `env.platform_type` and `env.app.*`; backend selection is now
  `env.backend.type`, `env.backend.class_path`, and `env.backend.params`.
- The old `local` LLM provider alias; OpenAI-compatible endpoints use
  `openai_compatible`.

The current bridge exists for porting old modules, not for new Silisocs
extensions.
