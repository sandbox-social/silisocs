# Configuration Reference (Generated)

Default values for every key in the packaged base config groups, generated
from the YAML files under `src/silisocs/conf/`. Do not edit by hand:
regenerate with `python -m silisocs.runtime.configuration.config_reference`;
`tests/test_config_reference.py` fails when this file drifts from the
packaged defaults.

For what the knobs mean, see [configuration.md](configuration.md). Scenario
files override these defaults via Hydra composition (see `AGENTS.md`).

## world (`world/default.yaml`)

| Key | Default | Type |
|-----|---------|------|
| `scenario_name` | `"default"` | str |
| `jobname_format` | `"N${num_agents}_T${num_steps}_${experiment_name}_${run_name}"` | str |
| `num_agents` | `10` | int |
| `num_steps` | `5` | int |
| `run_name` | `"run1"` | str |
| `seed` | `1` | int |
| `output_rootname` | `""` | str |
| `setting.name` | `"Generic Community"` | str |
| `setting.background` | `["A general-purpose social media community where people discuss everyday topi...` | list |
| `event.name` | `"Daily discussion"` | str |
| `event.context` | `"Users are participating in everyday social media discussions. They share opi...` | str |
| `data` | `{}` | dict |

## agents (`agents/default.yaml`)

| Key | Default | Type |
|-----|---------|------|
| `agents.builder.class_path` | `null` | null |
| `agents.builder.params` | `{}` | dict |
| `agents.persona_pipeline.defaults.params.seed_post` | `""` | str |
| `agents.persona_pipeline.defaults.params.bio` | `""` | str |
| `agents.persona_pipeline.defaults.params.style` | `""` | str |
| `agents.persona_pipeline.defaults.params.goal` | `null` | null |
| `agents.persona_pipeline.defaults.params.world_context` | `"${event.context}"` | str |
| `agents.persona_pipeline.defaults.shared_memories` | `["They are an active user on a social media platform.", "${event.context}"]` | list |
| `agents.persona_pipeline.classes.user.count` | `"${num_agents}"` | str |
| `agents.persona_pipeline.classes.user.class_path` | `"silisocs.agents.native.NativeAgent"` | str |
| `agents.persona_pipeline.classes.user.sim_role_name` | `"user"` | str |
| `agents.persona_pipeline.classes.user.data.source` | `"inline"` | str |
| `agents.persona_pipeline.classes.user.data.records` | `[{"name": "Alex", "persona": "Alex is a local organizer who posts about publi...` | list |
| `agents.persona_pipeline.classes.user.field_map.name` | `"name"` | str |
| `agents.persona_pipeline.classes.user.field_map.context` | `"persona"` | str |
| `agents.persona_pipeline.classes.fixed_seed.count` | `0` | int |
| `agents.persona_pipeline.classes.fixed_seed.class_path` | `"silisocs.agents.fixed.FixedAgent"` | str |
| `agents.persona_pipeline.classes.fixed_seed.sim_role_name` | `"fixed_seed"` | str |
| `agents.persona_pipeline.classes.fixed_seed.flow_tag` | `null` | null |
| `agents.persona_pipeline.classes.fixed_seed.params` | `{}` | dict |
| `agents.persona_pipeline.classes.llm_user.count` | `0` | int |
| `agents.persona_pipeline.classes.llm_user.class_path` | `"silisocs.agents.native.NativeAgent"` | str |
| `agents.persona_pipeline.classes.llm_user.sim_role_name` | `"llm_user"` | str |
| `agents.persona_pipeline.classes.llm_user.flow_tag` | `null` | null |
| `agents.persona_pipeline.classes.llm_user.data.source` | `"inline"` | str |
| `agents.persona_pipeline.classes.llm_user.data.records` | `[]` | list |
| `agents.persona_pipeline.classes.llm_user.field_map.name` | `"name"` | str |
| `agents.persona_pipeline.classes.llm_user.field_map.context` | `"persona"` | str |
| `agents.shared_memories` | `["They are an active user on a social media platform.", "${event.context}"]` | list |
| `agents.initial_observations` | `["{name} is at home checking their social media feed.", "{name} decides to br...` | list |

