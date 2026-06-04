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

from silisocs.evaluations.probes.deployment import DefaultProbeRunner
from silisocs.initialization.agents import build_agent_initializer
from silisocs.initialization.context import InitializationContext
from silisocs.initialization.game_masters import build_game_master_initializer_strategy
from silisocs.initialization.simulation import build_simulation_initializer
from silisocs.runtime.checkpointing import (
    build_checkpoint_restore,
    checkpoint_has_backend_state,
    checkpoint_runtime_metadata,
    load_checkpoint_file,
    load_checkpoint_into_runtime,
    resolve_checkpoint_source,
    restore_rng_state_from_metadata,
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
from silisocs.runtime.construction.game_masters import build_game_masters
from silisocs.runtime.construction.initialization_context import (
    build_initializer_context,
    populate_agent_data,
)
from silisocs.runtime.io import configure_logging
from silisocs.runtime.language_models import LanguageModel, select_large_language_model
from silisocs.runtime.telemetry import SimMetricsCollector

# Package root (src/silisocs)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONF_DIR = PACKAGE_ROOT / "conf"
RUNTIME_LAYER_NAME = "silisocs-native"


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
    print("\n" + "=" * 80)
    print("STARTING SIMULATION")
    print("=" * 80)
    _initialize_runtime_environment()

    # Initialize metrics collector
    metrics = SimMetricsCollector.reset()
    metrics.mark_sim_start()

    # Setup Logging and Environment
    logger = logging.getLogger(__name__)

    # Load environment variables
    if load_dotenv(find_dotenv()):
        logger.info(f"Successfully loaded .env file from: {find_dotenv()}")
    else:
        logger.warning("Warning: .env file not found or empty.")

    configure_logging(logger)
    cfg = merge_external_group_overrides(cfg)
    RuntimeProjection.from_cfg(cfg)

    # Determine scenario path for file validation.
    # Check top-level scenarios/ first, fall back to in-package.
    project_root = _resolve_project_root()
    top_scenario = project_root / "scenarios" / cfg.scenario_name
    pkg_scenario = PACKAGE_ROOT / "scenarios" / cfg.scenario_name
    scenario_path = top_scenario if top_scenario.is_dir() else pkg_scenario

    # Run all config schema validation checks
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
        # Add hydra-generated output path
        output_dir = os.path.join(
            HydraConfig.get().runtime.output_dir,
            HydraConfig.get().job.name,
        )

    # Update config with output directory
    # Disable struct mode to allow setting new keys
    OmegaConf.set_struct(cfg, False)
    cfg.output_rootname = output_dir
    cfg.scenario_name = str(getattr(cfg, "scenario_name", "default") or "default")
    OmegaConf.set_struct(cfg, True)

    print(f"\nOutput directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    run_stats_path = os.path.join(output_dir, "run_stats.log")

    # Persist runtime-effective config after external overrides are applied.
    effective_cfg_path = os.path.join(output_dir, "effective_config.yaml")
    OmegaConf.save(config=cfg, f=effective_cfg_path, resolve=True)
    logger.info("Wrote runtime-effective config to: %s", effective_cfg_path)

    # Mirror runtime-effective config next to Hydra's composed snapshot.
    # Hydra writes config.yaml under configs/<run_root_name>/ for this project.
    run_root_name = os.path.basename(HydraConfig.get().runtime.output_dir)
    config_snapshot_dir = os.path.join(
        HydraConfig.get().runtime.output_dir,
        "configs",
        run_root_name,
    )
    os.makedirs(config_snapshot_dir, exist_ok=True)
    effective_cfg_snapshot_path = os.path.join(config_snapshot_dir, "effective_config.yaml")
    OmegaConf.save(config=cfg, f=effective_cfg_snapshot_path, resolve=True)
    logger.info("Wrote runtime-effective config snapshot to: %s", effective_cfg_snapshot_path)

    def _log_startup_phase(phase_name: str, duration_s: float, details: str = "") -> None:
        """_log_startup_phase.

        :param str phase_name:
        :type phase_name: str
        :param float duration_s:
        :type duration_s: float
        :param str details:
        :type details: str

        :returns: None
        :rtype: None
        """
        details_part = f" {details}" if details else ""
        line = f"Startup {phase_name}: {duration_s:.2f}s{details_part}"
        logger.info(line)
        with open(run_stats_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # build gamemasters (scenario agnostic)
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
    populate_agent_data(agent_configs, game_masters)
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
    llm_cfg = cfg.sim.llm
    llm_api_base = getattr(llm_cfg, "api_base", None) or None
    llm_api_key = getattr(llm_cfg, "api_key", None) or None
    llm_provider = getattr(llm_cfg, "provider", None)
    raw_llm_extra_kwargs = getattr(llm_cfg, "extra_kwargs", {}) or {}
    llm_extra_kwargs = cast(
        dict[str, Any],
        OmegaConf.to_container(raw_llm_extra_kwargs, resolve=True)
        if isinstance(raw_llm_extra_kwargs, DictConfig)
        else dict(raw_llm_extra_kwargs),
    )
    # Build models map and agent->model mapping for all instances.
    # If an instance doesn't specify a model name, default to `cfg.sim.llm.name`.
    t0 = time.time()
    with metrics.phase("model_creation"):
        models: dict[str, LanguageModel] = {}
        object_to_model: dict[str, str] = {}
        for instance in instances:
            try:
                model_name = instance.params["model"]["name"]
            except Exception:
                model_name = None

            if not model_name:
                model_name = llm_cfg.name

            # map agent name to the model name
            object_to_model[instance.params["name"]] = model_name

            # load the model once per unique name
            if model_name not in models:
                models[model_name] = select_large_language_model(
                    model_name,
                    prompts_file,
                    True,
                    disable_language_model=getattr(llm_cfg, "disabled", False),
                    api_base=llm_api_base,
                    api_key=llm_api_key,
                    temperature=float(getattr(llm_cfg, "temperature", 0.5)),
                    provider=llm_provider,
                    extra_kwargs=llm_extra_kwargs,
                )

        # Use the configured default model for compatibility (should be present in `models`).
        model = models.get(llm_cfg.name)
        if model is None:
            model = select_large_language_model(
                llm_cfg.name,
                prompts_file,
                True,
                disable_language_model=getattr(llm_cfg, "disabled", False),
                api_base=llm_api_base,
                api_key=llm_api_key,
                temperature=float(getattr(llm_cfg, "temperature", 0.5)),
                provider=llm_provider,
                extra_kwargs=llm_extra_kwargs,
            )
    _log_startup_phase("model_creation", time.time() - t0, f"unique_models={len(models)}")

    sim_engine = build_engine(cfg)

    t0 = time.time()
    runtime_objects = construct_runtime_with_metrics(
        specs=instances,
        models=models,
        object_to_model=object_to_model,
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
    source_run = None
    if checkpoint_cfg is not None:
        source_run = getattr(checkpoint_cfg, "source_run", None)

    start_step = 0
    checkpoint_meta: dict[str, Any] = {}
    checkpoint_restore = None
    checkpoint_action_events = None
    checkpoint_data: dict[str, Any] | None = None
    checkpoint_backend_state_authoritative = False
    if source_run:
        if checkpoint_cfg is None or getattr(checkpoint_cfg, "restore", None) is None:
            raise ValueError("sim.checkpoint.source_run requires sim.checkpoint.restore.")
        source_path = Path(str(source_run)).expanduser()
        if not source_path.is_absolute():
            source_path = (Path.cwd() / source_path).resolve()
        resume_path, checkpoint_action_events = resolve_checkpoint_source(source_path)
        checkpoint_restore = build_checkpoint_restore(getattr(checkpoint_cfg, "restore", None))

        with metrics.phase("checkpoint_load"):
            checkpoint_data = load_checkpoint_file(resume_path)
            checkpoint_backend_state_authoritative = checkpoint_has_backend_state(checkpoint_data)
            checkpoint_meta = checkpoint_runtime_metadata(checkpoint_data)

        default_step = checkpoint_data.get("step")
        if default_step is None:
            raise ValueError("Checkpoint is missing required `step` value.")
        start_step = int(default_step)

        logger.info(
            "Restoring from checkpoint %s at step %d",
            resume_path,
            start_step,
        )
        checkpoint_meta["checkpoint_step"] = start_step
        checkpoint_meta["checkpoint_file"] = str(resume_path)
        checkpoint_meta["source_run"] = str(source_path)
        checkpoint_meta["action_events_file"] = str(checkpoint_action_events)
        initializer_context = InitializationContext(
            shared_memories=initializer_context.shared_memories,
            player_specific_memories=initializer_context.player_specific_memories,
            player_specific_context=initializer_context.player_specific_context,
            sim_roles=initializer_context.sim_roles,
            agent_flow_tags=initializer_context.agent_flow_tags,
            agent_bios=initializer_context.agent_bios,
            checkpoint=checkpoint_meta,
        )

    probes_cfg = OmegaConf.select(cfg, "eval.probes") or OmegaConf.select(cfg, "evaluations.probes")
    probes_cfg_map = (
        cast(dict[str, Any], OmegaConf.to_container(probes_cfg, resolve=True))
        if isinstance(probes_cfg, DictConfig)
        else cast(dict[str, Any], probes_cfg or {})
    )
    probe_runner = DefaultProbeRunner(probes_cfg_map, output_dir)
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
                initializer_model=runtime_objects.default_model(models),
            )
            if checkpoint_data is not None:
                load_checkpoint_into_runtime(
                    runtime_objects,
                    checkpoint_data,
                    models=models,
                    object_to_model=object_to_model,
                )
            if checkpoint_restore is not None and not checkpoint_backend_state_authoritative:
                if checkpoint_action_events is None:
                    raise ValueError("Checkpoint restore requires action_events.jsonl.")
                checkpoint_restore.restore(
                    game_masters=runtime_objects.game_masters_by_sequence(),
                    action_events_file=checkpoint_action_events,
                    checkpoint_step=start_step,
                )
            if checkpoint_data is not None:
                restore_rng_state_from_metadata(checkpoint_meta)
        _log_startup_phase("engine_initialize", time.time() - t0)

        t0 = time.time()
        with metrics.phase("engine_run_loop"):

            def checkpoint_callback(step: int) -> None:
                if should_save_checkpoint(step, checkpoint_cfg):
                    save_checkpoint(
                        runtime_objects,
                        step=step,
                        checkpoint_path=checkpoint_output_dir,
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
        with open(run_stats_path, "a", encoding="utf-8") as f:
            f.write(completion_line + "\n")


def _inject_external_config_path() -> None:
    """Run external config preprocessing before Hydra composes config."""
    inject_external_config_path()


def _register_search_path_plugin() -> None:
    """Register the Hydra search-path plugin."""
    register_search_path_plugin()


def cli_main() -> None:
    """CLI entry point: preprocess --config-path flags then run Hydra main."""
    if len(sys.argv) > 1 and sys.argv[1] in ("new-scenario", "new-study"):
        from silisocs.scenario_gen.cli import scenario_gen_cli

        scenario_gen_cli()
        return
    _inject_external_config_path()
    _register_search_path_plugin()
    main()


if __name__ == "__main__":
    cli_main()
