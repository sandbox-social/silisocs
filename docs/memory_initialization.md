# Memory Initialization

The initializer game master runs once at the start of a simulation,
before the main social-media loop begins. Its job is to populate each
agent's observation stream and the GM memory bank with initial memories.

## What the Initializer Does

1. **Shared memories** (from config) are added to the GM memory bank and
   queued as observations for every agent.
2. **Generated memories** (from `generate_memories()`) are produced
   per-agent and injected as observations + GM memories.
3. **Specific memories** (per-agent, from config) are injected last.
4. Control is handed to the main simulation game master.

Steps 1–4 run concurrently across all players. After initialization,
agents have their starting knowledge and the simulation loop begins.

## Supported Modes

| Mode | Config | What it does |
|------|--------|-------------|
| **Raw** | `processing_mode: raw` | No LLM calls. Only config memories are injected. |
| **Formative** | `processing_mode: formative` or `processing_mode: llm_formative` | Uses the LLM to generate a multi-episode backstory per agent. |

Both formative names are accepted for compatibility. Prefer `formative` in
new configs.

## Current Extensibility Boundary

The runtime currently validates `processing_mode` to the two supported modes
above. A custom initializer is not loadable through YAML alone at this time.

If you need a custom initializer today, you must also update
`build_game_masters()` in `src/mastodon_sim/runtime/runner.py` to register
your mode name and module path.

## How A Custom Initializer Would Look

One class, one method — override `generate_memories()` on `InitializerGM`:

```python
# mastodon_sim/agents/initialization/my_init.py
import dataclasses
from mastodon_sim.agents.initialization.base import InitializerGM

@dataclasses.dataclass
class GameMaster(InitializerGM):
    def generate_memories(self, model, player_name, shared_memories, player_context):
        # `model` — the LLM (call model.sample_text() etc.)
        # `player_name` — e.g. "Alice Smith"
        # `shared_memories` — list of shared memory strings from config
        # `player_context` — per-player context string from config
        return [
            f"{player_name} grew up in a small town.",
            f"{player_name} has always been passionate about gardening.",
        ]
```

After adding runtime registration in `runner.py`, set
`processing_mode: my_init` in your scenario YAML.

The base class handles everything else:
- Concordia GM component wiring
- Injecting shared memories into the GM memory bank
- Queuing shared memories as observations for every agent
- Calling your `generate_memories()` for each agent (concurrently)
- Injecting player-specific memories from config
- Handing off to the main simulation GM

## Runtime Architecture

```
InitializerGM (Prefab — you subclass this)
│
├── generate_memories(model, name, shared, ctx)  ← OVERRIDE THIS
│   ├── Raw:       returns []
│   └── Formative: returns LLM-generated episodes
│
└── build() → creates Concordia GM entity with:
    ├── Instructions, Examples, PlayerCharacters
    ├── Observation, Memory, ObservationToMemory
    ├── _MemoryInitComponent (internal — calls generate_memories)
    ├── SimpleMakeObservation
    └── NextActingAllEntities, FixedActionSpec
```

Everything below `build()` is internal. You never need to touch it.

---

## Related

- [Building Agents](building_agents.md) — How agent entities are constructed
- [Configuration Reference](configuration.md) — `processing_mode` and memory config options
- [Usage Overview](usage.md#memory-initialization) — Memory initialization in context
