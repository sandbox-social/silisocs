from silisocs.runtime.telemetry.engine_metrics import (
    _DEFAULT_COLD_START_CAP,
    compute_dynamic_worker_limit,
    update_adaptive_worker_cap,
)


def test_compute_dynamic_worker_limit_applies_phase_and_config_caps() -> None:
    dynamic, effective = compute_dynamic_worker_limit(
        requested_workers=200,
        phase_cap=120,
        configured_worker_cap=80,
    )
    assert dynamic == 120
    assert effective == 80


def test_compute_dynamic_worker_limit_cold_start_caps_without_config() -> None:
    """When no configured cap and no adaptive history, apply cold-start default."""
    dynamic, effective = compute_dynamic_worker_limit(
        requested_workers=5000,
        phase_cap=None,
        configured_worker_cap=None,
    )
    assert dynamic == 5000
    assert effective == _DEFAULT_COLD_START_CAP


def test_compute_dynamic_worker_limit_cold_start_caps_even_with_config() -> None:
    """When configured cap exists but no adaptive history, cold-start still applies."""
    dynamic, effective = compute_dynamic_worker_limit(
        requested_workers=5000,
        phase_cap=None,
        configured_worker_cap=1000,
    )
    assert dynamic == 5000
    # Cold-start (256) is lower than configured (1000), so it wins.
    assert effective == _DEFAULT_COLD_START_CAP


def test_compute_dynamic_worker_limit_cold_start_skipped_when_adaptive_exists() -> None:
    """When adaptive history exists (phase_cap set), cold-start should not apply."""
    dynamic, effective = compute_dynamic_worker_limit(
        requested_workers=5000,
        phase_cap=400,
        configured_worker_cap=None,
    )
    assert dynamic == 400
    assert effective == 400


def test_update_adaptive_worker_cap_reduces_on_high_retry_pressure() -> None:
    cap = update_adaptive_worker_cap(
        previous_cap=None,
        requested_workers=160,
        calls=120,
        retry_per_call=2.1,
        failure_ratio=0.0,
    )
    # retry_per_call >= 2.0 triggers 55% reduction: int(160 * 0.55) = 88
    assert cap == 88


def test_update_adaptive_worker_cap_recovers_when_stable() -> None:
    cap = update_adaptive_worker_cap(
        previous_cap=80,
        requested_workers=200,
        calls=120,
        retry_per_call=0.05,
        failure_ratio=0.0,
    )
    # headroom = 200 - 80 = 120, recovery = int(120 * 0.15) = 18 -> 80 + 18 = 98
    assert cap == 98


def test_update_adaptive_worker_cap_severe_pressure_triggers_aggressive_cut() -> None:
    cap = update_adaptive_worker_cap(
        previous_cap=500,
        requested_workers=1000,
        calls=500,
        retry_per_call=5.0,
        failure_ratio=0.0,
    )
    # retry_per_call >= 4.0 triggers 40% reduction: int(500 * 0.40) = 200
    assert cap == 200


def test_update_adaptive_worker_cap_massive_scale_throttles_to_absolute_floor() -> None:
    """Ensure 1M-agent runs can throttle down to a small absolute floor."""
    cap: int | None = 1_000_000
    for _ in range(20):
        assert cap is not None
        cap = update_adaptive_worker_cap(
            previous_cap=cap,
            requested_workers=1_000_000,
            calls=cap,
            retry_per_call=5.0,
            failure_ratio=0.15,
        )
    # After repeated severe pressure the cap must settle at the absolute floor (8),
    # NOT at requested_workers // 64 = 15625.
    assert cap == 8


def test_failed_turn_is_isolated_counted_and_reported() -> None:
    import concurrent.futures
    from types import SimpleNamespace

    from silisocs.runtime.telemetry import SimMetricsCollector
    from silisocs.simulation_engines.base_engines import RuntimeEngine

    SimMetricsCollector.reset()
    ran: list[str] = []
    failed: list[str] = []

    def _ok() -> None:
        ran.append("Alice")

    def _boom() -> str:
        raise RuntimeError("model exploded")

    # Drive the production drain primitive on a worker-limited pool: a failing turn
    # is isolated (siblings still run), counted, and reported in failed_tasks.
    engine = SimpleNamespace(_async_turns=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        RuntimeEngine._drain_tasks_on_pool(
            engine,  # type: ignore[arg-type]
            pool,
            {"gm::Alice": _ok, "gm::Bob": _boom},
            failed,
        )

    assert ran == ["Alice"]
    assert failed == ["gm::Bob"]
    assert SimMetricsCollector.get().counter("agent_turn_failures") == 1


def test_step_result_degraded_property() -> None:
    from silisocs.simulation_engines.runtime_base import StepResult

    assert not StepResult().degraded
    assert StepResult(failed_turns=("gm::Bob",)).degraded
