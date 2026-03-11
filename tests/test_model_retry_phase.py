from mastodon_sim.utils.media import GptLanguageModel


def _make_model() -> GptLanguageModel:
    return GptLanguageModel(
        model_name="qwen3-4b",
        api_key="test-key",
        api_base="http://localhost:8000/v1",
        debug=False,
    )


def test_phase_counters_are_independent() -> None:
    model = _make_model()

    model.set_retry_phase("probe")
    for _ in range(5):
        model._record_retry_outcome(retries=2, success=True)

    model.set_retry_phase("action")
    for _ in range(3):
        model._record_retry_outcome(retries=1, success=False)

    probe = model.get_retry_counters(phase="probe")
    assert probe["calls_total"] == 5
    assert probe["retries_total"] == 10
    assert probe["failed_calls_total"] == 0

    action = model.get_retry_counters(phase="action")
    assert action["calls_total"] == 3
    assert action["retries_total"] == 3
    assert action["failed_calls_total"] == 3

    all_counters = model.get_retry_counters(phase="all")
    assert all_counters["calls_total"] == 8
    assert all_counters["retries_total"] == 13
    assert all_counters["failed_calls_total"] == 3
