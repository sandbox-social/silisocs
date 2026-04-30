"""Runtime telemetry helpers for simulation engine instrumentation."""

from silisocs.runtime.telemetry.engine_metrics import (
    append_episode_run_stats,
    capture_retry_counters,
    collect_retry_telemetry,
    collect_unique_models,
    compute_dynamic_worker_limit,
    resolve_configured_worker_cap,
    set_model_retry_phase,
    summarize_retry_delta,
    update_adaptive_worker_cap,
)

__all__ = [
    "append_episode_run_stats",
    "capture_retry_counters",
    "collect_retry_telemetry",
    "collect_unique_models",
    "compute_dynamic_worker_limit",
    "resolve_configured_worker_cap",
    "set_model_retry_phase",
    "summarize_retry_delta",
    "update_adaptive_worker_cap",
]
