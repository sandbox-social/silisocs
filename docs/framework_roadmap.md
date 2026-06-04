# Framework Roadmap

This roadmap focuses on making Silisocs a powerful simulator and extensible
framework without losing the current YAML-first workflow.

## Target Architecture

Silisocs should make each simulator layer replaceable through a defined shape:

| Layer | Stable shape | Extension path |
|---|---|---|
| World | Hydra config groups plus world-local overrides | Add world files, agent variants, study overrides |
| Agent | `Agent` runtime plus optional compatibility adapter | Custom agent module or custom builder |
| Environment app | `BackendApp` plus `@app_action` methods | Built-in `env.gm.backend.type` or `env.gm.backend.class_path` |
| Timeline/recsys capability | `SocialBackendApp` optional timeline/recsys interface | Twitter-like, Reddit-like, Mastodon, or custom backend using those components |
| GM | Component-slotted game master | Override component slots before replacing the full GM |
| GM component | Slot config with `built_in`, `class_path`, `params`, optional `flows` | Custom initialize, next-acting, action_prompt, observe, resolve, update |
| Engine | Runtime loop plus policy objects | Custom turn-policy or probe-schedule policy first; custom engine only when needed |
| Evaluation | Probe deployment plus postprocessors/evaluators | Custom probe types and study evaluators |

## V1 Priorities

1. **Documentation completeness**
   - Keep [Documentation Coverage](documentation_coverage.md) current.
   - Make [Backends](backends.md) the canonical guide for generic and social
     backend authors.
   - Make [Simulation Extensibility API](simulation_extensibility_api.md) the
     canonical contract reference for agents, apps, GMs, components, engines,
     and policies.

2. **Strict config contracts**
   - Treat YAML `params` as public constructor arguments.
   - Fail fast on unknown `params` unless the target class accepts `**kwargs`.
   - Apply this consistently to GM components, engine policies, and custom
     environment apps.

3. **Generic simulator boundary**
   - Keep `BackendApp` domain-neutral.
   - Keep timeline/recommendation assumptions inside `SocialBackendApp`, social
     observe components, social resolve components, and recommendation components.
   - Use `resource_market` and `virtual_space` as small reference-world backends.

4. **Contract tests**
   - Test each extension shape with one small custom implementation.
   - Include negative tests for bad config keys, unknown actions, and missing
     required app methods.
   - Keep fast non-LLM smoke tests for packaged sample worlds.

5. **Configuration discoverability**
   - Document `env.gm.backend.class_path` and `env.gm.backend.params`.
   - Document component slot `params` and `flows`.
   - Add generated or checked config tables once the schema settles.

## Feature Backlog

| Priority | Feature | Why it matters |
|---|---|---|
| P0 | Strict constructor validation for extension `params` | Prevents silent YAML typos and broken custom components |
| P0 | Reference-world backend docs and samples | Proves Silisocs is a simulator framework, not only social media |
| P1 | Capability matrix for backends | Makes optional timeline/recsys/live-server features explicit |
| P1 | Public/internal API labels | Helps extension authors avoid unstable modules |
| P1 | Typed config schema or generated config reference | Reduces drift between YAML defaults and docs |
| P1 | Plugin-style discovery for backends/components | Avoids editing core factories for every extension |
| P2 | Deterministic replay harness | Improves experiment auditability and regression tests |
| P2 | Benchmark world suite | Tracks runtime, cost, scale, and behavioral regressions |
| P2 | Rich evaluator registry | Makes study analysis as configurable as simulation setup |
| P2 | Distributed/batched execution | Enables larger experiment grids without custom orchestration |

## Acceptance Markers

The framework is in good V1 shape when:

- A new backend can be added through `env.gm.backend.class_path` without changing core
  runtime code.
- A new GM component or engine policy can be configured with documented
  `params`, and typos fail before simulation starts.
- Public docs describe every stable extension surface.
- Reference-world smoke tests run without timeline or recommendation
  assumptions.
- `uv run pytest` and `uv run --group docs properdocs build --strict` pass in the contributor
  environment.
