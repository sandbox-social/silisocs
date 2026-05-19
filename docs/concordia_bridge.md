# Concordia Bridge

Silisocs runs on native runtime contracts by default. Concordia is no longer a
required dependency for installing, importing, or running the standard runtime.
The bridge exists only to help teams port older Concordia-designed agents,
prefabs, or context components into Silisocs incrementally.

Install the bridge extra only when you need legacy interoperability:

```sh
pip install "silisocs[concordia]"
```

## Native First

The native runtime contracts are:

| Native concept | Primary API |
|---|---|
| Agent | `silisocs.agents.base_agent.Agent` |
| Action spec | `silisocs.runtime.types.ActionSpec` |
| Output type | `silisocs.runtime.types.OutputType` |
| Runtime config records | direct `class_path` + `params` specs |
| Default agent | `silisocs.agents.native.NativeAgent` |
| Native GM helpers | `silisocs.environments.gm.components` |
| Runtime engine | `silisocs.simulation_engines` |

Every runtime agent must implement:

```python
class Agent:
    @property
    def name(self) -> str: ...
    def observe(self, observation: str) -> None: ...
    def act(self, action_spec) -> ActionOutput: ...
```

Stateful custom agents should also implement `get_state()` and
`set_state(state)` for checkpoint resume.

## Bridge Boundary

All Concordia-shaped compatibility code lives under
`silisocs.adapters.concordia`. Runtime code and native extensions should not
import `concordia.*` directly. The adapter imports the real optional
`gdm-concordia` package and exposes the limited legacy surface needed by older
agents and game masters.

Legacy configs must opt in explicitly:

```yaml
persona_pipeline:
  classes:
    old_agent:
      class_path: my_legacy.agent
      compat: concordia
```

Use the bridge only for legacy modules that are expensive to rewrite
immediately. New extensions should target native Silisocs contracts.

## Porting Guidance

- Replace direct `concordia.*` imports with native Silisocs contracts where
  practical.
- If a legacy component expects Concordia-shaped classes, import them from
  `silisocs.adapters.concordia` as a temporary compatibility step.
- Keep legacy scenario YAML stable except for the explicit `compat: concordia`
  marker.
- Prefer native agents that build context in `act()` and call
  `self._call_model(context, action_spec)`.

The long-term target is simple: user-authored extensions should not need to
know Concordia unless they are deliberately porting old Concordia code.
