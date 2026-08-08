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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, cast

import hydra

# Environment
from dotenv import find_dotenv, load_dotenv
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf

from silisocs.agents.harness.runtime import (
    PromptProxyLogger,
    harness_usage_models,
    setup_harness_proxy,
)
from silisocs.agents.memory import build_memory_policy
from silisocs.evaluations.probes.deployment import DefaultProbeRunner
from silisocs.evaluations.vocabulary import HEALTH_COUNTERS
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
from silisocs.runtime.execution.manifest import backend_committed_nothing, write_run_manifest
from silisocs.runtime.execution.resume import plan_checkpoint_resume
from silisocs.runtime.execution.run_events import RunEventLog
from silisocs.runtime.io import configure_logging
from silisocs.runtime.plugins import load_plugins
from silisocs.runtime.telemetry import (
    SimMetricsCollector,
    collect_usage_summary,
)
from silisocs.simulation_engines.control import build_run_control

# Package root (src/silisocs)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONF_DIR = PACKAGE_ROOT / "conf"
RUNTIME_LAYER_NAME = "silisocs-native"


class ConfigValidationError(ValueError):
    """A scenario config rejected by :func:`validate_scenario_config`.

    Distinct from a plain ``ValueError`` so the CLI can report bad user input as
    one actionable message instead of a stack trace, while a genuine internal
    bug still surfaces its full traceback.

    Only the exception types the validators raise deliberately (``ValueError``
    and ``FileNotFoundError``) are wrapped. A validator's own ``AttributeError``
    or ``TypeError`` is a bug in this codebase, not bad user input, and keeps its
    traceback rather than masquerading as a one-line config error.
    """


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

    # Make BOTH the src layout and scenario-local code importable: with the
    # project root on sys.path, `scenarios/<name>/builders.py` resolves as
    # `scenarios.<name>.builders` (implicit namespace packages) from a plain
    # CLI invocation — not only under Studio, which patches the path itself.
    for extra in (project_root / "src", project_root):
        if extra.is_dir() and str(extra) not in sys.path:
            sys.path.insert(0, str(extra))

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
    # Import plugin modules (config `plugins:` list + silisocs.plugins entry
    # points) BEFORE validation, so registered providers/mappers/components
    # resolve like built-ins everywhere downstream.
    load_plugins(cast(Any, OmegaConf.select(cfg, "plugins", default=None)))
    RuntimeProjection.from_cfg(cfg)
    return cfg, metrics, logger


_REDACTED_SECRET = "**redacted**"