## sim (`sim/base.yaml`)

| Key | Default | Type |
|-----|---------|------|
| `sim.llm.provider` | `"openai"` | str |
| `sim.llm.name` | `"gpt-4o-mini"` | str |
| `sim.llm.temperature` | `0.5` | float |
| `sim.llm.api_base` | `null` | null |
| `sim.llm.api_key` | `null` | null |
| `sim.llm.disabled` | `false` | bool |
| `sim.llm.extra_kwargs` | `{}` | dict |
| `sim.max_concurrent_actions` | `1000` | int |
| `sim.action_mode` | `"custom"` | str |
| `sim.telemetry.record_active_agent_names` | `false` | bool |
| `sim.tool_calling.mode` | `"single"` | str |
| `sim.prompt_additions.action_count_guidance` | `true` | bool |
| `sim.memory.built_in` | `"window"` | str |
| `sim.memory.class_path` | `null` | null |
| `sim.memory.params` | `{}` | dict |
| `sim.initialization.agents.built_in` | `"default"` | str |
| `sim.initialization.agents.class_path` | `null` | null |
| `sim.initialization.agents.params` | `{}` | dict |
| `sim.initialization.game_masters.built_in` | `"default"` | str |
| `sim.initialization.game_masters.class_path` | `null` | null |
| `sim.initialization.game_masters.params` | `{}` | dict |
| `sim.initialization.simulation.built_in` | `"none"` | str |
| `sim.initialization.simulation.class_path` | `null` | null |
| `sim.initialization.simulation.params` | `{}` | dict |
| `sim.checkpoint.every_n_steps` | `null` | null |
| `sim.checkpoint.explicit_steps` | `[]` | list |
| `sim.checkpoint.source_run` | `null` | null |
| `sim.checkpoint.auto_resume` | `true` | bool |
| `sim.checkpoint.save.built_in` | `"monolithic_json"` | str |
| `sim.checkpoint.save.class_path` | `null` | null |
| `sim.checkpoint.save.params` | `{}` | dict |
| `sim.checkpoint.restore.built_in` | `"social_action_event_replay"` | str |
| `sim.checkpoint.restore.class_path` | `null` | null |
| `sim.checkpoint.restore.params` | `{}` | dict |
| `sim.engine.class_path` | `null` | null |
| `sim.engine.params` | `{}` | dict |
| `sim.engine.executor` | `"threads"` | str |
| `sim.engine.loop.built_in` | `"fixed_steps"` | str |
| `sim.engine.loop.class_path` | `null` | null |
| `sim.engine.loop.params` | `{}` | dict |
| `sim.engine.step.built_in` | `"base"` | str |
| `sim.engine.step.class_path` | `null` | null |
| `sim.engine.step.params.flow_order` | `["fixed_pre", "default"]` | list |
| `sim.engine.step.params.agent_to_flow` | `{}` | dict |
| `sim.engine.step.params.gm_turn_policies` | `{}` | dict |
| `sim.engine.step.params.gm_concurrency_caps` | `{}` | dict |
| `sim.engine.turn_policy.built_in` | `"single_action"` | str |
| `sim.engine.turn_policy.class_path` | `null` | null |
| `sim.engine.turn_policy.params` | `{}` | dict |
| `sim.engine.control.built_in` | `"none"` | str |
| `sim.engine.control.start_paused` | `false` | bool |
| `sim.engine.control.control_file` | `null` | null |
| `sim.engine.control.poll_interval` | `0.3` | float |
| `sim.engine.participation.built_in` | `"all"` | str |
| `sim.engine.participation.class_path` | `null` | null |
| `sim.engine.participation.params` | `{}` | dict |
| `sim.roleplaying_instructions` | `"<general_instructions> You are simulating {name}, a character in a social sc...` | str |

