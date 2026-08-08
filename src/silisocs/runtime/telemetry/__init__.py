"""Runtime telemetry helpers for simulation engine instrumentation."""

from typing import Any

from silisocs.runtime.telemetry.collector import SimMetricsCollector

# engine_metrics imports omegaconf at module scope; importing it here eagerly
# would drag omegaconf into every consumer of the light collector (notably the
# Studio bind path via backends/base.py). PEP 562 keeps the re-exports working
# while deferring that cost to first use.
__all__ = [
    "SimMetricsCollector",
    "append_episode_run_stats",
    "capture_retry_counters",
    "collect_retry_telemetry",
    "collect_unique_models",
    "collect_usage_summary",
    "compute_dynamic_worker_limit",
    "resolve_configured_worker_cap",
    "set_model_retry_phase",
    "summarize_retry_delta",
    "update_adaptive_worker_cap",
]

_ENGINE_METRIC_NAMES = frozenset(__all__) - {"SimMetricsCollector"}


def __getattr__(name: str) -> Any:
    if name in _ENGINE_METRIC_NAMES:
        from silisocs.runtime.telemetry import engine_metrics  # noqa: PLC0415

        return getattr(engine_metrics, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
