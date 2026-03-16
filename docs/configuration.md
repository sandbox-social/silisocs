# Configuration Reference

Complete reference for all YAML configuration options.

## Top-Level Config

```yaml
# src/mastodon_sim/conf/config.yaml
defaults:
  - sim: base                # Simulation parameters
  - social_media: twitter_like  # Platform backend
  - scenario: default        # Scenario definition
  - _self_

experiment_name: independent
```

The `defaults` list determines which sub-configs are composed. Override from
the CLI:

```sh
uv run mastodon-sim social_media=reddit_like scenario=my_scenario
```

---

## Simulation Parameters (`sim/base.yaml`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `llm_name` | `qwen3.5-4b` | LLM model name (passed to Concordia model factory) |
| `llm_api_base` | `null` | Custom API base URL (for OpenAI-compatible endpoints) |
| `llm_api_key` | `null` | API key (or set via environment variable) |
| `disable_language_model` | `false` | Use a no-op model (for testing) |
| `num_agents` | `500` | Number of agents to create |
| `num_steps` | `200` | Simulation steps to run |
| `max_concurrent_actions` | `1000` | Max parallel LLM calls per step |
| `run_name` | `run1` | Run identifier (used in output path) |
| `seed` | `1` | Random seed |
| `sentence_encoder` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model for associative memory |
| `memory_backend` | `list` | Memory type: `list` (fast) or `associative` (embedding-based) |
| `action_mode` | `custom` | Action parsing: `custom`, `generic`, or `tool_calling` |
| `gm.preset` | `social_media_default` | Prebuilt GM component preset |
| `gm.components.*` | *(slots)* | YAML-selectable GM components (`next_acting`, `observe`, `resolve`, `initializer`) |
| `timeline_posts` | `10` | Number of posts shown in agent timeline |
| `observation_history` | `100` | Max observations kept in agent memory |
| `write_html_log` | `true` | Generate Concordia HTML logs |
| `roleplaying_instructions` | *(template)* | System prompt injected into every agent. Use `{name}` placeholder. |

### GM Components (SwitchAct-style Configurability)

Game master behavior is configurable through component slots:

```yaml
sim:
  gm:
    preset: social_media_default
    components:
      next_acting:
        built_in: activity_markov
        class_path: null
        params: {}
      observe:
        built_in: timeline_every_turn
        class_path: null
        params: {}
      resolve:
        built_in: parsed_action
        class_path: null
        params: {}
      initializer:
        built_in: backend_default
        class_path: null
        params: {}
```

Built-in aliases:

- `next_acting`: `activity_markov`, `all_entities`, `fixed_order`
- `observe`: `timeline_every_turn`, `chunk_start_only`
- `resolve`: `parsed_action`, `generic_action`, `tool_calling`
- `initializer`: `backend_default`

For advanced use, provide `class_path` and optional `params` to load a custom
component implementation.

See [Environment Layer](environment_layer.md) for extension patterns and examples.

### Replacing the Entire GM Prefab

For full orchestration customization, point the GM module path to your own prefab module:

```yaml
social_media:
  gamemaster:
    sim_role:
      module_path: my_scenario.custom_game_master
```

This bypasses slot-level customization and lets you own the complete GM build flow.

---

## Platform Backends (`social_media/`)

### Twitter-like (default)

```yaml
# social_media/twitter_like.yaml
platform_type: twitter_like
use_server: false              # Local SQLite backend
```

Supports: `POST`, `REPLY`, `REPOST`, `LIKE`. Character limit: 280.

### Reddit-like

```yaml
# social_media/reddit_like.yaml
platform_type: reddit_like
use_server: false              # Local SQLite backend
```

Supports: `POST`, `COMMENT`, `UPVOTE`, `DOWNVOTE`. Subreddit-based organization.

### Mastodon (Remote)

```yaml
# social_media/mastodon.yaml
platform_type: mastodon
use_server: true               # Requires a running Mastodon server
```

Requires environment variables for server URL and API credentials. See
[Installation](installation.md) for `.env` setup.

---

## Scenario Config (`scenario/`)

Scenario files define the simulation content. They must include a `# @package scenario`
header when placed in external directories.

