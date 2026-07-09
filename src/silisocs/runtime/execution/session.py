"""Runtime entrypoint for experiments.

This module provides the CLI entrypoint used to compose experiment
configurations with Hydra and launch simulations. It exposes :func:`main`,
which is decorated with :func:`hydra.main` and expects the composed
``experiment`` configuration group.

The CLI accepts a ``--config-path`` allowing external scenario directories to
override package defaults. Use repeated ``--overlay-config-path`` flags to
layer additional override trees.
"""

import logging
import os
import random
import sys
import time
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import hydra

# Environment
from dotenv import find_dotenv, load_dotenv
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf

from silisocs.agents.memory import build_memory_policy
from silisocs.evaluations.probes.deployment import DefaultProbeRunner
from silisocs.initialization.agents import build_agent_initializer
from silisocs.initialization.game_masters import build_game_master_initializer_strategy
from silisocs.initialization.simulation import build_simulation_initializer
from silisocs.runtime.checkpointing import (
    build_checkpoint_save_strategy,
    build_per_gm_checkpoint_restores,
    load_checkpoint_into_runtime,
    restore_rng_state_from_metadata,
    run_checkpoint_restores,
    save_checkpoint,
    should_save_checkpoint,
)
from silisocs.runtime.configuration.external import (
    inject_external_config_path,
    merge_external_group_overrides,
    register_search_path_plugin,
)
from silisocs.runtime.configuration.projection import RuntimeProjection

# Local imports
from silisocs.runtime.configuration.validation import validate_scenario_config
from silisocs.runtime.construction.agent_configs import build_agent_configs
from silisocs.runtime.construction.assembly import construct_runtime_with_metrics
from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.construction.game_masters import (
    build_game_masters,
    collapse_flow_chains,
    resolve_flow_chains,
)
from silisocs.runtime.construction.initialization_context import (
    build_initializer_context,
    populate_agent_data,
)
from silisocs.runtime.construction.models import build_deduped_models, build_global_llm_config
from silisocs.runtime.execution.manifest import write_run_manifest
from silisocs.runtime.execution.resume import plan_checkpoint_resume
from silisocs.runtime.io import configure_logging
from silisocs.runtime.telemetry import (
    SimMetricsCollector,
    collect_usage_summary,
)

# Package root (src/silisocs)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONF_DIR = PACKAGE_ROOT / "conf"
RUNTIME_LAYER_NAME = "silisocs-native"


def _record_llm_usage_summary(cfg: Any, *, models: Any, metrics: Any) -> None:
    """Record a run-level LLM token-usage summary (+cost when priced) to sim_metrics.

    ``sim.llm.pricing`` (``{input_per_1m, output_per_1m}``) is optional; without
    it only token counts are reported. Best-effort — never fails a run.
    """
    if not models:
        return
    try:
        pricing = OmegaConf.select(cfg, "sim.llm.pricing")
        pricing_map = (
            cast(dict, OmegaConf.to_container(pricing, resolve=True))
            if isinstance(pricing, DictConfig)
            else (pricing if isinstance(pricing, dict) else None)
        )
        # ``models`` is already deduped by effective config (build_deduped_models).
        model_list = list(models.values()) if isinstance(models, dict) else list(models)
        metrics.set_meta("llm_usage", collect_usage_summary(model_list, pricing_map))
    except Exception:  # pragma: no cover - telemetry must never break a run
        logging.getLogger(__name__).debug("failed to record llm usage summary", exc_info=True)


def _initialize_runtime_environment() -> Path:
    """Apply runtime setup only when the simulation entrypoint is executed.

    Resolves the project root by walking up from the package location until a
    ``pyproject.toml`` is found (works both from a repo checkout and when the
    package is installed in editable mode). Falls back to ``cwd`` when running
    from an installed wheel.
    """
    print(r"""
     _ _ _
 ___(_) (_)___  ___   ___ ___
/ __| | | / __|/ _ \ / __/ __|
\__ \ | | \__ \ (_) | (__\__ \
|___/_|_|_|___/\___/ \___|___/
""")
    print("=" * 80)
    print(f"Runtime layer: {RUNTIME_LAYER_NAME}")
    warnings.filterwarnings(action="ignore", category=FutureWarning, module="concordia")
    print("=" * 80)

    project_root = _resolve_project_root()
    print(f"Project root: {project_root}")
    print("=" * 80)

    if project_root != Path.cwd():
        os.chdir(project_root)

    src_path = project_root / "src"
    if src_path.is_dir() and str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    print(f"Config directory: {CONF_DIR}")
    return project_root


