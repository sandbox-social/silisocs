# Architecture Fixes Summary

This document tracks fixes made to align the codebase with the intended multi-flow architecture.

## Completed Fixes

### Fix #1: RecommendationComponent Integration ✅

**Status:** COMPLETE

**Changes:**
- Made `RecommendationComponent` inherit from `FlowComponent` to support flow-level field mapping
- Removed single `recsys_type` parameter; component now extracts unique types from flow:field mapping
- Added `_extract_unique_recsys_types()` method to initialize all configured recommendation algorithms
- Integrated into `BaseSocialMediaGameMaster`:
  - Added import in factory
  - Build from config: `recommend_slot = dict(gm_components_cfg.get("recommend", {}))`
  - Initialize multi-fields: `initialize_component_multi_fields(recommend_component, recommend_slot)`
  - Added to context components dict with key `"recommendation"`

**How It Works:**
```yaml
gm:
  components:
    recommend:
      built_in: recommendation_component
      params:
        update_every_n_steps: 1
        lazy: true
        max_posts: 10
      entities:
        active:
          recsys_type: "twitter"
        lurker:
          recsys_type: "reddit"
```

The component:
1. Receives flow:field mapping at initialization
2. Extracts unique recsys types: `{"twitter", "reddit"}`
3. Calls `backend.init_recsys()` for each on first step
4. Calls `backend.update_recommendations()` every step (backend handles all variants)
5. Agents in "active" flow use twitter recommendations
6. Agents in "lurker" flow use reddit recommendations

**Files Modified:**
- `src/mastodon_sim/environments/gm/components/recommend.py`
- `src/mastodon_sim/environments/gm/base_game_master.py`

---

### Fix #2: Seed Posts - CSV/JSON Support + Disable Option ✅

**Status:** COMPLETE

**Changes:**
- Enhanced `CSVSeedPostProvider` to auto-detect file type (.csv or .json) and parse accordingly
- Added JSON parser (`_load_json()`) that expects format: `{agent_name: post_text}`
- Added CSV parser (`_load_csv()`) with existing logic, now as internal method
- Created `DisabledSeedPostProvider` for organic growth (returns empty posts for all agents)
- Updated `_build_seed_post_provider()` factory to support new types:
  - `type: "none"` → `DisabledSeedPostProvider`
  - `type: "csv"` → `CSVSeedPostProvider` with `file_path` param
  - `type: "json"` → `CSVSeedPostProvider` with `file_path` param
  - `type: "fallback"` → `FallbackSeedPostProvider` with `file_path` param
  - `type: "llm"` → `LLMSeedPostProvider` (default)

**Configuration Examples:**

```yaml
# LLM-generated (default)
seed_posts:
  type: "llm"
  params:
    max_workers: 64

# JSON file
seed_posts:
  type: "json"
  params:
    file_path: "/path/to/agents.json"

# CSV file
seed_posts:
  type: "csv"
  params:
    file_path: "/path/to/agents.csv"

# Fallback (CSV first, LLM for gaps)
seed_posts:
  type: "fallback"
  params:
    file_path: "/path/to/agents.csv"
    llm_fallback: true

# Disabled (organic growth)
seed_posts:
  type: "none"
```

**JSON Format:**
```json
{
  "agent_alice": "This is a sample tweet from alice",
  "agent_bob": "Bob's initial tweet here",
  ...
}
```

**Files Modified:**
- `src/mastodon_sim/environments/gm/components/seed_post_provider.py`
- `src/mastodon_sim/environments/gm/base_game_master.py`

---

### Fix #3: Preset Naming - "simple" → "base" ✅

**Status:** COMPLETE

**Changes:**
- Renamed `gm.preset: simple` → `gm.preset: base` for consistency with engine preset naming
- Updated all default values from "simple" to "base" in fallback cases
- Updated comments and help text in configuration files and UI

**Pattern:**
- Engine presets: `base` | `flow`
- GM presets: `base` | `shared_flow`

**Files Modified:**
- `src/mastodon_sim/conf/sim/base.yaml`
- `src/mastodon_sim/runtime/runner.py` (2 functions)
- `src/mastodon_sim/dashboard/launch_app.py` (3 locations)

---

## Outstanding Issues

