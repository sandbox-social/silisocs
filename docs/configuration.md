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
| `llm_name` | `gpt-4o-mini` | LLM model name (passed to Concordia model factory) |
| `llm_api_base` | `null` | Custom API base URL (for OpenAI-compatible endpoints) |
| `llm_api_key` | `null` | API key (or set via environment variable) |
| `disable_language_model` | `false` | Use a no-op model (for testing) |
| `num_agents` | `100` | Number of agents to create |
| `num_steps` | `50` | Simulation steps to run |
| `max_concurrent_actions` | `1000` | Max parallel LLM calls per step |
| `run_name` | `run1` | Run identifier (used in output path) |
| `seed` | `1` | Random seed |
| `sentence_encoder` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model for associative memory |
| `memory_backend` | `list` | Memory type: `list` (fast) or `associative` (embedding-based) |
| `action_mode` | `custom` | Prompt style: `custom` (scenario prompt) or `generic` (backend-generated action prompt) |
| `tool_calling.mode` | `single` | Tool dispatch mode: `none`, `single`, or `multi` (requires `gm.components.resolve.built_in=tool_calling` when not `none`) |
| `prompt_additions.add_action_count_guidance` | `false` | Add `[ActNum]` marker and action count guidance text to prompt |
| `prompt_additions.add_output_style` | `false` | Add `[OUTPUT STYLE]` section to prompt (stripped when tool-calling enabled) |
| `prompt_additions.include_backend_info` | `false` | Include backend/social-media app description in prompt |
| `enabled_actions` | `null` | Optional whitelist of **exact backend action function names**. **Example**: `["create_tweet", "like_tweet", "follow_user"]`. `null` means all actions enabled. |
| `enable_gm_multi_flow` | `false` | Enable multi-flow GM: routes different agent flows to different component instances (e.g., separate Observe components per flow) |
| `enable_engine_multi_flow` | `false` | Enable multi-flow engine: schedules agent flows in customizable phases (can be combined with `enable_gm_multi_flow`) |
| `checkpoint.every_n_steps` | `null` | Save checkpoints every N steps when set. |
| `checkpoint.explicit_steps` | `[]` | Additional explicit checkpoint steps. |
| `checkpoint.resume_file` | `null` | Optional path to checkpoint JSON used to resume a prior run. |
| `checkpoint.resume_step` | `null` | Optional step override when resuming (defaults to checkpoint step). |
| `gm.preset` | `base` | GM preset: `base` (default, single components) or `shared_flow` (multi-flow routing). Auto-selected by `enable_gm_multi_flow` if not set. |
| `gm.components.*` | *(slots)* | YAML-selectable GM components (`next_acting`, `observe`, `resolve`, `initializer`, `recommend`) |
| `engine.preset` | `base` | Engine preset: `base` (default, simple scheduling) or `flow` (flow-aware). Auto-selected by `enable_engine_multi_flow` if not set. |
| `timeline_posts` | `10` | Number of posts shown in agent timeline |
| `timeline_mode` | `hybrid_recsys_follower` | Canonical timeline selector: `follower_chronological`, `pure_recsys`, `hybrid_recsys_follower`, `curated_global` |
| `timeline_config` | `{recsys_ratio: 0.6, follower_ratio: 0.4}` | Strategy-specific config for hybrid/blended timelines |
| `observation_history` | `100` | Max observations kept in agent memory |
| `write_html_log` | `true` | Generate Concordia HTML logs |
| `roleplaying_instructions` | *(template)* | System prompt injected into every agent. Use `{name}` placeholder. |
| `seed_posts.type` | `llm` | Seed post provider: `llm` (LLM-generated), `csv` (CSV file), `json` (JSON file), `none` (disabled), `fallback` (CSV then LLM) |
| `seed_posts.params.file_path` | `null` | Path to CSV/JSON file for seed posts (used when type is `csv`, `json`, or `fallback`) |

### Seed Posts Configuration

