"""Tests for the ``silisocs tutorial`` guided first-run command."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from silisocs.runtime.tutorial import TutorialBehavior, run_tutorial


class _FakeModel:
    def __init__(self, agent: str = "Alice", episode: int = 0) -> None:
        self.meta_data = {"agent_name": agent, "episode_idx": episode}


def _tool(name: str) -> dict[str, Any]:
    return {"function": {"name": name}}


def test_tutorial_behavior_posts_first_then_likes() -> None:
    behavior = TutorialBehavior()
    tools = [_tool("create_tweet"), _tool("like_tweet")]

    first = behavior.sample_tool_calls("choose an action", tools, model=_FakeModel(episode=0))
    assert [call.name for call in first] == ["create_tweet"]

    later = behavior.sample_tool_calls(
        "Timeline:\nTweet ID: 42 hello", tools, model=_FakeModel(episode=1)
    )
    assert [call.name for call in later] == ["like_tweet"]
    assert later[0].arguments == {"post_id": "42"}


def test_tutorial_reports_demo_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    def _failing_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=3, stdout="boom", stderr="")

    monkeypatch.setattr(subprocess, "run", _failing_run)
    assert run_tutorial(tmp_path / "demo") == 3
    output = capsys.readouterr().out
    assert "The demo run failed" in output
    assert "boom" in output


@pytest.mark.subprocess
def test_tutorial_end_to_end_produces_manifest_and_tour(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "demo"
    assert run_tutorial(output_dir) == 0

    assert (output_dir / "run_manifest.json").is_file()
    assert (output_dir / "action_events.jsonl").is_file()

    printed = capsys.readouterr().out
    assert "What the run produced" in printed
    assert "Next steps" in printed
    assert "post" in printed and "like" in printed