def _redact_secrets(node: Any) -> Any:
    """Return a copy of a plain config container with credential values masked.

    Masks every non-empty ``api_key`` value (the documented config-settable
    credential field, global ``sim.llm`` and per-class model blocks alike) so
    on-disk config artifacts never carry a live key. Mirrors the intent of
    ``construction/models._effective_model_key``, which hashes keys before
    using them in log signatures.
    """
    if isinstance(node, dict):
        return {
            key: (_REDACTED_SECRET if key == "api_key" and value else _redact_secrets(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_redact_secrets(item) for item in node]
    return node


def _hydra_run_dir() -> str | None:
    """Hydra's job output dir, or ``None`` when not running under a Hydra app."""
    try:
        return str(HydraConfig.get().runtime.output_dir)
    except ValueError:
        return None


def _resolve_output_directory(
    cfg: DictConfig,
    metrics: SimMetricsCollector,
    logger: logging.Logger,
    *,
    explicit_output_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Validate the scenario config and resolve/persist the run output directory.

    Runs schema validation, resolves the output directory (explicit argument,
    ``output_dir``, or Hydra's job dir, in that order), stamps it back
    onto ``cfg``, and writes the runtime-effective config (plus a snapshot next
    to Hydra's when running under Hydra). Returns the resolved directory.
    """
    project_root = _resolve_project_root()
    top_scenario = project_root / "scenarios" / cfg.scenario_name
    pkg_scenario = PACKAGE_ROOT / "scenarios" / cfg.scenario_name
    scenario_path = top_scenario if top_scenario.is_dir() else pkg_scenario

    with metrics.phase("config_validation"):
        try:
            validate_scenario_config(cfg, scenario_path)
        # Deliberately narrow: these are the two types the validators raise for
        # rejected user config. Anything else (AttributeError, TypeError, ...) is
        # an internal validator bug and must keep its traceback.
        except (ValueError, FileNotFoundError) as e:
            raise ConfigValidationError(f"Configuration validation failed: {e}") from e

    configured_output_dir = str(OmegaConf.select(cfg, "output_dir", default="") or "").strip()
    if explicit_output_dir is not None:
        output_dir = os.path.abspath(os.fspath(explicit_output_dir))
    elif configured_output_dir:
        output_dir = configured_output_dir
        if not os.path.isabs(output_dir):
            output_dir = os.path.abspath(output_dir)
    else:
        hydra_dir = _hydra_run_dir()
        if hydra_dir is None:
            raise ValueError(
                "No output directory: outside a Hydra invocation, pass "
                "run_simulation(cfg, output_dir=...) or set 'output_dir' "
                "in the config."
            )
        output_dir = os.path.join(hydra_dir, HydraConfig.get().job.name)

    # Disable struct mode to allow setting new keys, then re-enable.
    OmegaConf.set_struct(cfg, False)
    cfg.output_dir = output_dir
    cfg.scenario_name = str(getattr(cfg, "scenario_name", "default") or "default")
    OmegaConf.set_struct(cfg, True)

    print(f"\nOutput directory: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

    # Persist runtime-effective config after external overrides are applied.
    # Secrets are redacted first: this file is a run artifact (rendered verbatim
    # by Studio's Effective YAML panel, archived, shared) and nothing reads model
    # credentials back out of it — the runtime resolves keys from live config/env.
    redacted_cfg = _redact_secrets(cast(Any, OmegaConf.to_container(cfg, resolve=True)))
    effective_cfg_path = os.path.join(output_dir, "effective_config.yaml")
    OmegaConf.save(config=OmegaConf.create(redacted_cfg), f=effective_cfg_path)
    logger.info("Wrote runtime-effective config to: %s", effective_cfg_path)

    # Mirror it next to Hydra's composed snapshot (configs/<run_root_name>/) —
    # only meaningful under a Hydra invocation.
    hydra_dir = _hydra_run_dir()
    if hydra_dir is not None:
        config_snapshot_dir = os.path.join(hydra_dir, "configs", os.path.basename(hydra_dir))
        os.makedirs(config_snapshot_dir, exist_ok=True)
        effective_cfg_snapshot_path = os.path.join(config_snapshot_dir, "effective_config.yaml")
        OmegaConf.save(config=OmegaConf.create(redacted_cfg), f=effective_cfg_snapshot_path)
        logger.info("Wrote runtime-effective config snapshot to: %s", effective_cfg_snapshot_path)
    return output_dir


def _warn_degraded_health(metrics: SimMetricsCollector, logger: logging.Logger) -> None:
    """Surface degraded-run signals so silent failures cannot pass as clean runs."""
    for counter_name, description in HEALTH_COUNTERS.items():
        count = metrics.counter(counter_name)
        if count:
            health_line = f"⚠ DEGRADED RUN: {count} {description} (see sim_metrics.json)"
            logger.warning(health_line)
            print(health_line)


def _warn_silent_backends(game_masters: Sequence[Any], logger: logging.Logger) -> None:
    """Surface backends that committed no structured action events.

    Invoke-layer auto-logging covers ordinary ``@app_action`` calls. This warning
    catches runs where no action committed, or where a custom dispatch path
    bypassed invoke without calling ``_log_action_event`` itself.
    """
    for gm in game_masters:
        if not backend_committed_nothing(gm):
            continue
        line = (
            f"⚠ NO ACTION EVENTS: backend {getattr(gm, 'backend_type', '?')!r} "
            f"(game master {getattr(gm, 'name', '?')!r}) committed no structured action events, "
            "so analysis of this run will show nothing it did. Either no agent action succeeded, "
            "its actions opted out of auto-logging, or a custom dispatch path bypassed the invoke "
            "layer without calling _log_action_event(...) (see docs/backends.md)."
        )
        logger.warning(line)
        print(line)


def run_simulation(cfg: DictConfig, *, output_dir: str | os.PathLike[str] | None = None) -> Path:
    """Run a composed experiment configuration to completion (programmatic API).

    The in-process twin of the ``silisocs`` CLI, for notebooks, tools, and
    orchestrators::

        from silisocs import compose_config, run_simulation

        cfg = compose_config(overrides=["num_steps=2", "sim.llm.provider=scripted"])
        run_dir = run_simulation(cfg, output_dir="outputs/notebook_run")

    ``cfg`` is any composed experiment config (see :func:`compose_config`).
    Outside a Hydra invocation, pass ``output_dir`` explicitly or set
    ``output_dir`` in the config. Returns the resolved output directory
    holding the run's artifacts (``run_manifest.json``, event logs,
    checkpoints, ...); load them with
    :func:`silisocs.evaluations.run_artifact.load_run`.
    """
    cfg, metrics, logger = _setup_run_environment(cfg)
    output_dir = _resolve_output_directory(cfg, metrics, logger, explicit_output_dir=output_dir)
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
            built_model.agent_names = list(all_agent_names)  # type: ignore[attr-defined]
        rebuild_index = getattr(built_model, "_rebuild_agent_name_index", None)
        if callable(rebuild_index):
            rebuild_index()
    _log_startup_phase("runtime_construction", time.time() - t0)

    checkpoint_output_dir = os.path.join(output_dir, "checkpoints")
    completion_status = "success"
    completion_error = ""
    # From here on, setup starts real threads/sockets (harness proxy, run
    # controller), so it runs under the completion `try`: a config error raised
    # in resume planning, probe config, or run control still reaches the
    # `finally` that stops them — and marks the run failed instead of vanishing.
    harness_proxy = None
    run_controller = None
    run_events: RunEventLog | None = None
    try:
        # Harness Model Proxy: one telemetry plane for harness + native model calls. Starts
        # only when the run has harness agents; binds each to a per-agent routing token; its
        # usage accumulators fold into the unified llm_usage summary. Stopped in `finally`.
        harness_proxy = setup_harness_proxy(
            runtime_objects.agents, prompt_logger=PromptProxyLogger(prompts_file)
        )
        for usage_model in harness_usage_models(harness_proxy):
            models[f"harness::{getattr(usage_model, '_model_name', 'model')}"] = usage_model

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

        probes_cfg = OmegaConf.select(cfg, "eval.probes") or OmegaConf.select(
            cfg, "evaluations.probes"
        )
        probes_cfg_map = (
            cast(dict[str, Any], OmegaConf.to_container(probes_cfg, resolve=True))
            if isinstance(probes_cfg, DictConfig)
            else cast(dict[str, Any], probes_cfg or {})
        )
        probe_runner = DefaultProbeRunner(
            probes_cfg_map, output_dir, seed=int(getattr(cfg, "seed", 0) or 0)
        )
        sim_engine.probe_runner = probe_runner

        # Optional interactive run control (play/pause/step). `none` returns
        # (None, None) and the loop runs uninterrupted; stdin/control_file attach a
        # gate the loop consults at each episode boundary.
        control_node = OmegaConf.select(cfg, "sim.engine.control")
        control_cfg = (
            OmegaConf.to_container(control_node, resolve=True)
            if isinstance(control_node, DictConfig)
            else control_node
        )
        step_gate, run_controller = build_run_control(cast(Any, control_cfg), output_dir=output_dir)
        if step_gate is not None:
            sim_engine.step_gate = step_gate
        if run_controller is not None:
            run_controller.start()

        # Purpose-built live signal: the loop emits step boundaries here and
        # observers tail this one file (see runtime/execution/run_events.py).
        # A fresh (non-resumed) run truncates a previous run's stale feed.
        run_events = RunEventLog(output_dir, fresh_start=start_step <= 0)
        sim_engine.run_event_log = run_events
        run_events.emit("status", status="running")

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

        # Provisional manifest: the run becomes loadable (Studio run page, live
        # Watch panels) while it executes. Status "running" tells consumers the
        # index is live — RunArtifact re-resolves event files on each load
        # instead of trusting this snapshot's artifact list — and the final
        # write in `finally` overwrites it with the completed record.
        provisional_snapshot = metrics.to_dict()
        write_run_manifest(
            output_dir=output_dir,
            status="running",
            meta=provisional_snapshot.get("meta"),
            counters=provisional_snapshot.get("counters"),
            game_masters=runtime_objects.game_masters_by_sequence(),
            project_root=_resolve_project_root(),
        )

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
        # Stop the interactive control thread (if any) before finalizing.
        if run_controller is not None:
            run_controller.close()
        # Finalize and write metrics
        metrics.mark_sim_end()
        # Stop the harness Model Proxy (if any) before summarizing so its usage
        # accumulators — already in `models` — are final and its thread is joined.
        if harness_proxy is not None:
            harness_proxy.stop()
        _record_llm_usage_summary(cfg, models=models, metrics=metrics)
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
        _warn_silent_backends(runtime_objects.game_masters_by_sequence(), logger)
        with open(run_stats_path, "a", encoding="utf-8") as f:
            f.write(completion_line + "\n")

        if run_events is not None:
            run_events.emit("status", status=completion_status, error=completion_error or None)
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
    return Path(output_dir)


def compose_config(
    *,
    scenario: str | Path | None = None,
    overrides: Sequence[str] = (),
) -> DictConfig:
    """Compose the experiment config without running it (programmatic API).

    ``scenario`` selects a scenario config directory exactly like the CLI's
    ``--config-path`` (a filesystem path, or a repository scenario name such
    as ``"misinformation"``); ``overrides`` are Hydra override strings
    (``["num_agents=4", "sim.llm.provider=scripted"]``). Pair the result with
    :func:`run_simulation`.
    """
    from hydra import compose, initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    from silisocs.runtime.configuration.external import set_external_config_dirs

    if GlobalHydra.instance().is_initialized():
        raise RuntimeError(
            "compose_config cannot run inside an active Hydra app — there the "
            "composed cfg already exists; pass it to run_simulation directly."
        )
    set_external_config_dirs([scenario] if scenario is not None else [])
    register_search_path_plugin()
    try:
        with initialize_config_dir(config_dir=str(CONF_DIR), version_base=None):
            cfg = compose(config_name="experiment", overrides=list(overrides))
        # Merge the scenario's flat group files NOW, re-applying the caller's
        # overrides on top (outside Hydra there are no task overrides for the
        # run-time merge to recover)...
        return merge_external_group_overrides(cfg, value_overrides=list(overrides))
    finally:
        # ...and clear the process-global selection either way, so a later
        # run_simulation (or another compose_config) can never merge THIS
        # call's scenario files into a different config.
        set_external_config_dirs([])


@hydra.main(version_base=None, config_path=str(CONF_DIR), config_name="experiment")
def main(cfg: DictConfig):
    """Hydra entrypoint for running an experiment (see :func:`run_simulation`)."""
    try:
        run_simulation(cfg)
    except ConfigValidationError as exc:
        if os.environ.get("HYDRA_FULL_ERROR") == "1":
            raise
        # Rejected user input, not a crash: Hydra would bury the (deliberately
        # actionable) message under a stack trace. SystemExit passes through
        # Hydra's error reporting untouched.
        print(str(exc), file=sys.stderr)
        sys.exit(1)


def _inject_external_config_path() -> None:
    """Run external config preprocessing before Hydra composes config."""
    inject_external_config_path()


def _register_search_path_plugin() -> None:
    """Register the Hydra search-path plugin."""
    register_search_path_plugin()


class _Subcommand(NamedTuple):
    """One ``silisocs <name>`` subcommand as the help screens describe it."""

    usage: str
    summary: str
    details: str
    # Name of the single optional positional argument, "" when the subcommand
    # takes flags instead (those are parsed by Typer, not here).
    positional: str = ""


_SUBCOMMANDS: dict[str, _Subcommand] = {
    "doctor": _Subcommand(
        usage="silisocs doctor [OUTPUT_DIR]",
        summary="environment health checks (no API key needed)",
        details=(
            "Reports Python/silisocs versions, which optional extras are installed,\n"
            "which provider credentials are set, and whether runs can be written to\n"
            "disk. OUTPUT_DIR is the directory to test for writability (default: a\n"
            "temporary directory). Exits non-zero when a required check fails."
        ),
        positional="OUTPUT_DIR",
    ),
    "tutorial": _Subcommand(
        usage="silisocs tutorial [OUTPUT_DIR]",
        summary="guided scripted demo run + artifact tour",
        details=(
            "Runs a small deterministic simulation on the scripted model provider (no\n"
            "API key needed), then walks through the artifacts it produced.\n"
            "OUTPUT_DIR is where the demo run is written\n"
            "(default: ./outputs/tutorial/<timestamp>)."
        ),
        positional="OUTPUT_DIR",
    ),
    "new-scenario": _Subcommand(
        usage="silisocs new-scenario --from-spec-json JSON [--output-dir DIR]",
        summary="scaffold a scenario config directory",
        details="Writes and validates scenarios/<name>/conf/ from a ScenarioSpec JSON blob.",
    ),
    "new-study": _Subcommand(
        usage="silisocs new-study --from-spec-json JSON [--output-dir DIR]",
        summary="scaffold a study directory",
        details="Writes and validates a study.yaml plan from a StudySpec JSON blob.",
    ),
}

_HELP_FLAGS = ("-h", "--help")


def _print_help() -> None:
    """Print the top-level help screen (subcommands + companion commands)."""
    subcommands = "\n".join(f"  {name:<13} {spec.summary}" for name, spec in _SUBCOMMANDS.items())
    print(
        f"""\
silisocs — native social simulation framework

Usage:
  silisocs [key=value ...]        run a simulation (Hydra config overrides)
  silisocs <subcommand> [args]    run a helper command

First run (no API key needed):
  silisocs tutorial

Subcommands:
{subcommands}
  help          print this screen

Companion commands installed alongside silisocs:
  silisocs-studio           visual workspace: build, launch, inspect, analyse runs
  silisocs-study            run and manage multi-run studies
  silisocs-report           render an analysis report from a finished run
  silisocs-config-dry-run   compose a config and report problems without running

Seeing the composed config:
  silisocs --cfg job [key=value ...]        print the fully composed config
  silisocs --config-path <dir> --cfg job    ...for an external scenario directory

Run `silisocs <subcommand> --help` for one subcommand, or `silisocs --hydra-help`
for Hydra's own flags (multirun, sweeps, launchers)."""
    )


def _print_subcommand_help(name: str) -> None:
    """Print usage + description for a single subcommand."""
    spec = _SUBCOMMANDS[name]
    headline = spec.summary[:1].upper() + spec.summary[1:]
    print(f"usage: {spec.usage}\n\n{headline}.\n\n{spec.details}")


def _optional_path_argument(name: str, args: Sequence[str]) -> str | None:
    """Return the lone optional positional for ``name``, rejecting flag-shaped input."""
    spec = _SUBCOMMANDS[name]
    if not args:
        return None
    if args[0].startswith("-"):
        raise SystemExit(
            f"silisocs {name}: unrecognised option {args[0]!r}; this subcommand takes "
            f"an optional {spec.positional} path.\nusage: {spec.usage}"
        )
    if len(args) > 1:
        raise SystemExit(
            f"silisocs {name}: unexpected extra argument {args[1]!r}.\nusage: {spec.usage}"
        )
    return args[0]


def cli_main() -> None:
    """CLI entry point: dispatch subcommands, else preprocess flags and run Hydra main."""
    args = sys.argv[1:]
    command = args[0] if args else ""
    rest = args[1:]

    # Hydra owns --help, which would otherwise hide everything below it.
    if command in (*_HELP_FLAGS, "help"):
        _print_help()
        return

    if command in ("doctor", "tutorial"):
        if any(arg in _HELP_FLAGS for arg in rest):
            _print_subcommand_help(command)
            return
        target = _optional_path_argument(command, rest)
        if command == "doctor":
            from silisocs.runtime.doctor import run_doctor

            sys.exit(run_doctor(target))
        from silisocs.runtime.tutorial import run_tutorial

        sys.exit(run_tutorial(target))

    if command in ("new-scenario", "new-study"):
        # Typer owns these subcommands' help (it lists their options); only the
        # short flag it does not accept is normalised here.
        sys.argv = [sys.argv[0], *("--help" if arg == "-h" else arg for arg in args)]
        from silisocs.scenario_gen.cli import scenario_gen_cli

        scenario_gen_cli()
        return

    _inject_external_config_path()
    _register_search_path_plugin()
    main()


if __name__ == "__main__":
    cli_main()