### Issue #1: TimelineObservation Component Routing (Not Fixed)

**Problem:**
`TimelineMakeObservation.pre_act()` contains an if-statement (lines 71-85):
```python
flow_type = self._entity_action_flows.get(active_entity_name, "default")
if flow_type in self._episode_observation_flows:
    # Return episode number instead of timeline
    result = f"EPISODE: {current_episode}"
    ...
```

**Why It's Wrong:**
- Mixes two concerns: timeline and episode observation in one component
- Should be handled by separate component instances with proper routing
- Example: EpisodeObservation exists but component selection logic isn't implemented

**Correct Architecture:**
1. `TimelineMakeObservation` - ONLY returns timeline (no if-statement)
2. `EpisodeObservation` - ONLY returns episode number (no if-statement)
3. GM instantiates BOTH components
4. Higher-level routing selects which component based on agent's flow

**Why Not Fixed:**
Requires architectural support for multiple observe components and routing logic, which would affect:
- Factory component selection mechanism
- GM component instantiation logic
- Potentially engine-level routing

This is a larger refactor that should be done separately.

**Workaround:**
Current setup still works because:
- `TimelineMakeObservation` handles "fixed_pre" agents via if-statement
- `EpisodeObservation` is available for standalone use
- No functional breakage, just not architecturally clean

---

## Multi-Flow Architecture Clarity

### Two Levels of Multi-Flow Configuration

#### Level 1: Component Routing (Separate Instances)
Create multiple component instances, route agents to appropriate one:
```yaml
# Example: Different observation strategies per flow
observe:
  # Route fixed_pre agents to episode observations
  # Route active agents to timeline observations
```

Status: Not yet implemented for observe component.

#### Level 2: Multi-Field Configuration (Within Component)
Single component handles different agents with different field values:
```yaml
recommend:
  params:
    update_every_n_steps: 1
  entities:
    active:
      recsys_type: "twitter"  # Different field value per flow
    lurker:
      recsys_type: "reddit"   # Different field value per flow
```

Status: ✅ IMPLEMENTED via `FlowComponent` and `initialize_component_multi_fields()`

---

## Testing Recommendations

### Test RecommendationComponent
- [ ] Verify component initializes with multiple recsys types
- [ ] Check that `backend.init_recsys()` is called for each unique type
- [ ] Verify recommendations are updated every episode
- [ ] Test with agents having different `recsys_type` field values

### Test Seed Posts
- [ ] Test JSON loading with sample JSON file
- [ ] Test CSV loading with sample CSV file
- [ ] Test fallback mode with partial CSV + LLM
- [ ] Test "none" type returns empty posts
- [ ] Verify backwards compatibility with existing CSV configs

### Test Preset Renaming
- [ ] Run with `gm.preset: base`
- [ ] Verify "simple" preset still works (if intended for backwards compat)
- [ ] Check dashboard UI shows "base" as default option

---

## Next Steps

1. **Implement Component Routing for Observe** (Priority: Medium)
   - Create factory support for multiple observe components
   - Add GM-level routing logic
   - Refactor TimelineObservation to be pure timeline
   - Ensure EpisodeObservation works standalone

2. **Add Multi-Field Support to Other Components** (Priority: Low)
   - Consider adding resolve component multi-fields for per-agent action parsing
   - Document multi-field capability for future component developers

3. **Update Documentation** (Priority: High)
   - Document recommendation component configuration in docs/
   - Document seed posts configuration options
   - Add examples for multi-flow scenarios

---

## Files Summary

### Modified
- `src/mastodon_sim/environments/gm/components/recommend.py` - Refactored as FlowComponent
- `src/mastodon_sim/environments/gm/base_game_master.py` - Integrated recommendation and updated imports
- `src/mastodon_sim/environments/gm/components/seed_post_provider.py` - Added JSON support and disable option
- `src/mastodon_sim/conf/sim/base.yaml` - Updated preset naming
- `src/mastodon_sim/runtime/runner.py` - Updated default preset values
- `src/mastodon_sim/dashboard/launch_app.py` - Updated UI defaults

### Not Modified
- `src/mastodon_sim/environments/gm/components/observe.py` - Flagged for future refactor
- `src/mastodon_sim/environments/gm/components/factory.py` - Works as-is with current design