def _resolve_project_root() -> Path:
    """Walk up from the package to find the project root (contains pyproject.toml).

    Falls back to cwd when running from an installed package without a repo
    checkout above the install location.
    """
    candidate = Path(__file__).resolve().parent
    for _ in range(8):
        if (candidate / "pyproject.toml").is_file():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return Path.cwd()


# ============================================================================
# Main Experiment Function
# ============================================================================


def _setup_run_environment(
    cfg: DictConfig,
) -> tuple[DictConfig, SimMetricsCollector, logging.Logger]:
    """Print the banner and bring up the run environment.

    Initializes the runtime environment + metrics collector + logging, loads ``.env``,
    applies external group overrides, and projects the runtime config. Returns the
    merged ``cfg``, the metrics collector, and the logger the rest of ``main`` uses.
    """
    print("\n" + "=" * 80)
    print("STARTING SIMULATION")
    print("=" * 80)
    _initialize_runtime_environment()

    metrics = SimMetricsCollector.reset()
    metrics.mark_sim_start()

    logger = logging.getLogger(__name__)
    if load_dotenv(find_dotenv()):
        logger.info(f"Successfully loaded .env file from: {find_dotenv()}")
    else:
        logger.warning("Warning: .env file not found or empty.")

    configure_logging(logger)
    cfg = merge_external_group_overrides(cfg)
    RuntimeProjection.from_cfg(cfg)
    return cfg, metrics, logger