**Purpose:** Initialize agent feeds with background posts before simulation starts.

**Methods:**

| Method | File Format | When to Use |
|--------|-------------|------------|
| `llm` (default) | N/A | Generate context-aware posts via LLM (slower, more realistic) |
| `csv` | `agent_name,post_text` | Pre-written posts, faster than LLM |
| `json` | `{"agent_name": "post_text"}` | Pre-written posts, easier to edit than CSV |
| `none` | N/A | Disable seed posts (organic growth only) |
| `fallback` | CSV first, LLM if missing | Hybrid: use CSV when available, generate for missing agents |

**Configuration via Command Line:**

```bash
# Generate via LLM (default)
python -m mastodon_sim.runtime.runner

# Use CSV file
python -m mastodon_sim.runtime.runner \
    seed_posts.type=csv \
    seed_posts.params.file_path=agents_posts.csv

# Use JSON file
python -m mastodon_sim.runtime.runner \
    seed_posts.type=json \
    seed_posts.params.file_path=agents_posts.json

# Disable seed posts
python -m mastodon_sim.runtime.runner seed_posts.type=none
```

**CSV Format:**

```csv
agent_name,post_text
Alice,"Great day for a walk!"
Bob,"Just finished a great book."
Charlie,"Planning to try something new."
```

**JSON Format:**

```json
{
  "Alice": "Great day for a walk!",
  "Bob": "Just finished a great book.",
  "Charlie": "Planning to try something new."
}
```

**Configuration via Dashboard:**

1. In the "Seed Posts Configuration" section, select the type from the dropdown
2. If using CSV/JSON/fallback, enter the file path
3.  Save or run the scenario



### Action Prompt Additions Configuration

**Purpose:** Control which optional components are added to agent action prompts.

All prompt additions default to `false` (disabled). Enable them via config to add guidance, formatting, or context to agent prompts.

**Available Flags:**

| Flag | Default | Effect |
|------|---------|--------|
| `sim.prompt_additions.add_action_count_guidance` | `false` | Add `[ActNum]` marker and action count guidance text. When `tool_calling.mode=single`, guidance says "Only take one action in this step". When `tool_calling.mode=multi`, says "You are allowed to output multiple tool calls (max 5)". |
| `sim.prompt_additions.add_output_style` | `false` | Add `[OUTPUT STYLE]` section describing expected response format. **Automatically stripped when tool-calling is enabled** (tool-calling uses JSON format instead). |
| `sim.prompt_additions.include_backend_info` | `false` | Include backend/social-media app description in prompt. For `generic` action mode, this is always included as part of the baseline prompt. |

**Configuration via YAML:**

```yaml
sim:
  action_mode: custom
  tool_calling:
    mode: single
  prompt_additions:
    add_action_count_guidance: true    # Add [ActNum] marker
    add_output_style: false              # Don't add output style
    include_backend_info: false          # Don't add app description
```

**Configuration via CLI:**

```bash
# Enable action count guidance
uv run mastodon_sim prompt_additions.add_action_count_guidance=true

# Enable both action count and output style
uv run mastodon_sim \
  prompt_additions.add_action_count_guidance=true \
  prompt_additions.add_output_style=true
```

**Behavior Notes:**

- **Tool-calling override**: When `tool_calling.mode` is not `none` and you enable `add_output_style`, the `[OUTPUT STYLE]` section is automatically **stripped** from the final prompt. Tool-calling uses JSON format, not text format.
- **Custom vs Generic modes**: In `action_mode=custom`, prompt additions are independent flags. In `action_mode=generic`, the baseline prompt always includes available actions and backend info; `include_backend_info` controls additional context.
- **FINISHED semantics**: When using `engine.action_loop.built_in=open_ended`, guidance about the "FINISHED" action is automatically included (not controlled by flags).

**Example Prompts:**

*Custom mode, no additions (default)*:
```
Take one action on this social media platform.
Available actions: create_tweet, like_tweet, follow_user
```

