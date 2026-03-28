"""Simulation-backed LLM E2E contract tests.

These tests run short real simulations against an OpenAI-compatible LLM server
and validate runtime artifacts (action events, prompt logs, run stats, DB files)
plus key behavior contracts.

Run only when an LLM server is available:
    export LLM_SERVER_URL=http://localhost:30000/v1
    uv run pytest -m llm_e2e tests/test_e2e_multi_gm_llm.py -v -s
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _llm_server_url() -> str:
    url = os.getenv("LLM_SERVER_URL", "").strip()
    if not url:
        pytest.skip("LLM_SERVER_URL not set")
    return url


def _write_scenario(conf_dir: Path, scenario_name: str) -> None:
    scenario_dir = conf_dir / "scenario"
    scenario_dir.mkdir(parents=True, exist_ok=True)

    scenario_yaml = textwrap.dedent(
        f"""
        # @package scenario
        scenario_name: {scenario_name}
        jobname_format: "{scenario_name}_${{sim.num_steps}}"

        setting:
          name: LLM E2E Contract Test
          background:
            - Compact deterministic scenario for artifact and behavior validation.

        event:
          name: Contract validation run
          context: Validate timeline observation and action resolution contracts.

        persona_pipeline:
          processing_mode: raw
          defaults:
            params:
              scenario_context: Agents act in a compact social simulation.
              seed_post: seeded default post
          classes:
            fixed_seed:
              count: 1
              prefab_module: mastodon_sim.agents.fixed_entity
              sim_role_name: fixed_seed
              flow_tag: fixed_pre
              params:
                name: SeedBot
                context: Deterministic fixed seed entity.
                seed_post: SeedBot seed post
                action_output_mode: parsed_action
                advance_without_episode_observation: true
                emit_finished_on_episode_end: true
                fixed_action_plan:
                  0:
                    - action_type: post
                      content: SeedBot episode zero broadcast
                  1:
                    - action_type: post
                      content: SeedBot episode one broadcast
            llm_user:
              count: 2
              prefab_module: mastodon_sim.agents.entity
              sim_role_name: llm_user
              data:
                source: inline
                records:
                  - name: Alice Analyst
                    persona: Analytical user who responds calmly and briefly.
                    seed_post: Alice seed post
                  - name: Bob Builder
                    persona: Builder user who posts practical updates.
                    seed_post: Bob seed post
              field_map:
                name: name
                context: persona
                seed_post: seed_post

        social_network:
          activity_transition_rates:
            fixed_seed:
              inactive_to_active: 1.0
              active_to_inactive: 0.0
            llm_user:
              inactive_to_active: 1.0
              active_to_inactive: 0.0
          fully_connected_targets: []
          base_followership_probability: 1.0
          network_type: random

        shared_memories:
          - Agents are validating runtime contracts.

        initial_observations:
          - "{{name}} starts this contract test run."

        probes: {{}}
        """
    ).strip()

    (scenario_dir / f"{scenario_name}.yaml").write_text(scenario_yaml + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    assert payloads, f"Expected non-empty JSONL at {path}"
    return payloads


def _run_simulation(
    *,
    tmp_path: Path,
    scenario_name: str,
    backend: str,
    engine_preset: str,
    timeline_mode: str,
    recsys_type: str,
    resolve_built_in: str = "parsed_action",
    action_mode: str = "custom",
    tool_calling_mode: str | None = None,
    num_steps: int = 3,
) -> Path:
    conf_dir = tmp_path / "conf"
    _write_scenario(conf_dir, scenario_name)

    llm_url = _llm_server_url()
    hydra_dir = tmp_path / "hydra"
    job_name = f"{scenario_name}_{backend}_{engine_preset}"
    resolved_tool_mode = tool_calling_mode
    if resolved_tool_mode is None:
        resolved_tool_mode = "single" if resolve_built_in == "tool_calling" else "none"

    cmd = [
        sys.executable,
        "-m",
        "mastodon_sim.runtime.runner",
        "--config-path",
        str(conf_dir),
        f"scenario={scenario_name}",
        f"social_media={backend}",
        "sim.gm.preset=base",
        "sim.enable_gm_multi_flow=false",
        f"sim.engine.preset={engine_preset}",
        f"sim.enable_engine_multi_flow={'true' if engine_preset == 'flow' else 'false'}",
        "sim.engine.action_loop.built_in=single_action",
        "sim.gm.components.next_acting.built_in=all_entities",
        f"sim.gm.components.resolve.built_in={resolve_built_in}",
        f"sim.tool_calling.mode={resolved_tool_mode}",
        f"sim.timeline_mode={timeline_mode}",
        "sim.timeline_posts=5",
        f"sim.gm.components.recommend.params.default_recsys_type={recsys_type}",
        f"sim.gm.components.observe.params.recsys_type={recsys_type}",
        f"sim.action_mode={action_mode}",
        "sim.memory_backend=list",
        f"sim.num_steps={num_steps}",
        "sim.seed=11",
        "sim.write_html_log=false",
        "sim.max_concurrent_actions=8",
        "sim.llm_name=qwen3.5-4b",
        f"sim.llm_api_base={llm_url}",
        "sim.llm_api_key=test-key",
        "sim.engine.flow_routing.flow_order=[fixed_pre,default]",
        f"hydra.run.dir={hydra_dir}",
        f"hydra.job.name={job_name}",
        "experiment_name=llm_e2e_test",
    ]

    env = dict(os.environ)
    env.setdefault("OPENAI_API_KEY", "test-key")

    result = subprocess.run(
        cmd,
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=420,
    )
    if result.returncode != 0:
        pytest.fail(
            "Simulation run failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    output_dir = hydra_dir / job_name
    assert output_dir.exists(), f"Expected output directory: {output_dir}"
    return output_dir


def _assert_common_artifacts(
    output_dir: Path, backend: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    action_events_file = output_dir / "action_events.jsonl"
    prompt_file = output_dir / "prompts_and_responses.jsonl"
    run_stats_file = output_dir / "run_stats.log"
    db_file = output_dir / f"{backend}.db"

    assert action_events_file.exists()
    assert prompt_file.exists()
    assert run_stats_file.exists()
    assert db_file.exists()

    action_events = _read_jsonl(action_events_file)
    prompts = _read_jsonl(prompt_file)

    stats_text = run_stats_file.read_text(encoding="utf-8")
    assert stats_text.strip()

    for event in action_events:
        assert "episode" in event
        assert "event_type" in event
        assert "event_index" in event
        assert event["event_type"] == "action"

    for item in prompts:
        assert "prompt" in item
        assert "output" in item
        assert "episode_idx" in item

    return action_events, prompts


def _assert_seed_posted(action_events: list[dict[str, Any]]) -> None:
    initialize_events = [event for event in action_events if event.get("label") == "initialize"]
    assert initialize_events
    max_seed_count = max(
        int(event.get("data", {}).get("num_seed_posts", 0)) for event in initialize_events
    )
    assert max_seed_count >= 1


def _assert_seedbot_flow_precedence(action_events: list[dict[str, Any]]) -> None:
    episode_values: set[int] = set()
    for event in action_events:
        raw_episode = event.get("episode")
        if raw_episode is None:
            continue
        try:
            episode_values.add(int(raw_episode))
        except (TypeError, ValueError):
            continue

    episodes = sorted(episode_values)

    def _check_precedence(include_init_events: bool) -> int:
        checked_local = 0
        for episode in episodes:
            episode_events = [
                event
                for event in action_events
                if event.get("episode") == episode
                and event.get("source_user") in {"SeedBot", "Alice Analyst", "Bob Builder"}
                and (include_init_events or not str(event.get("label", "")).startswith("init_"))
            ]
            seed_events = [
                event for event in episode_events if event.get("source_user") == "SeedBot"
            ]
            llm_events = [
                event for event in episode_events if event.get("source_user") != "SeedBot"
            ]
            if not seed_events or not llm_events:
                continue

            checked_local += 1
            assert min(event["event_index"] for event in seed_events) < min(
                event["event_index"] for event in llm_events
            )
        return checked_local

    checked = _check_precedence(include_init_events=False)
    if checked == 0:
        checked = _check_precedence(include_init_events=True)

    assert checked >= 1


def _assert_prompt_contract(prompts: list[dict[str, Any]], backend: str) -> None:
    all_prompts = "\n".join(str(item.get("prompt", "")) for item in prompts)
    all_outputs = "\n".join(str(item.get("output", "")) for item in prompts)

    assert (
        "FINAL DECISION" in all_prompts
        or "## AVAILABLE ACTIONS" in all_prompts
        or "Exercise:" in all_prompts
    )
    assert all_outputs.strip()

    if backend == "twitter_like":
        assert "Tweet ID:" in all_prompts or "Timeline" in all_prompts
    if backend == "reddit_like":
        assert "Post ID:" in all_prompts or "Home Feed" in all_prompts


def _assert_non_init_actions_logged(action_events: list[dict[str, Any]]) -> None:
    non_init_events = [
        event
        for event in action_events
        if not str(event.get("label", "")).startswith("init_")
        and str(event.get("label", "")) != "initialize"
    ]
    assert non_init_events, "Expected at least one non-init action event to be logged"


def _assert_tool_call_outputs_present(prompts: list[dict[str, Any]]) -> None:
    tool_call_outputs = [
        item
        for item in prompts
        if int(item.get("episode_idx", -1)) >= 1
        and "tool_call" in str(item.get("output", "")).lower()
    ]
    assert tool_call_outputs, "Expected at least one tool_call output in action episodes"


def _assert_multi_tool_call_outputs_present(prompts: list[dict[str, Any]]) -> None:
    multi_tool_outputs = [
        item
        for item in prompts
        if int(item.get("episode_idx", -1)) >= 1
        and "tool_calls" in str(item.get("output", "")).lower()
    ]
    assert multi_tool_outputs, "Expected at least one tool_calls output in action episodes"

    tool_names: set[str] = set()
    saw_multi_in_single_output = False

    for item in multi_tool_outputs:
        output_text = str(item.get("output", "")).strip()

        try:
            parsed = json.loads(output_text)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None

        if isinstance(parsed, dict) and isinstance(parsed.get("tool_calls"), list):
            calls = [call for call in parsed["tool_calls"] if isinstance(call, dict)]
            if len(calls) >= 2:
                saw_multi_in_single_output = True
            for call in calls:
                name = str(call.get("name", "")).strip()
                if name:
                    tool_names.add(name)

        matches = re.findall(r"tool_calls\s*:\s*([a-zA-Z_][\w]*)\s*\(", output_text)
        if matches:
            tool_names.update(matches)
            if len(matches) >= 2:
                saw_multi_in_single_output = True

    assert tool_names, "Expected parseable tool_calls outputs with tool names"
    assert saw_multi_in_single_output or len(multi_tool_outputs) >= 2


def _assert_three_agent_activity(action_events: list[dict[str, Any]]) -> None:
    actor_names = {
        str(event.get("source_user", "")).strip()
        for event in action_events
        if str(event.get("source_user", "")).strip()
        and str(event.get("source_user", "")).strip().lower() != "system"
    }
    expected = {"SeedBot", "Alice Analyst", "Bob Builder"}
    assert expected.issubset(actor_names), f"Expected actor names {expected}, got {actor_names}"


@pytest.mark.llm_e2e
@pytest.mark.skipif(not os.getenv("LLM_SERVER_URL"), reason="LLM_SERVER_URL not set")
def test_llm_e2e_base_gm_flow_engine_twitter_hybrid_twhin_contracts(tmp_path: Path) -> None:
    output_dir = _run_simulation(
        tmp_path=tmp_path,
        scenario_name="llm_e2e_flow_twitter",
        backend="twitter_like",
        engine_preset="flow",
        timeline_mode="hybrid_recsys_follower",
        recsys_type="twhin",
    )

    action_events, prompts = _assert_common_artifacts(output_dir, "twitter_like")
    _assert_seed_posted(action_events)
    _assert_seedbot_flow_precedence(action_events)
    _assert_prompt_contract(prompts, backend="twitter_like")


@pytest.mark.llm_e2e
@pytest.mark.skipif(not os.getenv("LLM_SERVER_URL"), reason="LLM_SERVER_URL not set")
def test_llm_e2e_base_gm_flow_engine_reddit_default_timeline_contracts(tmp_path: Path) -> None:
    output_dir = _run_simulation(
        tmp_path=tmp_path,
        scenario_name="llm_e2e_flow_reddit",
        backend="reddit_like",
        engine_preset="flow",
        timeline_mode="follower_chronological",
        recsys_type="reddit",
    )

    action_events, prompts = _assert_common_artifacts(output_dir, "reddit_like")
    _assert_seed_posted(action_events)
    _assert_seedbot_flow_precedence(action_events)
    _assert_prompt_contract(prompts, backend="reddit_like")


@pytest.mark.llm_e2e
@pytest.mark.skipif(not os.getenv("LLM_SERVER_URL"), reason="LLM_SERVER_URL not set")
def test_llm_e2e_base_gm_base_engine_no_flow_contracts(tmp_path: Path) -> None:
    output_dir = _run_simulation(
        tmp_path=tmp_path,
        scenario_name="llm_e2e_base_twitter",
        backend="twitter_like",
        engine_preset="base",
        timeline_mode="follower_chronological",
        recsys_type="twitter",
    )

    action_events, prompts = _assert_common_artifacts(output_dir, "twitter_like")
    _assert_seed_posted(action_events)
    _assert_prompt_contract(prompts, backend="twitter_like")

    # Base engine test intentionally validates behavior without flow precedence assertions.
    non_system_events = [
        event for event in action_events if str(event.get("source_user", "")).lower() != "system"
    ]
    assert non_system_events


@pytest.mark.llm_e2e
@pytest.mark.skipif(not os.getenv("LLM_SERVER_URL"), reason="LLM_SERVER_URL not set")
def test_llm_e2e_tool_calling_logs_post_init_actions(tmp_path: Path) -> None:
    output_dir = _run_simulation(
        tmp_path=tmp_path,
        scenario_name="llm_e2e_tool_calling_twitter",
        backend="twitter_like",
        engine_preset="flow",
        timeline_mode="follower_chronological",
        recsys_type="twitter",
        resolve_built_in="tool_calling",
        action_mode="custom",
        num_steps=4,
    )

    action_events, prompts = _assert_common_artifacts(output_dir, "twitter_like")
    _assert_seed_posted(action_events)
    _assert_prompt_contract(prompts, backend="twitter_like")
    _assert_tool_call_outputs_present(prompts)
    _assert_non_init_actions_logged(action_events)


@pytest.mark.llm_e2e
@pytest.mark.skipif(not os.getenv("LLM_SERVER_URL"), reason="LLM_SERVER_URL not set")
def test_llm_e2e_multi_tool_calling_three_agents_three_steps(tmp_path: Path) -> None:
    output_dir = _run_simulation(
        tmp_path=tmp_path,
        scenario_name="llm_e2e_tool_calling_multi_twitter",
        backend="twitter_like",
        engine_preset="flow",
        timeline_mode="follower_chronological",
        recsys_type="twitter",
        resolve_built_in="tool_calling",
        action_mode="custom",
        tool_calling_mode="multi",
        num_steps=3,
    )

    action_events, prompts = _assert_common_artifacts(output_dir, "twitter_like")
    _assert_seed_posted(action_events)
    _assert_prompt_contract(prompts, backend="twitter_like")
    _assert_multi_tool_call_outputs_present(prompts)
    _assert_non_init_actions_logged(action_events)
    _assert_three_agent_activity(action_events)