def _resolve_output_directory(
    cfg: DictConfig, metrics: SimMetricsCollector, logger: logging.Logger
) -> str:
    """Validate the scenario config and resolve/persist the run output directory.

    Runs schema validation, resolves the output directory (explicit ``output_rootname``
    or Hydra's job dir), stamps it back onto ``cfg``, and writes the runtime-effective
    config plus a snapshot next to Hydra's. Returns the resolved output directory.
    """
    project_root = _resolve_project_root()
    top_scenario = project_root / "scenarios" / cfg.scenario_name
    pkg_scenario = PACKAGE_ROOT / "scenarios" / cfg.scenario_name
    scenario_path = top_scenario if top_scenario.is_dir() else pkg_scenario

    with metrics.phase("config_validation"):
        try:
            validate_scenario_config(cfg, scenario_path)
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            raise

    configured_output_rootname = str(
        OmegaConf.select(cfg, "output_rootname", default="") or ""
    ).strip()
    if configured_output_rootname:
        output_dir = configured_output_rootname
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(output_dir)
    else:
        output_dir = os.path.join(
            HydraConfig.get().runtime.output_dir,
            HydraConfig.get().job.name,
        )

    # Disable struct mode to allow setting new keys, then re-enable.
    OmegaConf.set_struct(cfg, False)
    cfg.output_rootname = output_dir
    cfg.scenario_name = str(getattr(cfg, "scenario_name", "default") or "default")
    OmegaConf.set_struct(cfg, True)

    print(f"\nOutput directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    # Persist runtime-effective config after external overrides are applied.
    effective_cfg_path = os.path.join(output_dir, "effective_config.yaml")
    OmegaConf.save(config=cfg, f=effective_cfg_path, resolve=True)
    logger.info("Wrote runtime-effective config to: %s", effective_cfg_path)

    # Mirror it next to Hydra's composed snapshot (configs/<run_root_name>/).
    run_root_name = os.path.basename(HydraConfig.get().runtime.output_dir)
    config_snapshot_dir = os.path.join(
        HydraConfig.get().runtime.output_dir, "configs", run_root_name
    )
    os.makedirs(config_snapshot_dir, exist_ok=True)
    effective_cfg_snapshot_path = os.path.join(config_snapshot_dir, "effective_config.yaml")
    OmegaConf.save(config=cfg, f=effective_cfg_snapshot_path, resolve=True)
    logger.info("Wrote runtime-effective config snapshot to: %s", effective_cfg_snapshot_path)
    return output_dir


def _warn_degraded_health(metrics: SimMetricsCollector, logger: logging.Logger) -> None:
    """Surface degraded-run signals so silent failures cannot pass as clean runs."""
    health_counters = {
        "agent_turn_failures": "agent turns raised an exception",
        "action_parse_failures": "agent actions were dropped as unparseable",
        "action_invalid_targets": "agent actions referenced invalid target ids",
        "backend_action_errors": "backend actions raised unexpected exceptions",
    }
    for counter_name, description in health_counters.items():
        count = metrics.counter(counter_name)
        if count:
            health_line = f"⚠ DEGRADED RUN: {count} {description} (see sim_metrics.json)"
            logger.warning(health_line)
            print(health_line)


@hydra.main(version_base=None, config_path=str(CONF_DIR), config_name="experiment")
def main(cfg: DictConfig):
    """Hydra entrypoint for running an experiment.

    Parameters
    ----------
    cfg:
        Composed Hydra :class:`omegaconf.DictConfig` representing the
        experiment configuration. The config includes grouped sections such
        as ``sim``, ``agents``, ``env`` and ``eval``.

    Notes
    -----
    This function performs environment initialization, logging setup,
    agent construction, and delegates execution to the configured simulation
    engine.
    """
    cfg, metrics, logger = _setup_run_environment(cfg)
    output_dir = _resolve_output_directory(cfg, metrics, logger)
    run_stats_path = os.path.join(output_dir, "run_stats.log")

    def _log_startup_phase(phase_name: str, duration_s: float, details: str = "") -> None:
        """Log a named startup phase's duration to the logger and run-stats file."""
        details_part = f" {details}" if details else ""
        line = f"Startup {phase_name}: {duration_s:.2f}s{details_part}"
        logger.info(line)
        with open(run_stats_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # build gamemasters (world agnostic)
    t0 = time.time()
    with metrics.phase("build_game_masters"):
        game_masters = build_game_masters(cfg)
    _log_startup_phase("build_game_masters", time.time() - t0, f"count={len(game_masters)}")

    # Build agent specs with agents.builder.class_path, or the default
    # persona-pipeline builder when that slot is unset.
    t0 = time.time()
    with metrics.phase("build_agents"):
        agent_configs = build_agent_configs(cfg)
    _log_startup_phase("build_agents", time.time() - t0, f"count={len(agent_configs)}")

    t0 = time.time()
    populate_agent_data(cfg, agent_configs, game_masters)
    _log_startup_phase(
        "populate_agent_data",
        time.time() - t0,
        f"updated_game_masters={len(game_masters)}",
    )

    t0 = time.time()
    initializer_context = build_initializer_context(cfg, agent_configs)
    initialization_cfg = OmegaConf.select(cfg, "sim.initialization", default={}) or {}
    if isinstance(initialization_cfg, DictConfig):
        initialization_view = cast(
            dict[str, Any],
            OmegaConf.to_container(initialization_cfg, resolve=True),
        )
    else:
        initialization_view = dict(initialization_cfg)
    agent_initializer = build_agent_initializer(
        cast(Mapping[str, Any] | None, initialization_view.get("agents"))
    )
    game_master_initializer = build_game_master_initializer_strategy(
        cast(Mapping[str, Any] | None, initialization_view.get("game_masters"))
    )
    simulation_initializer = build_simulation_initializer(
        cast(Mapping[str, Any] | None, initialization_view.get("simulation"))
    )
    _log_startup_phase(
        "build_initializers",
        time.time() - t0,
        "phases=agents,game_masters,simulation",
    )

    SEED = cfg.seed
    random.seed(SEED)
    print(f"\n✓ Random seed set to: {SEED}")

    instances = agent_configs + game_masters

    # Record metadata
    metrics.set_meta("num_agents", len(agent_configs))
    metrics.set_meta("num_game_masters", len(game_masters))
    metrics.set_meta("num_steps", cfg.num_steps)
    metrics.set_meta("seed", SEED)
    metrics.set_meta("scenario", cfg.scenario_name)
    metrics.set_meta("llm_name", cfg.sim.llm.name)
    metrics.set_meta("output_dir", output_dir)
    metrics.set_meta("agent_names", [inst.params["name"] for inst in agent_configs])

    prompts_file = os.path.join(output_dir, "prompts_and_responses.jsonl")
    # Global sim.llm block is the universal baseline; per-instance `model` dicts
    # (scalar names already arrive as {'name': ...}) override it field-by-field.
    # Built off MODEL_FIELDS in construction/models.py so it can't drift from the resolver.
    global_llm = build_global_llm_config(cfg.sim.llm)

    # One model per distinct effective config (global sim.llm layered with each
    # instance's per-class `model` override); deduped in construction/models.py.
    t0 = time.time()
    with metrics.phase("model_creation"):
        models, object_to_model, model = build_deduped_models(instances, global_llm, prompts_file)
    _log_startup_phase("model_creation", time.time() - t0, f"unique_models={len(models)}")

    # The flow -> GM-sequence routing topology is engine/scheduling config (not GM
    # state): the step strategy routes by it and the checkpoint replay router needs
    # it. Resolve once from config and hand it to both consumers.
    flow_chains = resolve_flow_chains(cfg)
    sim_engine = build_engine(cfg, flow_chains=flow_chains, sim_roles=initializer_context.sim_roles)

    t0 = time.time()
    memory_cfg = OmegaConf.select(cfg, "sim.memory")
    memory_factory = build_memory_policy(
        cast(dict, OmegaConf.to_container(memory_cfg, resolve=True))
        if isinstance(memory_cfg, DictConfig)
        else None
    )
    runtime_objects = construct_runtime_with_metrics(
        specs=instances,
        models=models,
        object_to_model=object_to_model,
        memory_factory=memory_factory,
    )
    all_agent_names = [agent.name for agent in runtime_objects.agents]
    for built_model in models.values():
        if hasattr(built_model, "agent_names"):
            try:
                built_model.agent_names = list(all_agent_names)  # type: ignore[attr-defined]
            except Exception:
                pass
        rebuild_index = getattr(built_model, "_rebuild_agent_name_index", None)
        if callable(rebuild_index):
            rebuild_index()
    _log_startup_phase("runtime_construction", time.time() - t0)

    checkpoint_cfg = getattr(cfg.sim, "checkpoint", None)
    resume = plan_checkpoint_resume(
        checkpoint_cfg=checkpoint_cfg,
        output_dir=output_dir,
        initializer_context=initializer_context,
        metrics=metrics,
        logger=logger,
    )
    start_step = resume.start_step
    checkpoint_data = resume.checkpoint_data
    checkpoint_restore = resume.checkpoint_restore
    checkpoint_action_event_files = resume.action_event_files
    checkpoint_authoritative_gms = resume.authoritative_gm_names
    checkpoint_meta = resume.checkpoint_meta
    initializer_context = resume.initializer_context

    probes_cfg = OmegaConf.select(cfg, "eval.probes") or OmegaConf.select(cfg, "evaluations.probes")
    probes_cfg_map = (
        cast(dict[str, Any], OmegaConf.to_container(probes_cfg, resolve=True))
        if isinstance(probes_cfg, DictConfig)
        else cast(dict[str, Any], probes_cfg or {})
    )
    probe_runner = DefaultProbeRunner(
        probes_cfg_map, output_dir, seed=int(getattr(cfg, "seed", 0) or 0)
    )
    sim_engine.probe_runner = probe_runner

    checkpoint_output_dir = os.path.join(output_dir, "checkpoints")
    completion_status = "success"
    completion_error = ""
    try:
        t0 = time.time()
        with metrics.phase("engine_initialize"):
            sim_engine.initialize(
                agents=runtime_objects.agents,
                game_masters=runtime_objects.game_masters_by_sequence(),
                agent_initializer=agent_initializer if start_step <= 0 else None,
                game_master_initializer=game_master_initializer,
                simulation_initializer=simulation_initializer if start_step <= 0 else None,
                initialization_context=initializer_context,
                # Use the GLOBAL (no-override) model for runtime initialization, not
                # whichever model the first-declared class happens to resolve to (a
                # per-class block could disable it / change provider).
                initializer_model=model,
            )
            if checkpoint_data is not None:
                load_checkpoint_into_runtime(
                    runtime_objects,
                    checkpoint_data,
                    models=models,
                    object_to_model=object_to_model,
                )
            if checkpoint_restore is not None:
                restore_gms = runtime_objects.game_masters_by_sequence()
                all_gm_names = frozenset(str(getattr(gm, "name", "")) for gm in restore_gms)
                # Replay only the game masters that lack an authoritative snapshot;
                # snapshot-restored GMs were already applied by set_state above. Each
                # GM may override the global strategy via its own restore config.
                if all_gm_names - checkpoint_authoritative_gms:
                    run_checkpoint_restores(
                        game_masters=restore_gms,
                        default_strategy=checkpoint_restore,
                        per_gm_strategies=build_per_gm_checkpoint_restores(cfg),
                        action_events_files=checkpoint_action_event_files,
                        checkpoint_step=start_step,
                        authoritative_gm_names=checkpoint_authoritative_gms,
                        flow_chains=collapse_flow_chains(flow_chains),
                    )
            if checkpoint_data is not None:
                restore_rng_state_from_metadata(checkpoint_meta)
        _log_startup_phase("engine_initialize", time.time() - t0)

        t0 = time.time()
        with metrics.phase("engine_run_loop"):
            checkpoint_save_strategy = build_checkpoint_save_strategy(
                getattr(checkpoint_cfg, "save", None)
            )

            def checkpoint_callback(step: int) -> None:
                if should_save_checkpoint(step, checkpoint_cfg):
                    save_checkpoint(
                        runtime_objects,
                        step=step,
                        checkpoint_path=checkpoint_output_dir,
                        save_strategy=checkpoint_save_strategy,
                    )

            sim_engine.run_loop(
                game_masters=runtime_objects.game_masters_by_sequence(),
                agents=runtime_objects.agents,
                max_steps=cfg.num_steps,
                start_step=start_step,
                verbose=True,
                checkpoint_callback=checkpoint_callback,
            )
        _log_startup_phase(
            "engine_run_loop",
            time.time() - t0,
            f"max_steps={cfg.num_steps}",
        )
    except Exception as e:
        completion_status = "failed"
        completion_error = str(e)
        logger.exception("Simulation execution failed before completion marker.")
        raise
    finally:
        # Finalize and write metrics
        metrics.mark_sim_end()
        _record_llm_usage_summary(cfg, models=locals().get("models"), metrics=metrics)
        metrics.write_json(output_dir)

        completion_line = (
            "Simulation complete: "
            f"status={completion_status} "
            f"episodes={cfg.num_steps} "
            f"output_dir={output_dir}"
        )
        if completion_error:
            completion_line += f" error={completion_error}"
        logger.info(completion_line)
        print(completion_line)

        _warn_degraded_health(metrics, logger)
        with open(run_stats_path, "a", encoding="utf-8") as f:
            f.write(completion_line + "\n")

        # Self-describing run index (never raises; see manifest module docstring).
        snapshot = metrics.to_dict()
        manifest_path = write_run_manifest(
            output_dir=output_dir,
            status=completion_status,
            error=completion_error,
            meta=snapshot.get("meta"),
            counters=snapshot.get("counters"),
            game_masters=runtime_objects.game_masters_by_sequence(),
            project_root=_resolve_project_root(),
        )
        if manifest_path is not None:
            logger.info("Wrote run manifest to: %s", manifest_path)


def _inject_external_config_path() -> None:
    """Run external config preprocessing before Hydra composes config."""
    inject_external_config_path()


def _register_search_path_plugin() -> None:
    """Register the Hydra search-path plugin."""
    register_search_path_plugin()


def cli_main() -> None:
    """CLI entry point: preprocess --config-path flags then run Hydra main."""
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from silisocs.runtime.doctor import run_doctor

        sys.exit(run_doctor(sys.argv[2] if len(sys.argv) > 2 else None))
    if len(sys.argv) > 1 and sys.argv[1] in ("new-scenario", "new-study"):
        from silisocs.scenario_gen.cli import scenario_gen_cli

        scenario_gen_cli()
        return
    _inject_external_config_path()
    _register_search_path_plugin()
    main()


if __name__ == "__main__":
    cli_main()