*Custom mode, with action count guidance*:
```
[ActNum] marker: 1

Take one action on this social media platform.
Only take one action in this step.

Available actions: create_tweet, like_tweet, follow_user
```

*Custom mode, with output style*:
```
Take one action on this social media platform.
Available actions: create_tweet, like_tweet, follow_user

[OUTPUT STYLE]
Format your response as JSON: {"action": "name", "parameters": {...}}
```

*Tool-calling mode (output style automatically stripped)*:
```
Take one action on this social media platform.
Available actions: create_tweet, like_tweet, follow_user

### TOOL_CALLING_MODE ###
[Tool schemas in JSON...]
```

### How Action Prompts Are Constructed

The action prompt pipeline is centralized and configuration-driven:

**Stage 1: Runner Startup (Before GM)**

When the simulation starts, the runner calls `build_complete_action_prompt_for_runner()` to compile the base prompt:

1. Determine mode: `custom` (scenario-provided) or `generic` (backend-generated)
2. For **custom mode**: use base prompt from scenario config
3. For **generic mode**: call `backend.generate_generic_action_prompt()` to list available actions
4. Inject optional additions based on config flags:
   - If `add_action_count_guidance=true`: inject `[ActNum]` marker + guidance text
   - If `add_output_style=true`: inject `[OUTPUT STYLE]` section
   - If `include_backend_info=true`: inject backend description (or always for generic mode)
5. If `tool_calling.mode=none`: keep output style as-is
6. If `tool_calling.mode=single|multi`: strip output style (will be replaced with JSON format at SMAct time)
7. Return final prompt string → stored in agent initialization

**Stage 2: Game Master (SMAct Pass-Through)**

During each action, SMAct simply wraps the prompt:

```python
action_spec = f"prompt: {runner_compiled_prompt} ;;type: free"
```

If `tool_calling.mode != none`, append tool schemas:
```
### TOOL_CALLING_MODE ###
{tool_schemas_json}
```

SMAct does NOT modify the base prompt itself—it's a dumb pass-through + optional tool marker wrapping.

**Stage 3: Entity Acting (LLM Call)**

The entity's act component parses the action spec and:

- If tool-calling markers detected: call LLM with tool-calling mode (returns structured tool call as JSON)
- Otherwise: call LLM in free-text mode (returns raw text)

### Why This Structure?

