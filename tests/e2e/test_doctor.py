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


def test_doctor_fails_on_unwritable_output(capsys, monkeypatch, tmp_path) -> None:
    def _raise(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _raise)
    assert doctor.run_doctor(tmp_path) == 1
    out = capsys.readouterr().out
    assert "not writable" in out and "required check(s) failed" in out