## env (twitter_like) (`env/twitter_like.yaml`)

| Key | Default | Type |
|-----|---------|------|
| `env.gm.backend.type` | `"twitter_like"` | str |
| `env.gm.backend.class_path` | `null` | null |
| `env.gm.backend.params.perform_operations` | `false` | bool |
| `env.gm.backend.params.app_description` | `"Twitter-like social backend. Agents can create posts, reply, like, repost, f...` | str |
| `env.gm.backend.enabled_actions` | `null` | null |
| `env.gm.backend.excluded_actions` | `null` | null |
| `env.gm.components.initialize.built_in` | `"social_media"` | str |
| `env.gm.components.initialize.class_path` | `null` | null |
| `env.gm.components.initialize.params.graph.fully_connected_targets` | `[]` | list |
| `env.gm.components.initialize.params.graph.base_followership_probability` | `0.3` | float |
| `env.gm.components.initialize.params.graph.network_type` | `"barabasi_albert"` | str |
| `env.gm.components.initialize.params.graph.barabasi_albert_m` | `10` | int |
| `env.gm.components.next_acting.built_in` | `"all_agents"` | str |
| `env.gm.components.next_acting.class_path` | `null` | null |
| `env.gm.components.next_acting.params` | `{}` | dict |
| `env.gm.components.update.built_in` | `"social_recommendation"` | str |
| `env.gm.components.update.class_path` | `null` | null |
| `env.gm.components.update.params.default_recsys_type` | `null` | null |
| `env.gm.components.update.params.update_every_n_steps` | `1` | int |
| `env.gm.components.update.params.lazy` | `true` | bool |
| `env.gm.components.update.params.max_posts` | `10` | int |
| `env.gm.components.update.params.user_context_recent_posts` | `10` | int |
| `env.gm.components.update.params.include_like_trace` | `true` | bool |
| `env.gm.components.update.params.like_trace_window` | `10` | int |
| `env.gm.components.update.params.like_trace_weight` | `0.5` | float |
| `env.gm.components.update.params.include_like_trace_in_context` | `false` | bool |
| `env.gm.components.observe.built_in` | `"timeline_every_turn"` | str |
| `env.gm.components.observe.class_path` | `null` | null |
| `env.gm.components.observe.params.recsys_type` | `null` | null |
| `env.gm.components.observe.params.timeline_mode` | `"hybrid_recsys_follower"` | str |
| `env.gm.components.observe.params.timeline_posts` | `10` | int |
| `env.gm.components.observe.params.timeline_config.recsys_ratio` | `0.6` | float |
| `env.gm.components.observe.params.timeline_config.follower_ratio` | `0.4` | float |
| `env.gm.components.resolve.built_in` | `"tool_calling"` | str |
| `env.gm.components.resolve.class_path` | `null` | null |
| `env.gm.components.resolve.params` | `{}` | dict |
| `env.gm.components.action_prompt.built_in` | `"default"` | str |
| `env.gm.components.action_prompt.class_path` | `null` | null |
| `env.gm.components.action_prompt.params.action_prompt` | `"You are operating on a Twitter-like social backend.  The backend exposes act...` | str |
| `env.gm.components.action_prompt.params.output_style` | `"## OUTPUT FORMAT Answer: {name} STEP 1: [Analyze {name}'s motivation based o...` | str |
| `env.gm.name` | `"twitter_like_gm"` | str |
| `env.gm.class_path` | `"silisocs.environments.gm.game_master.ComponentGameMaster"` | str |

## eval (`eval/base.yaml`)

| Key | Default | Type |
|-----|---------|------|
| `eval.probes.schedule.built_in` | `"step_schedule"` | str |
| `eval.probes.schedule.class_path` | `null` | null |
| `eval.probes.schedule.params` | `{}` | dict |
