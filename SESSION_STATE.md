# Session State

## Current Focus
Phase 1 complete: config structure refactor on branch `scenario_automation`.
Phase 2 pending: Claude Code skill for custom scenario generation.

## Config Architecture (as of f29cc59)

### Config groups (in `src/mastodon_sim/conf/`)
- `llm/base.yaml`, `llm/gpt4o.yaml` — LLM connection params
- `simulator/base.yaml` — engine, tool_calling, memory_backend, checkpoint, roleplaying
- `agent_situation/base.yaml` — setting, event, data, persona_pipeline, shared_memories, initial_observations
- `env/twitter_like.yaml` — platform config, gm, social_network, candidates, news_account, partisan_types
- `evals/base.yaml` — write_html_log, probes
- `experiment.yaml` — top-level; includes root-level run params + defaults list

### Root-level keys in `experiment.yaml` (no group prefix)
- `num_agents`, `num_steps`, `seed`, `run_name`, `scenario_name`, `agent_situation_name`, `jobname_format`, `output_rootname`, `experiment_name`

### Per-scenario files
- `scenarios/*/conf/run.yaml` — flat override merged to config root (replaces `sim.yaml`)
- `scenarios/*/conf/env.yaml` — env overrides (includes candidates/news_account/partisan_types for election scenarios)
- `scenarios/*/conf/agent_situation/{default,thin}.yaml` — setting, event, data + persona pipeline
- `scenarios/*/conf/simulator.yaml` — scenario-specific simulator overrides (ai_conference, misinformation, election_recsys_engagement)
- `scenarios/election_recsys_engagement/conf/llm.yaml` — local model config

### Deleted
- `src/mastodon_sim/conf/sim/` — entire directory removed
- `scenarios/*/conf/sim.yaml` — all deleted (replaced by `run.yaml`)

## Key Namespace Mapping (for reference)
- `cfg.sim.scenario_name` → `cfg.scenario_name`
- `cfg.sim.num_steps` → `cfg.num_steps`
- `cfg.sim.seed` → `cfg.seed`
- `cfg.sim.num_agents` → `cfg.num_agents`
- `cfg.sim.output_rootname` → `cfg.output_rootname`
- `cfg.sim.agent_situation_name` → `cfg.agent_situation_name`
- `cfg.sim.setting` / `cfg.sim.event` / `cfg.sim.data` → `cfg.agent_situation.setting/event/data`
- `${sim.event.context}` interpolations → `${agent_situation.event.context}`
- `cfg.sim.engine` → `cfg.simulator.engine`
- `cfg.sim.action_mode` → `cfg.simulator.action_mode`
- `cfg.sim.timeline_mode` → `cfg.env.timeline_mode`
- `cfg.sim.candidates/news_account/partisan_types` → `cfg.env.candidates/news_account/partisan_types`

## _merge_external_group_overrides behavior
1. Reads `run.yaml` → merges flat to config root
2. Reads `env.yaml`, `evals.yaml`, `llm.yaml`, `simulator.yaml` → merges to named groups
3. Reads `agent_situation/{agent_situation_name}.yaml` → merges to `agent_situation` group
4. Re-applies Hydra CLI task overrides so they take precedence over scenario files

## _inject_external_config_path behavior
- Reads `run.yaml` (falls back to `sim.yaml` for compat) to inject root-level `scenario_name=` and `jobname_format=` CLI overrides for Hydra path interpolation

## Verified
- `uv run python -m silisocs.runtime.runner --config-path scenarios/misinformation/conf num_steps=1 llm.disabled=true` → `status=success episodes=1`

## Commits
- `7b0b387` — Phase 1: split sim into llm/simulator/agent_situation groups
- `f29cc59` — Phase 2a: dissolve sim group entirely

## Next Steps
- [ ] Phase 2: Design and implement Claude Code skill for custom scenario generation
  - Interactive (ask-then-generate) vs template-based — still open
  - Should produce: `run.yaml`, `env.yaml`, `evals.yaml`, `agent_situation/default.yaml`

## Open Questions
- Should the scenario generation skill be interactive or template-based?
