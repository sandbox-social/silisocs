# Preset Configurations

The simulator comes with two pre-configured presets: **default** and **oasis**. Both are designed for simple, single-GM scenarios with no multi-flow complexity.

---

## Default Preset

**When to use**: General-purpose simulations, quick prototyping.

```bash
python -m mastodon_sim.runtime.runner  # Uses default
```

or explicitly:

```bash
python -m mastodon_sim.runtime.runner sim=base social_media=mastodon
```

**Configuration snapshot** (`sim/base.yaml`):
- **LLM**: `qwen3.5-4b` (fast, default)
- **Agents**: 500
- **Steps**: 200
- **Backend**: Mastodon (single-server style)
- **Action mode**: `custom` (DOM parsing)
- **Memory**: `list` (fast hash-based)
- **Multi-flow**: **Disabled** (simple mode)
- **Multi-GM**: **Disabled** (single GM)

**Suitable for**:
- Standard social network experiments
- Testing new features
- Educational scenarios
- Quick prototypes

---

## OASIS Preset

**When to use**: OASIS-compatible information propagation studies, large-scale recommendation testing.

```bash
python -m mastodon_sim.runtime.runner sim=oasis social_media=reddit_like
```

**Configuration snapshot** (`sim/oasis_base.yaml`):
- **LLM**: `gpt-4o-mini` (OASIS standard)
- **Agents**: 100 (smaller for faster iteration)
- **Steps**: 50 (shorter runs)
- **Backend**: Reddit-like (supports embedded recommendations)
- **Action mode**: `tool_calling` (LLM native)
- **Memory**: `list` (fast)
- **Timeline**:  `hybrid_recsys_follower` (60% recs, 40% followers)
  - Blends recommendation algorithm output with follower posts
  - Enables studying information diffusion with algorithmic bias
- **Multi-flow**: **Disabled** (simple mode)
- **Multi-GM**: **Disabled** (single GM)

**Included components**:
- RecommendationComponent: Updates recommendations every episode
- NextActing: `activity_probability` (probabilistic scheduling)
- Timeline: Hybrid strategy mixing recommendations and followers
- Probe schedule: Runs every 5 steps for measurement

**Suitable for**:
- OASIS information propagation studies
- Recommendation algorithm evaluation
- Comparative studies (Reddit style platforms)
- Scenarios with real recommendation data

---

## Choosing Between Presets

| Feature | Default | OASIS |
|---------|---------|-------|
| **Primary use** | General testing | Info propagation studies |
| **Platform** | Mastodon-like | Reddit-like |
| **Agent count** | 500 | 100 |
| **Action mode** | Custom DOM parsing | LLM tool-calling |
| **Recommendations** | Optional | Built-in + hybrid feed |
| **Timeline style** | Follower-based | Mixed (recs + followers) |
| **Initial speed** | Slower LLM calls | Faster w/ gpt-4o-mini |
| **Best for** | Prototypes | OASIS reproducibility |

---

## Mixing Preset Variants

You can  mix components from both presets:

```bash
# Use OASIS agent count and LLM, but Mastodon backend
python -m mastodon_sim.runtime.runner \
  sim=oasis \
  social_media=mastodon \
  sim.num_agents=200

# Use default config but with tool-calling
python -m mastodon_sim.runtime.runner \
  sim=base \
  sim.action_mode=tool_calling
```

---

## Extending a Preset

Create a new simulation config that extends an existing preset:

```yaml
# conf/sim/my_variant.yaml
defaults:
  - base        # or: oasis
  - _self_

# Override only what you need
num_agents: 1000
num_steps: 500
timeline_strategy: pure_recsys  # Only recommendations, no followers
```

Then use it:

```bash
python -m mastodon_sim.runtime.runner sim=my_variant
```

---

## Key Differences from Multi-Flow/Multi-GM

Both presets are **single-GM, non-multi-flow** by default. This means:

- ✅ All agents use the same Observe component (same feed type)
- ✅ All agents use the same Resolve component (same action parsing)
- ✅ All agents share a single RecommendationComponent instance
- ✅ Zero routing overhead

If you need:
- Different observations for different agents → Use `enable_gm_multi_flow: true`
- Flow-aware engine scheduling → Use `enable_engine_multi_flow: true`
- Multiple GMs with agent class routing → Use [Advanced Multi-GM](docs/multi_gm_architecture.md)

See [Multi-Flow Guide](MULTI_FLOW_GUIDE.md) for advanced component routing.
