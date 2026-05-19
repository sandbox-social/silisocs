"""Telemetry and worker-planning helpers for the social simulation engine."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from omegaconf import OmegaConf

from silisocs.agents.base_agent import Agent


def resolve_configured_worker_cap(cfg: Any) -> int | None:
    """Read optional worker cap from config. `null` keeps existing behavior."""
    raw_cap = OmegaConf.select(cfg, "sim.max_concurrent_actions")
    if raw_cap is None:
        return None

    try:
        cap = int(raw_cap)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"sim.max_concurrent_actions must be an integer or null, got: {raw_cap!r}"
        ) from exc

    if cap <= 0:
        raise ValueError(f"sim.max_concurrent_actions must be > 0, got: {cap}")
    return cap


def collect_unique_models(
    game_master: Agent,
    entities_to_process: Sequence[Agent] | None = None,
) -> list[Any]:
    """Collect unique model objects used by the game master and active entities."""
    candidate_models: list[Any] = []
    gm_model = getattr(game_master, "model", None)
    if gm_model is not None:
        candidate_models.append(gm_model)

    if entities_to_process:
        for entity in entities_to_process:
            entity_model = getattr(entity, "model", None)
            if entity_model is not None:
                candidate_models.append(entity_model)

    return list({id(model): model for model in candidate_models}.values())


def set_model_retry_phase(models: Sequence[Any], phase: str) -> None:
    """Set retry accounting phase for model wrappers that support it."""
    for model in models:
        setter = getattr(model, "set_retry_phase", None)
        if not callable(setter):
            continue
        try:
            setter(phase)
        except Exception:
            continue


# Sensible default when no configured cap and no prior adaptive history.
# Prevents cold-start overload on local inference servers.
_DEFAULT_COLD_START_CAP = 256


def compute_dynamic_worker_limit(
    requested_workers: int,
    phase_cap: int | None,
    configured_worker_cap: int | None,
) -> tuple[int, int]:
    """Compute dynamic and effective worker caps for the current phase.

    When no prior adaptive cap exists (cold start), applies a conservative
    default to avoid overwhelming backend servers — even when a configured cap
    is present, since the configured cap may still be too high for the backend
    to handle on the very first episode.
    """
    if requested_workers <= 0:
        return 0, 0

    dynamic_cap = requested_workers
    if phase_cap is not None:
        dynamic_cap = min(dynamic_cap, max(1, int(phase_cap)))

    effective_cap = dynamic_cap
    if configured_worker_cap is not None:
        effective_cap = min(effective_cap, configured_worker_cap)
    if phase_cap is None:
        # Cold start: no adaptive history yet.  Apply conservative ceiling
        # regardless of whether a configured cap exists.
        effective_cap = min(effective_cap, _DEFAULT_COLD_START_CAP)

    return dynamic_cap, max(1, effective_cap)


def update_adaptive_worker_cap(
    previous_cap: int | None,
    requested_workers: int,
    calls: int,
    retry_per_call: float,
    failure_ratio: float,
) -> int | None:
    """Update a cross-episode worker cap using observed retry pressure.

    Uses a proportional controller that scales reduction smoothly with
    retry pressure and recovers gradually when pressure subsides.  The goal
    is to converge on the highest throughput the backend can sustain.
    """
    if requested_workers <= 0:
        return previous_cap
    if calls <= 0:
        return previous_cap

    # Absolute floor – must not scale with agent count so that even
    # 1M-agent runs can throttle down to a safe level under heavy load.
    _MIN_CAP_FLOOR = 8
    min_cap = _MIN_CAP_FLOOR
    cap = min(previous_cap or requested_workers, requested_workers)

    # --- reduction: proportional to severity ---
    if failure_ratio >= 0.10 or retry_per_call >= 4.0:
        cap = max(min_cap, int(cap * 0.40))
    elif failure_ratio >= 0.05 or retry_per_call >= 2.0:
        cap = max(min_cap, int(cap * 0.55))
    elif retry_per_call >= 1.0:
        cap = max(min_cap, int(cap * 0.70))
    elif retry_per_call >= 0.5:
        cap = max(min_cap, int(cap * 0.85))
    # --- recovery: gentle ramp when pressure is low ---
    elif retry_per_call <= 0.25 and failure_ratio <= 0.01:
        headroom = requested_workers - cap
        if headroom > 0:
            recovery = max(1, int(headroom * 0.15))
            cap = min(requested_workers, cap + recovery)

    return max(1, min(cap, requested_workers))


def _read_retry_counters(model: Any, phase: str = "all") -> dict[str, int]:
    """Read cumulative retry counters from model."""
    counters_getter = getattr(model, "get_retry_counters", None)
    if callable(counters_getter):
        try:
            counters = counters_getter(phase=phase)
        except TypeError:
            counters = counters_getter()
        return {
            "calls_total": int(counters.get("calls_total", 0)),
            "failed_calls_total": int(counters.get("failed_calls_total", 0)),
            "retries_total": int(counters.get("retries_total", 0)),
        }
    return {"calls_total": 0, "failed_calls_total": 0, "retries_total": 0}


def capture_retry_counters(models: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Capture cumulative retry counters for differential phase telemetry."""
    snapshot: dict[str, dict[str, Any]] = {}
    for model in models:
        model_key = str(id(model))
        snapshot[model_key] = {
            "model": str(getattr(model, "_model_name", model.__class__.__name__)),
            **_read_retry_counters(model),
        }
    return snapshot


