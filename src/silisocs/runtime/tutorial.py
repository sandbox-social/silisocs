"""``silisocs tutorial`` — a guided first run with no API key required.

Runs a small deterministic demo (scripted model, Twitter-like backend), then
uses the Run Artifact Module to tour the artifacts it produced and print the
next commands to try. The goal is install -> viewable run without reading
source or spending model budget.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

NUM_AGENTS = 3
NUM_STEPS = 3


class TutorialBehavior:
    """Scripted-model behavior for the demo: post first, then like what you see."""

    def sample_text(self, prompt: str, *, model: Any) -> str:
        agent = str(model.meta_data.get("agent_name", "agent") or "agent")
        episode = int(model.meta_data.get("episode_idx", -1) or -1)
        if "seed post" in prompt.lower():
            return f"{agent}: hello from the silisocs tutorial"
        return f"{agent} checking the timeline at step {episode}"

    def sample_tool_calls(
        self,
        prompt: str,
        tools: list[dict[str, Any]],
        *,
        model: Any,
    ) -> list[Any]:
        from silisocs.runtime.types import ToolCall

        tool_names = {_tool_name(spec) for spec in tools}
        tool_names.discard("")
        agent = str(model.meta_data.get("agent_name", "agent") or "agent")
        episode = int(model.meta_data.get("episode_idx", -1) or -1)
        tweet_ids = re.findall(r"Tweet ID:\s*([0-9]+)", prompt)
        if episode <= 0 and "create_tweet" in tool_names:
            return [ToolCall("create_tweet", {"status": f"{agent} joins the conversation"})]
        if "like_tweet" in tool_names and tweet_ids:
            return [ToolCall("like_tweet", {"post_id": tweet_ids[0]})]
        if "create_tweet" in tool_names:
            return [ToolCall("create_tweet", {"status": f"{agent} update at step {episode}"})]
        return [ToolCall(sorted(tool_names)[0], {})] if tool_names else []


def _tool_name(spec: dict[str, Any]) -> str:
    function = spec.get("function")
    if isinstance(function, dict):
        return str(function.get("name", "") or "").strip()
    return str(spec.get("name", "") or "").strip()


def _demo_overlay_sim() -> dict[str, Any]:
    return {
        "llm": {
            "provider": "scripted",
            "name": "scripted",
            "extra_kwargs": {
                "behavior_class_path": "silisocs.runtime.tutorial.TutorialBehavior",
            },
        },
        "tool_calling": {"mode": "multi"},
        "checkpoint": {"every_n_steps": 1},
        "engine": {
            "turn_policy": {"built_in": "single_action", "class_path": None, "params": {}},
            "participation": {"built_in": "all", "class_path": None, "params": {}},
        },
    }


def _demo_overrides(output_dir: Path, hydra_dir: Path) -> list[str]:
    return [
        "world=default",
        "agents=default",
        "env=twitter_like",
        f"num_agents={NUM_AGENTS}",
        f"num_steps={NUM_STEPS}",
        "seed=1",
        "scenario_name=tutorial",
        "env.gm.components.resolve.built_in=tool_calling",
        "env.gm.components.update.built_in=disabled",
        "env.gm.components.observe.params.timeline_mode=follower_chronological",
        "env.gm.components.initialize.params.graph.base_followership_probability=1.0",
        f"output_rootname={output_dir}",
        f"hydra.run.dir={hydra_dir}",
        "hydra.output_subdir=configs",
    ]


def _run_demo(output_dir: Path) -> int:
    """Run the scripted demo simulation as a subprocess; return its exit code."""
    with tempfile.TemporaryDirectory(prefix="silisocs-tutorial-") as overlay_root:
        overlay = Path(overlay_root) / "conf"
        overlay.mkdir()
        (overlay / "sim.yaml").write_text(
            yaml.safe_dump(_demo_overlay_sim(), sort_keys=False), encoding="utf-8"
        )
        command = [
            sys.executable,
            "-m",
            "silisocs.runtime.runner",
            "--overlay-config-path",
            str(overlay),
            *_demo_overrides(output_dir, Path(overlay_root) / "hydra"),
        ]
        result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        print("\nThe demo run failed. Last output lines:")
        tail = (result.stdout + "\n" + result.stderr).strip().splitlines()[-25:]
        for line in tail:
            print(f"  {line}")
    return result.returncode


def _print_artifact_tour(output_dir: Path) -> None:
    from silisocs.evaluations.run_artifact import load_run

    artifact = load_run(output_dir)
    print("\nWhat the run produced:")
    labeled = [
        ("run_manifest.json", "self-describing run index (status, layout, usage, health)"),
        ("action_events.jsonl", "every executed backend action, one JSON row per event"),
        ("sim_metrics.json", "run telemetry: timings, retries, token usage by phase"),
        ("prompts_and_responses.jsonl", "every model prompt/response with agent + step"),
        ("probe_events.jsonl", "probe answers (when probes are configured)"),
        ("checkpoints/", "per-step snapshots for resume/replay"),
    ]
    for name, description in labeled:
        path = output_dir / name
        if path.exists():
            print(f"  {name:<30} {description}")

    actions = Counter(str(row.get("label", "")) for row in artifact.iter_actions())
    if actions:
        summary = ", ".join(f"{count}x {label}" for label, count in actions.most_common())
        print(f"\nActions taken by the {NUM_AGENTS} agents over {NUM_STEPS} steps: {summary}")
    if artifact.status:
        print(f"Run status (from run_manifest.json): {artifact.status}")


def _print_next_steps(output_dir: Path) -> None:
    print("\nNext steps:")
    print(f"  - browse the results app:   silisocs-studio --output-root {output_dir.parent}")
    print("  - build your own scenario:  silisocs-studio")
    print("  - run with a real model:    silisocs sim.llm.provider=openai sim.llm.name=gpt-4o-mini")
    print("  - check your environment:   silisocs doctor")
    print("  - load runs in Python:      from silisocs.evaluations.run_artifact import load_run")
    print("\nDocs: https://sandbox-social.github.io/silisocs/ (usage, configuration, glossary).")


def run_tutorial(output_dir: str | Path | None = None) -> int:
    """Run the guided demo; return 0 on success."""
    target = Path(output_dir) if output_dir else Path.cwd() / "outputs" / "tutorial"
    target = target if output_dir else target / time.strftime("%Y-%m-%d_%H-%M-%S")

    print("silisocs tutorial\n")
    print(
        f"Running a deterministic demo: {NUM_AGENTS} agents x {NUM_STEPS} steps on the "
        "Twitter-like backend,\nusing the scripted model provider (no API key needed)."
    )
    print(f"Output directory: {target}\n")

    started = time.time()
    returncode = _run_demo(target)
    if returncode != 0:
        return returncode
    print(f"Demo finished in {time.time() - started:.1f}s.")

    _print_artifact_tour(target)
    _print_next_steps(target)
    return 0
