"""Legacy config views and rejection helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from omegaconf import DictConfig, OmegaConf


def _contains_key(value: object, key_name: str) -> bool:
    if isinstance(value, DictConfig):
        value = OmegaConf.to_container(value, resolve=False)
    if isinstance(value, Mapping):
        return any(k == key_name or _contains_key(v, key_name) for k, v in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_key(item, key_name) for item in value)
    return False


def reject_removed_runtime_keys(cfg: DictConfig) -> None:
    checks = [
        (
            "environment",
            "`environment` has been removed. Use canonical `env` config.",
        ),
        (
            "agents.persona_pipeline.processing_mode",
            "`agents.persona_pipeline.processing_mode` has been removed. "
            "Configure `sim.initialization.agents.built_in` instead.",
        ),
        (
            "sim.initializer",
            "`sim.initializer` has been removed. Use `sim.initialization.agents`, "
            "`sim.initialization.game_masters`, and `sim.initialization.simulation` instead.",
        ),
        (
            "env.seed_posts",
            "`env.seed_posts` has been removed. Configure `sim.initialization.simulation` instead.",
        ),
        (
            "env.social_network",
            "`env.social_network` has been removed. Put graph setup under "
            "`env.gm.components.initialize.params.graph` and activity rates under "
            "`env.gm.components.next_acting.params.activity_transition_rates`.",
        ),
        (
            "env.timeline_mode",
            "`env.timeline_mode` has been removed. Use "
            "`env.gm.components.observe.params.timeline_mode`.",
        ),
        (
            "env.timeline_config",
            "`env.timeline_config` has been removed. Use "
            "`env.gm.components.observe.params.timeline_config`.",
        ),
        (
            "env.timeline_posts",
            "`env.timeline_posts` has been removed. Use "
            "`env.gm.components.observe.params.timeline_posts`.",
        ),
        (
            "env.enabled_actions",
            "`env.enabled_actions` has been removed. Use `env.backend.enabled_actions`.",
        ),
        (
            "env.use_server",
            "`env.use_server` has been removed. Use `env.backend.params.perform_operations`.",
        ),
        (
            "env.usage_instructions",
            "`env.usage_instructions` has been removed. Put prompt text under "
            "`env.gm.components.action_prompt.params.action_prompt`.",
        ),
        (
            "env.action_prompt",
            "`env.action_prompt` has been removed. Use "
            "`env.gm.components.action_prompt.params.action_prompt`.",
        ),
        (
            "env.output_style",
            "`env.output_style` has been removed. Use "
            "`env.gm.components.action_prompt.params.output_style`.",
        ),
        (
            "env.gamemaster",
            "`env.gamemaster` has been removed. Use `env.gm.name` and `env.gm.class_path`.",
        ),
        (
            "env.enable_gm_multi_flow",
            "`env.enable_gm_multi_flow` has been removed. Set `env.gm.class_path` directly.",
        ),
        (
            "env.gm.preset",
            "`env.gm.preset` has been removed. Set `env.gm.class_path` directly.",
        ),
        (
            "env.platform_type",
            "`env.platform_type` has been removed. Use `env.backend.type`.",
        ),
        (
            "env.app",
            "`env.app` has been removed. Use `env.backend.class_path` and `env.backend.params`.",
        ),
        (
            "env.gm.components.initializer",
            "`env.gm.components.initializer` has been removed. "
            "Use `env.gm.components.initialize` instead.",
        ),
        (
            "sim.memory_backend",
            "`sim.memory_backend` has been removed from native runtime. "
            "Use native agent initialization, or configure Concordia memory inside explicit "
            "`compat: concordia` modules.",
        ),
        ("sim.sentence_encoder", "`sim.sentence_encoder` has been removed from native runtime."),
        (
            "sim.engine.preset",
            "`sim.engine.preset` has been removed. "
            "Use `sim.engine.step.built_in` (`base` | `flow` | `multi_gm`).",
        ),
        (
            "sim.engine.action_loop",
            "`sim.engine.action_loop` has been removed. Use `sim.engine.turn_policy`.",
        ),
        (
            "sim.engine.probe_schedule",
            "`sim.engine.probe_schedule` has been removed. Use `evals.probes.schedule`.",
        ),
        (
            "sim.checkpoint.resume_file",
            "`sim.checkpoint.resume_file` has been removed. Use `sim.checkpoint.source_run`.",
        ),
        (
            "sim.checkpoint.resume_step",
            "`sim.checkpoint.resume_step` has been removed. Restore selects the checkpoint step.",
        ),
        (
            "evals.write_html_log",
            "`evals.write_html_log` has been removed; HTML logs are unsupported.",
        ),
        (
            "sim.engine.flow_routing.entity_to_flow",
            "`sim.engine.flow_routing.entity_to_flow` has been removed. "
            "Use `sim.engine.step.params.agent_to_flow`.",
        ),
    ]
    for key, message in checks:
        if OmegaConf.select(cfg, key) is not None:
            raise ValueError(message)
    if OmegaConf.select(cfg, "sc") is not None:
        raise ValueError("`cfg.sc` has been removed. Use canonical scenario config.")
    if _contains_key(cfg, "active_rates"):
        raise ValueError(
            "`active_rates` has been removed. Use "
            "`env.gm.components.next_acting.params.activity_transition_rates`."
        )
    if str(OmegaConf.select(cfg, "sim.llm.provider", default="") or "").strip().lower() == "local":
        raise ValueError("The local LLM provider name has been removed. Use `openai_compatible`.")
    if OmegaConf.select(cfg, "sim.initialization.simulation.built_in") == "checkpoint_replay":
        raise ValueError(
            "`checkpoint_replay` has been removed from simulation initialization. "
            "Use `sim.checkpoint.source_run` with `sim.checkpoint.restore`."
        )
    reject_legacy_probe_config(cfg)


def reject_legacy_probe_config(cfg: DictConfig) -> None:
    probes_cfg = OmegaConf.select(cfg, "evals.probes") or OmegaConf.select(
        cfg,
        "evaluations.probes",
    )
    if probes_cfg is None:
        return
    container = OmegaConf.to_container(probes_cfg, resolve=True)
    if not isinstance(container, Mapping):
        return

    deployment = container.get("deployment", {})
    if isinstance(deployment, Mapping):
        legacy_filters = sorted(
            key for key in ("include_entities", "exclude_entities") if key in deployment
        )
        if legacy_filters:
            raise ValueError(
                "Probe deployment uses removed entity filter key(s): "
                + ", ".join(legacy_filters)
                + ". Use include_agents/exclude_agents."
            )

    raw_probes = container.get("probes", {})
    if isinstance(raw_probes, Mapping):
        probe_items = list(raw_probes.values())
    elif isinstance(raw_probes, Sequence) and not isinstance(raw_probes, (str, bytes)):
        probe_items = list(raw_probes)
    else:
        probe_items = []
    for index, item in enumerate(probe_items):
        if not isinstance(item, Mapping):
            continue
        legacy_probe_keys = sorted(key for key in ("query_type", "query_data") if key in item)
        if legacy_probe_keys:
            raise ValueError(
                f"Probe config at index {index} uses removed key(s): "
                + ", ".join(legacy_probe_keys)
                + ". Use probe_type/probe_data."
            )
