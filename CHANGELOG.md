# Changelog

## Unreleased

### Breaking changes

1. **Role-count builder mode removed**
   - `BaseAgentBuilder` now requires `scenario.persona_pipeline.classes`.
   - Legacy role-count construction (`build_role_agents` fallback) has been removed.
   - **Why this can break:** scenarios relying on `roles` without `persona_pipeline.classes` will now fail fast during agent build.

2. **Custom scenario-local HF cache path removed**
   - `hf_dataset` loading no longer reads/writes `scenarios/<name>/input/personas/.hf_cache/*`.
   - Loading now goes directly through `datasets.load_dataset(...)`.
   - **Why this can break:** workflows that depended on checked-in/local `.hf_cache` JSON files or cache fallback when `datasets` was unavailable will no longer work.

3. **External root-file override merge removed**
   - Automatic merge of external root `sim.yaml` and `social_media.yaml` into composed config has been removed.
   - `--config-path` now relies on Hydra searchpath layering only.
   - **Why this can break:** commands expecting root-level external `sim.yaml`/`social_media.yaml` injection semantics must move those overrides into Hydra group files under the config tree.

### Added

1. **Overlay config layering**
   - New CLI flag: `--overlay-config-path <dir>` (repeatable).
   - Precedence: overlays > `--config-path` > package defaults.
   - This keeps scenario-by-path execution while allowing additive override packs from other config directories.

2. **Runtime refactor modules**
   - Added `mastodon_sim.runtime.projection` for normalized runtime mode validation.
   - Added `mastodon_sim.runtime.agent_building` for scenario builder resolution/build.
   - Added `mastodon_sim.runtime.factories` for GM filename/module defaults and engine factory.

3. **Action prompt pipeline centralization**
   - New module: `mastodon_sim.runtime.action_prompts` provides all prompt compilation logic.
   - **Breaking change in behavior**: `SMAct._next_entity_action_spec()` is now a simple pass-through (returns pre-compiled prompt).
   - **Key change**: Prompt compilation happens at runner startup via `build_complete_action_prompt_for_runner()`, not at GM/SMAct time.
   - Prompt additions (action count guidance, output style) are now config-driven via `sim.prompt_additions.*` flags (all default to `False`).
   - Tool-calling markers and schemas are added at SMAct runtime (when `enable_tool_calling=True` in SMAct init).
   - **Old behavior removed**: `_extract_actnum_guidance()` and `_inject_actnum_before_output_style()` helper functions removed from SMAct.
   - **Why this is cleaner**: All prompt logic now in one place; configuration is explicit; no downstream complexity.
