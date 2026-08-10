"""Tests for the ``silisocs doctor`` environment health check."""

from __future__ import annotations

import tempfile

from silisocs.runtime import doctor


def test_doctor_passes_in_dev_env(capsys, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert doctor.run_doctor() == 0
    out = capsys.readouterr().out
    assert "Core:" in out and "Optional extras:" in out
    assert "OPENAI_API_KEY set" in out
    assert "sk-test" not in out  # values are never printed
    assert "All required checks passed." in out


def test_doctor_reports_missing_extras_with_install_hint(capsys, monkeypatch) -> None:
    monkeypatch.setattr(doctor, "_importable", lambda name: False)
    assert doctor.run_doctor() == 0  # missing extras warn, never fail
    out = capsys.readouterr().out
    assert 'pip install "silisocs[studio]"' in out
    assert 'pip install "silisocs[analysis]"' in out


def test_doctor_recommends_scripted_not_the_no_op_model(capsys, monkeypatch) -> None:
    """The keyless hint must name a provider that can actually run a turn.

    ``sim.llm.disabled`` builds the no-op model, which answers a tool-call spec
    with an empty list — under the packaged ``sim.tool_calling.mode: single``
    that failed 100% of agent turns, and now fails at build. ``scripted`` is the
    offline provider that does answer tool calls.
    """
    for env_name in set(doctor._provider_key_envs().values()):
        monkeypatch.delenv(env_name, raising=False)

    assert doctor.run_doctor() == 0  # a missing key warns, never fails
    out = capsys.readouterr().out
    assert "no provider key set" in out
    assert "sim.llm.provider=scripted" in out
    assert "sim.llm.disabled" not in out


def test_doctor_fails_on_unwritable_output(capsys, monkeypatch, tmp_path) -> None:
    def _raise(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _raise)
    assert doctor.run_doctor(tmp_path) == 1
    out = capsys.readouterr().out
    assert "not writable" in out and "required check(s) failed" in out