- **Single source of truth**: All prompt building at runner startup. No scattered prompt modifications.
- **Config-driven**: All additions controlled by explicit YAML flags (all default `false`).
- **Tool-calling architecture**: Base prompt built before app instance exists (can't generate schemas at runner time). Tool schemas added at SMAct time when app is available.
- **Transparency**: Each stage is simple and focused. No hidden prompt transformations.

### Testing and Validation

All prompt compilation scenarios are tested in:
- `tests/test_action_mode_architecture.py` — unit tests for compile_action_prompt()
- `tests/test_prompt_pipeline_integration.py` — end-to-end integration tests for all config states

To verify prompt output in different configurations:

```bash
# Test custom mode with all additions
uv run pytest tests/test_prompt_pipeline_integration.py::TestPromptCompilationMatrixIntegration -v

# Test tool-calling wrapping behavior
uv run pytest tests/test_prompt_pipeline_integration.py::TestSMActPassThroughBehavior -v

# Test end-to-end flow
uv run pytest tests/test_prompt_pipeline_integration.py::TestPromptPipelineEndToEnd -v
```



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

- `next_acting`: `activity_markov`, `activity_probability`, `all_entities`, `fixed_order`
- `observe`: `timeline_every_turn`, `episode_only`
- `resolve`: `parsed_action`, `generic_action`, `tool_calling`
- `initializer`: `backend_default`

For advanced use, provide `class_path` and optional `params` to load a custom
component implementation.

When using `sim.gm.preset: shared_flow`, the `observe` slot also supports a
flow routing map:

```yaml
sim:
  gm:
    components:
      observe:
        instances:
          timeline:
            built_in: timeline_every_turn
          episode:
            built_in: episode_only
        flow_map:
          active: observe__timeline_make_observation
          fixed_pre: observe__episode_observation
          default: observe__timeline_make_observation
```

`observe.flow_map` is for **component routing** (which observe instance a flow uses).
Per-flow field values for FlowComponents use `<role>.flows`.

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

### Advanced GM Orchestration (Optional)

**Most users should keep both disabled** (`enable_gm_multi_flow: false`, `enable_engine_multi_flow: false`) and use the simple workflow.

#### Understanding the Three Orthogonal Features

These can be enabled independently or together:

**1. Multi-Flow GM** (`enable_gm_multi_flow: true`)
- Creates **separate component instances** per agent flow
- Example: Agents in `timeline_flow` see `TimelineObservation`, agents in `episode_flow` see `EpisodeObservation`
- Each component receives a `flow → field value` mapping at initialization
- Use when different flows need fundamentally different observation/action/next-acting logic
- **Does NOT require flows to be scheduled differently** — all flows still run each episode

**2. Multi-Flow Engine** (`enable_engine_multi_flow: true`)
- **Schedules flows in phases** rather than all-at-once
- Example: Run `fixed_pre` flows, then `default` flows in sequence, with parallel execution within each phase
- Allows per-flow action loop policies (e.g., `fixed_pre` uses `fixed_count`, `default` uses `single_action`)
- Use when you need temporal separation of different agent groups per episode
- **Can be combined with Multi-Flow GM**: one provides routing, one provides scheduling

**3. Multi-GM Orchestration** (`gm_orchestration.gms`)
- Creates **multiple separate game masters** (different prefab modules or instances)
- Each GM manages its own component set
- GMs can be assigned to flows via `flow_to_gm` (one per flow) or `flow_to_gms` (chain per flow)
- Use when you need fundamentally different GM logic (e.g., one GM for social media, one for game state)
- **Requires Multi-Flow Engine enabled** if you want orchestrated flow scheduling
- **Different from Multi-Flow GM**: Multi-GM is about multiple independent GMs; Multi-Flow GM is about one GM routing components

#### Configuration Examples

**Simple (Default)**: Single GM, single component per role, all agents act each episode
```yaml
enable_gm_multi_flow: false
enable_engine_multi_flow: false
gm_orchestration:
  gms: []  # Single default GM
  flow_bindings: {}
```

**Multi-Flow GM Only**: One GM with separate components per flow, all act each episode
```yaml
enable_gm_multi_flow: true
enable_engine_multi_flow: false
gm:
  preset: shared_flow
gm_orchestration:
  gms: []  # Still single GM
  flow_bindings: {}
```

**Multi-Flow Engine Only**: All agents see same components, but scheduled in phases
```yaml
enable_gm_multi_flow: false
enable_engine_multi_flow: true
engine:
  preset: flow
  flow_routing:
    flow_order: [fixed_pre, default]
    entity_to_flow: {}  # Define which agents go to which flow
```

**Multi-Flow GM + Multi-Flow Engine**: Separate components per flow + phase scheduling
```yaml
enable_gm_multi_flow: true
enable_engine_multi_flow: true
gm:
  preset: shared_flow
engine:
  preset: flow
  flow_routing:
    flow_order: [fixed_pre, default]
```

**Multi-GM Orchestration**: Multiple GMs, each controls subset of flows
```yaml
enable_gm_multi_flow: false  # Or true if GMs also need internal routing
enable_engine_multi_flow: true  # Required for orchestration
gm_orchestration:
  gms:
    - gm_name: social_gm
      module_path: my_scenario.social_game_master
    - gm_name: game_gm
      module_path: my_scenario.game_game_master
  flow_bindings:
    flow_to_gms:
      social_flow: [social_gm]
      game_flow: [game_gm]
```

---

### Engine Action Loop Policies

Configure how many actions each agent takes per episode. Available in **all engine modes** (`base` or `flow`):

| Policy | Option | Behavior | Use Case |
|--------|--------|----------|----------|
| **Single Action** | `single_action` | Each agent acts **once per episode** | Default, turntaking |
| **Fixed Count** | `fixed_count` | Each agent gets **N action turns per episode** | Multiple rounds per agent |
| **Open-Ended** | `open_ended` | Agent acts until outputting a **done token** | Self-paced actions |

**Configuration:**

```yaml
engine:
  action_loop:
    built_in: fixed_count
    params:
      count: 3  # Each agent acts 3 times per episode (unused for other policies)
```

**Per-Flow Override** (with `enable_engine_multi_flow: true`):

```yaml
engine:
  flow_routing:
    flow_order: [fixed_pre, default]
  flow_policies:
    fixed_pre:
      built_in: fixed_count
      params:
        count: 2
    default:
      built_in: single_action
```

`flow_policies` is only honored by the flow engine (`sim.engine.preset: flow` or
`enable_engine_multi_flow: true`). Base engine always uses `engine.action_loop`.

---

Configure what posts agents see in their feed using timeline strategies:

```yaml
sim:
  timeline_posts: 10                          # Posts per timeline observation
  timeline_mode: follower_chronological       # Canonical selector
  timeline_config:
    recsys_ratio: 0.6                         # For hybrid mode: 60% recommendations
    follower_ratio: 0.4                       # For hybrid mode: 40% followed users
```

**Available Strategies:**

| Strategy | Available Platforms | Description |
|----------|-------------------|-------------|
| `follower_chronological` | Twitter, Reddit, Mastodon | Recent posts from followed users (no algorithmic feed) |
| `pure_recsys` | Twitter, Reddit | Algorithm-selected posts only (no human-followed content) |
| `hybrid_recsys_follower` | Twitter, Reddit | Blend of recommendations + followed posts (default for OASIS) |
| `curated_global` | Twitter only | Mix of trending posts + network recommendations |

**Strategy Details:**

- **follower_chronological**: Shows recent posts from users the agent follows, in reverse chronological order. No recommendation algorithm is used.

- **pure_recsys**: Uses only algorithmic recommendations (no followed users). Useful for studying pure recommendation effects.

- **hybrid_recsys_follower**: Combines recommendations and followed posts with configurable ratio (default 60% recsys, 40% followers). This blends algorithmic with social signals for realistic behavior.

- **curated_global**: Twitter-specific strategy that blends trending posts (global engagement) with personalized recommendations based on the agent's bio/interests. Creates a "curated" feed that mixes broad trends with personal relevance. **Not available on Reddit/Mastodon.**

### Recommendation System Configuration

Configure how recommendations are computed and cached:

```yaml
sim:
  timeline_mode: hybrid_recsys_follower
  timeline_config:
    recsys_ratio: 0.6
    follower_ratio: 0.4
  gm:
    components:
      recommend:
        built_in: recommendation_component
        params:
          update_every_n_steps: 1
          lazy: true
          max_posts: 10
```

The runtime contract is:

- `sim.timeline_mode` chooses how the timeline is assembled.
- `sim.gm.components.recommend` schedules recommendation recomputation.
- In multi-flow GM mode, per-flow algorithm choice is configured via `recommend.flows.<flow>.recsys_type`.

**Recommendation Algorithms:**

| Algorithm | Metric | Best For |
|-----------|--------|----------|
| `reddit` | Hot-score = sign(engagement) × log(\|engagement\|) + recency decay | General social media, OASIS default |
| `twitter` | TF-IDF: Bio-to-content cosine similarity | Personalized feeds, user-content matching |
| `twhin` | Deep embeddings (TWHIN-BERT) | Advanced semantic similarity |

**Algorithm Details:**

- **reddit**: Engagement-based ranking that balances post score (likes - dislikes) with age. Newer posts decay faster, older high-engagement posts remain visible. Formula: `score(post) = sign(engagement) × log(|engagement| + 1) + (age_penalty)`. Used for "hot" feeds.

- **twitter**: Content-based personalization matching user bio/interests to post content using TF-IDF vectors. Computes cosine similarity between user biography and each post's text. Favors posts matching stated user interests.

- **twhin**: Deep semantic embeddings using TWHIN-BERT pre-trained on Twitter data. Learns high-dimensional representations of users and content for advanced similarity calculations. Most computationally expensive.

**Per-Flow Configuration:**

Different flows can use different recommendation algorithms:

```yaml
sim:
  enable_gm_multi_flow: true
  gm:
    components:
      recommend:
        built_in: recommendation_component
        flows:
          default:
            recsys_type: reddit
          active:
            recsys_type: twitter
      observe:
        built_in: timeline_every_turn
        flows:
          default:
            recsys_type: reddit
          active:
            recsys_type: twitter
```

The observe component reads the active entity's flow and passes the selected `recsys_type`
to the backend when fetching `pure_recsys` or `hybrid_recsys_follower` timelines.

---

## Enabled Actions (Restricting Agent Capabilities)

By default, agents can use **all available backend actions**. To restrict agents to a specific subset, use `enabled_actions`:

```yaml
sim:
  enabled_actions: null  # null = all actions enabled (default)
  enabled_actions: ["create_tweet", "like_tweet", "follow_user"]  # Restrict to these exact functions
```

### Important: Exact Function Names Required

Action names must be the **exact decorated backend function names**, not generic types:

**❌ WRONG:**
```yaml
enabled_actions: ["POST", "LIKE", "REPOST"]  # Generic types - will not work!
```

**✅ CORRECT:**
```yaml
enabled_actions: ["create_tweet", "like_tweet", "repost_tweet"]  # Exact function names
```

### Backend Actions by Platform

**TwitterLike:**
- `create_tweet` - Post new tweet
- `reply_to_tweet` - Reply to existing tweet
- `like_tweet` - Like a tweet
- `repost_tweet` - Repost (retweet) a tweet
- `follow_user` - Follow a user
- `unfollow_user` - Unfollow a user

**RedditLike:**
- `create_reddit_post` - Create new post
- `create_comment` - Comment on post
- `upvote_post` - Upvote a post
- `downvote_post` - Downvote a post
- `subscribe` - Subscribe to subreddit
- `unsubscribe` - Unsubscribe from subreddit

**Mastodon:**
- `post_toot` - Post new toot
- `reply_to_toot` - Reply to toot
- `like_toot` - Like (favorite) a toot
- `boost_toot` - Boost (reblog) a toot
- `follow_user` - Follow a user
- `unfollow_user` - Unfollow a user

### How It Works

When `enabled_actions` is set, the engine:
1. Restricts LLM prompts to only show enabled actions
2. Restricts LLM tool choices (for `tool_calling` mode) to enabled tools
3. Enforces action validation at resolution time

---

## Timeline Configuration and Strategies

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

## Running and Creating Scenarios

Scenarios are stored in `scenarios/{scenario_name}/conf/` and allow customizing agent definitions, network topology, probes, and optionally overriding simulation/platform settings.

### Directory Structure

```
scenarios/
├── election/
│   └── conf/
│       ├── scenario/
│       │   └── election.yaml        # @package scenario (required)
│       ├── sim.yaml                 # (optional) Scenario-specific sim overrides
│       └── social_media.yaml        # (optional) Scenario-specific platform config
│
└── reddit_herding/
    └── conf/
        ├── scenario/
        │   └── reddit_herding.yaml
        ├── sim.yaml
        └── social_media.yaml
```

### Running a Scenario

**From the CLI:**

```bash
# Run with defaults from base.yaml + twitter_like.yaml
python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf

# Override specific sim parameters
python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf \
    sim.num_agents=500 sim.num_steps=100

# View merged config before running
python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf --cfg job
```

**From the Dashboard:**

1. Load scenarios by choosing a scenario from the "Scenario" dropdown
2. Modify any settings
3. Click "Save Scenario" to save changes to `scenarios/{name}/`
4. Click "Run Simulation"

### Creating a New Scenario

**Option 1: Via Dashboard**

1. Start the dashboard: `streamlit run src/mastodon_sim/dashboard/launch_app.py`
2. Modify all settings (agents, network, probes, etc.)
3. Enter a new scenario name in the "Scenario Name" field
4. Click "Save Scenario"
5. This creates: `scenarios/{name}/conf/scenario/{name}.yaml`

**Option 2: Manual Directory**

```bash
mkdir -p scenarios/my_scenario/conf/scenario
cat > scenarios/my_scenario/conf/scenario/my_scenario.yaml << 'EOF'
# @package scenario

scenario_name: my_scenario
jobname_format: "N${sim.num_agents}_T${sim.num_steps}_run1"

setting:
  name: My Setting
  background:
    - Background detail 1
    - Background detail 2

event:
  name: My Event
  context: Event description

persona_pipeline:
  processing_mode: raw
  defaults:
    params: {}
  classes:
    user:
      count: ${sim.num_agents}
      prefab_module: mastodon_sim.agents.entity
      data:
        source: hf_dataset
        dataset: nvidia/Nemotron-Personas-USA

social_network:
  base_followership_probability: 0.3
  network_type: barabasi_albert
  barabasi_albert_m: 10

shared_memories: []
probes: {}
EOF
```

### Overriding Sim and Platform Settings per Scenario

**Default behavior:** Uses `sim/base.yaml` and `social_media/twitter_like.yaml` from package defaults.

**To override for a specific scenario**, create optional files:

**scenarios/election/conf/sim.yaml:**
```yaml
# @package sim
# Only list fields that differ from base.yaml; unspecified fields use defaults

num_agents: 500
num_steps: 200
action_mode: custom
tool_calling:
  mode: single
```

**scenarios/election/conf/social_media.yaml:**
```yaml
# @package social_media
# Override the platform for this scenario

platform_type: mastodon  # Use Mastodon instead of default twitter_like
```

**How Merging Works:**

Hydra composes configs in order, with later files overriding earlier ones:

1. **Package defaults** (lowest priority)
   - `src/mastodon_sim/conf/sim/base.yaml`
   - `src/mastodon_sim/conf/social_media/twitter_like.yaml`
   - `src/mastodon_sim/conf/scenario/default.yaml`

2. **External scenario overrides** (higher priority)
   - `scenarios/election/conf/sim.yaml` (if present)
   - `scenarios/election/conf/social_media.yaml` (if present)
   - `scenarios/election/conf/scenario/election.yaml`

3. **CLI overrides** (highest priority)
   - `sim.num_agents=1000`
   - `sim.llm_name=gpt-4o`
   - etc.

**Missing fields fallback to defaults:** If `scenarios/election/conf/sim.yaml` omits `llm_name`, the value from `base.yaml` is used.

### Output Structure

Simulation outputs go to: `scenarios/{scenario_name}/outputs/{job_name}/`

```
scenarios/election/outputs/
├── N500_T200_independent_run1/
│   ├── configs/
│   │   ├── config.yaml            # Full merged config
│   │   ├── overrides.yaml         # CLI overrides used
│   │   └── hydra.yaml
│   ├── logs/
│   │   └── *.html                 # Concordia agent logs
│   ├── trace_*.json               # Execution trace
│   └── agents_*.json              # Agent data
```

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
  processing_mode: raw          # raw | formative

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
      flow_tag: default         # Optional class-level flow tag (advanced)
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
      fixed_action:             # Optional helper for fixed_entity classes
        enabled: false
        action_set_ref: null
        selection_policy: round_robin
        on_exhaustion: loop

fixed_action_sets:
  file: null                    # Optional external YAML/JSON file with action sets
  inline:
    set_id:
      actions:
        - action: create_tweet
          args:
            status: "Breaking update from {name}"
          weight: 1.0
```

### Data Sources

| Source | Required Keys | Description |
|--------|--------------|-------------|
| `hf_dataset` | `dataset`, `split` | HuggingFace Datasets (cached after first download) |
| `local_json` | `path` | JSON file (array of objects) |
| `inline` | `records` | Records defined directly in YAML |
| `config_path` | `path` | Dot-path reference into another config section |

### Fixed-Entity Class Notes

- Use `prefab_module: mastodon_sim.agents.fixed_entity` for deterministic fixed-action agents.
- `params.fixed_action_plan` must be an episode-keyed dict (strict).
- You can initialize plans from an external file with `params.fixed_action_plan_file` (`.json` / `.yaml` / `.yml`).
- Set only one of `params.fixed_action_plan` or `params.fixed_action_plan_file`.
- Set `flow_tag` to control which engine flow bucket executes the entity.
- To provide episode-based observations, set:

Example fixed-entity params:

```yaml
params:
  fixed_action_plan:
    0:
      - action_type: create_tweet
        target_id: ""
        content: "Scheduled update"
        reasoning: "Deterministic episode 0 post."
  emit_finished_on_episode_end: true
```

Or file-based:

```yaml
params:
  fixed_action_plan_file: scenarios/my_scenario/input/fixed_action_plan.yaml
```

The file contents use the same episode-keyed dict shape as `fixed_action_plan`.

```yaml
sim:
  gm:
    components:
      observe:
        params:
          episode_observation_flows: [fixed_pre]

  engine:
    flow_routing:
      flow_order: [fixed_pre, default]
      entity_to_flow: {}
```

### Defining New Behavior Flows (Easy Recipe)

Use this whenever you add a new behavior-oriented agent class (fixed agents are one example).

1. Assign each class to a flow via `flow_tag`.
2. Define execution order in `sim.engine.flow_routing.flow_order`.
3. Optionally route specific entities by name with `sim.engine.flow_routing.entity_to_flow`.
4. If a flow needs a special observation format, list it in `sim.gm.components.observe.params.episode_observation_flows`.

Example:

```yaml
persona_pipeline:
  classes:
    broadcasters:
      prefab_module: mastodon_sim.agents.fixed_entity
      params:
        flow_tag: fixed_pre
    users:
      prefab_module: mastodon_sim.agents.entity
      params:
        flow_tag: default

sim:
  engine:
    flow_routing:
      flow_order: [fixed_pre, default, moderation_post]
      entity_to_flow:
        "moderator_1": moderation_post
  gm:
    components:
      observe:
        params:
          episode_observation_flows: [fixed_pre, moderation_post]
```

This keeps the system extensible without changing resolve logic or adding new manager layers.

### Checkpoint Resume / Replay

- Checkpoints are written to `.../outputs/.../checkpoints/step_<N>_checkpoint.json`.
- Checkpoint saving is disabled by default unless `checkpoint.every_n_steps` or `checkpoint.explicit_steps` is configured.
- Resume by setting `sim.checkpoint.resume_file` to a checkpoint JSON path.
- By default, replay resumes from the `step` value saved in the checkpoint.
- Set `sim.checkpoint.resume_step` to force a different starting step.

CLI example:

```sh
uv run mastodon-sim \
  --config-path scenarios/my_scenario/conf \
  sim.num_steps=200 \
  sim.checkpoint.every_n_steps=10 \
  sim.checkpoint.resume_file=scenarios/my_scenario/outputs/run1/.../checkpoints/step_100_checkpoint.json
```

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
- Custom mode names require runtime registration in
  `src/mastodon_sim/runtime/runner.py` (not YAML-only today).

When `sim.gm.components.resolve` is left at baseline defaults, `sim.action_mode`
maps to the corresponding resolve component only for `custom`/`generic`.
Tool-calling is controlled explicitly by `sim.tool_calling.mode`.

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