### Required Fields

| Field | Description |
|-------|-------------|
| `scenario_name` | Unique identifier |
| `setting.name` | Setting name |
| `setting.background` | List of background description strings |
| `event.name` | Event name |
| `event.context` | Event context (string or multiline) |

### Persona Pipeline

```yaml
persona_pipeline:
  processing_mode: raw          # raw | formative | llm_formative

  defaults:                     # Applied to all classes
    params:
      scenario_context: ${scenario.event.context}
      seed_post: ""
      bio: ""
      style: ""
      goal: null
    shared_memories:
      - "A shared memory for all agents."
    field_map:
      context: persona

  classes:
    <class_name>:
      count: 100                # Number of agents in this class
      prefab_module: mastodon_sim.agents.entity  # Entity module path
      sim_role_name: user       # Role name for activity rates
      model: null               # Per-class LLM override
      data:
        source: hf_dataset      # hf_dataset | local_json | inline | config_path
        dataset: nvidia/Nemotron-Personas-USA
        split: train
        subset: null            # Optional HF dataset subset
      field_map:
        context: persona        # Simple dot-path
        name: full_name
        bio: "{role}\n{interests}"  # Template combining fields
      params:                   # Per-class parameter overrides
        goal: "Have a productive discussion."
      shared_memories:          # Appended to defaults
        - "Class-specific memory."
```

### Data Sources

| Source | Required Keys | Description |
|--------|--------------|-------------|
| `hf_dataset` | `dataset`, `split` | HuggingFace Datasets (cached after first download) |
| `local_json` | `path` | JSON file (array of objects) |
| `inline` | `records` | Records defined directly in YAML |
| `config_path` | `path` | Dot-path reference into another config section |

### Social Network

```yaml
social_network:
  network_type: barabasi_albert   # barabasi_albert | random | lfr_benchmark | predefined
  barabasi_albert_m: 10           # Edges per new node (barabasi_albert only)
  base_followership_probability: 0.3
  fully_connected_targets:        # Roles that everyone follows
    - news_account
  activity_transition_rates:
    <role_name>:
      inactive_to_active: 0.3
      active_to_inactive: 0.3
```

### Probes

```yaml
probes:
  query_lib_module: null          # Optional custom probe type module

  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
    include_entities: []          # Empty = all agents
    exclude_entities: []

  queries:
    favorability:
      probe_name: favorability
      query_type: NumericRatingProbe   # Built-in or custom type
      query_data:
        name: Favorability
        question: "Return a single rating from {lo} to {hi}."
        context: "{agentname} rates favorability toward the current event."
        lo: 1
        hi: 10
```

### Other Scenario Fields

| Field | Description |
|-------|-------------|
| `shared_memories` | List of strings broadcast to all agents at init |
| `initial_observations` | List of templates (`{name}` replaced per-agent) |
| `jobname_format` | Hydra output directory name template |

### Processing Mode Notes

- `raw` and `formative` are the supported scenario-level modes.
- `llm_formative` is also accepted for backward compatibility.
- Custom mode names require runtime registration in
  `src/mastodon_sim/runtime/runner.py` (not YAML-only today).

When `sim.gm.components.resolve` is left at baseline defaults, `sim.action_mode`
still maps to the corresponding resolve component for backward compatibility.

---

## Output Configuration

Output paths are controlled by Hydra in the top-level `config.yaml`:

```yaml
hydra:
  job:
    name: ${scenario.jobname_format}_${now:%Y-%m-%d_%H-%M-%S}
  run:
    dir: scenarios/${scenario.scenario_name}/outputs/${scenario.jobname_format}
  output_subdir: configs/${scenario.jobname_format}
```

The simulation writes its artifacts into the directory resolved by `hydra.run.dir` +
`hydra.job.name`. See [Usage Overview — Output](usage.md#output) for the complete list
of output files.

---

## Related

- [Usage Overview](usage.md) — End-to-end workflow and output format
- [Building Agents](building_agents.md) — Persona pipeline details
- [Social Media Backends](backends.md) — Platform config and visualizers
- [Evaluation Probes](probes.md) — Probe type reference