def summarize_retry_delta(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize retry counter deltas between two telemetry snapshots."""
    calls = 0
    failed_calls = 0
    retries = 0
    models_with_activity = 0

    all_keys = set(before.keys()) | set(after.keys())
    for model_key in all_keys:
        b = before.get(model_key, {})
        a = after.get(model_key, {})
        dcalls = int(a.get("calls_total", 0)) - int(b.get("calls_total", 0))
        dfail = int(a.get("failed_calls_total", 0)) - int(b.get("failed_calls_total", 0))
        dretries = int(a.get("retries_total", 0)) - int(b.get("retries_total", 0))
        if dcalls > 0:
            models_with_activity += 1
        calls += max(0, dcalls)
        failed_calls += max(0, dfail)
        retries += max(0, dretries)

    retry_per_call = (retries / calls) if calls else 0.0
    failure_ratio = (failed_calls / calls) if calls else 0.0
    return {
        "calls": calls,
        "failed_calls": failed_calls,
        "retries": retries,
        "retry_per_call": round(retry_per_call, 4),
        "failure_ratio": round(failure_ratio, 4),
        "models_with_activity": models_with_activity,
    }


def collect_retry_telemetry(
    models: Sequence[Any],
    requested_workers: int,
    phase: str = "all",
) -> dict[str, Any]:
    """Summarize retry pressure exposed by language model wrappers."""
    per_model: list[dict[str, Any]] = []
    calls_total = 0
    failed_calls_total = 0
    retries_total = 0
    retry_samples = 0
    retry_sum = 0

    for model in models:
        counters = _read_retry_counters(model, phase=phase)
        model_calls = counters["calls_total"]
        model_failed = counters["failed_calls_total"]
        model_retries = counters["retries_total"]
        model_retry_per_call = (model_retries / model_calls) if model_calls else 0.0
        model_failure_ratio = (model_failed / model_calls) if model_calls else 0.0

        # Use the rolling window for the "samples/avg" view when available.
        retry_hist = list(getattr(model, "_retry_history", []))
        retry_len = len(retry_hist)
        retry_avg = (sum(retry_hist) / retry_len) if retry_len else 0.0

        per_model.append(
            {
                "model": str(getattr(model, "_model_name", model.__class__.__name__)),
                "retry_samples": retry_len,
                "retry_avg": round(retry_avg, 4),
                "failure_ratio": round(model_failure_ratio, 4),
                "max_retries": getattr(model, "_max_retries", None),
                "calls_total": model_calls,
                "failed_calls_total": model_failed,
                "retries_total": model_retries,
                "retry_per_call": round(model_retry_per_call, 4),
            }
        )

        retry_samples += retry_len
        retry_sum += sum(retry_hist)
        calls_total += model_calls
        failed_calls_total += model_failed
        retries_total += model_retries

    retry_avg_global = (retry_sum / retry_samples) if retry_samples else 0.0
    failure_ratio_global = (failed_calls_total / calls_total) if calls_total else 0.0
    retry_per_call = (retries_total / calls_total) if calls_total else 0.0
    return {
        "model_count": len(models),
        "retry_samples": retry_samples,
        "retry_avg": round(retry_avg_global, 4),
        "failure_ratio": round(failure_ratio_global, 4),
        "per_model": per_model,
        "calls_total": calls_total,
        "failed_calls_total": failed_calls_total,
        "retries_total": retries_total,
        "retry_per_call": round(retry_per_call, 4),
    }


def append_episode_run_stats(
    output_rootname: str,
    episode: int,
    duration_s: float,
    requested_workers: int,
    dynamic_worker_cap: int,
    configured_worker_cap: int | None,
    worker_limit: int,
    probe_dynamic_worker_cap: int,
    probe_worker_limit: int,
    retry_telemetry: Mapping[str, Any],
    phase_timings: Mapping[str, float],
    probe_phase: Mapping[str, Any],
    action_phase: Mapping[str, Any],
) -> None:
    """Append structured per-episode lines to run_stats.log."""
    stats_path = os.path.join(output_rootname, "run_stats.log")
    with open(stats_path, "a", encoding="utf-8") as f:
        f.write(f"Episode {episode} duration: {duration_s:.2f}s\n")
        f.write(
            f"Episode {episode} workers: requested={requested_workers}, "
            f"dynamic_cap={dynamic_worker_cap}, configured_cap={configured_worker_cap}, "
            f"effective={worker_limit}\n"
        )
        f.write(
            f"Episode {episode} probe_phase: deployed={probe_phase.get('deployed')}, "
            f"selected_agents={probe_phase.get('selected_agents')}, "
            f"dynamic_cap={probe_dynamic_worker_cap}, "
            f"effective_workers={probe_worker_limit}, "
            f"duration_s={probe_phase.get('duration_s')}, "
            f"calls={probe_phase.get('retry', {}).get('calls')}, "
            f"retries={probe_phase.get('retry', {}).get('retries')}, "
            f"retry_per_call={probe_phase.get('retry', {}).get('retry_per_call')}, "
            f"failures={probe_phase.get('retry', {}).get('failed_calls')}\n"
        )
        f.write(
            f"Episode {episode} action_phase: active_agents={action_phase.get('active_agents')}, "
            f"duration_s={action_phase.get('duration_s')}, "
            f"calls={action_phase.get('retry', {}).get('calls')}, "
            f"retries={action_phase.get('retry', {}).get('retries')}, "
            f"retry_per_call={action_phase.get('retry', {}).get('retry_per_call')}, "
            f"failures={action_phase.get('retry', {}).get('failed_calls')}\n"
        )
        f.write(
            f"Episode {episode} retry: models={retry_telemetry.get('model_count')}, "
            f"retry_samples={retry_telemetry.get('retry_samples')}, "
            f"retry_avg={retry_telemetry.get('retry_avg')}, "
            f"failure_ratio={retry_telemetry.get('failure_ratio')}, "
            f"retry_per_call={retry_telemetry.get('retry_per_call')}\n"
        )
        phase_summary = ", ".join(
            f"{phase}={elapsed:.3f}s" for phase, elapsed in sorted(phase_timings.items())
        )
        f.write(f"Episode {episode} timings: {phase_summary}\n")
