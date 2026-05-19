"""Legacy config views and rejection helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from omegaconf import DictConfig, OmegaConf


def reject_removed_runtime_keys(cfg: DictConfig) -> None:
    checks = [
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


def build_legacy_scenario_view(cfg: DictConfig) -> DictConfig:
    payload = {
        "scenario_name": OmegaConf.select(cfg, "scenario_name"),
        "jobname_format": OmegaConf.select(cfg, "jobname_format"),
        "setting": OmegaConf.select(cfg, "setting") or {},
        "event": OmegaConf.select(cfg, "event") or {},
        "data": OmegaConf.select(cfg, "data") or {},
        "social_network": OmegaConf.select(cfg, "env.social_network") or {},
        "persona_pipeline": OmegaConf.select(cfg, "agents.persona_pipeline") or {},
        "shared_memories": OmegaConf.select(cfg, "agents.shared_memories") or [],
        "initial_observations": OmegaConf.select(cfg, "agents.initial_observations") or [],
        "probes": OmegaConf.select(cfg, "evals.probes")
        or OmegaConf.select(cfg, "evaluations.probes")
        or {},
        "seed_posts": OmegaConf.select(cfg, "sim.initialization.simulation.params") or {},
        "fixed_action_sets": OmegaConf.select(cfg, "agents.fixed_action_sets") or {},
        "candidates": OmegaConf.select(cfg, "env.candidates") or {},
        "news_account": OmegaConf.select(cfg, "env.news_account") or {},
        "partisan_types": OmegaConf.select(cfg, "env.partisan_types") or [],
    }
    return OmegaConf.create(payload)
