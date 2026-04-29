"""LLM E2E tests for recommendation generation and retrieval in pure recsys mode.

These tests run short real simulations against an OpenAI-compatible endpoint
(assumed qwen3.5-4b on localhost:30000) and fail fast when:
1) recommendation rows are not generated, or
2) pure_recsys retrieval never surfaces timeline posts in observations.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _llm_server_url() -> str:
    # Default to the requested local qwen endpoint; allow override.
    return os.getenv("LLM_SERVER_URL", "http://localhost:30000/v1").strip()


def _write_scenario(conf_dir: Path, scenario_name: str) -> None:
    conf_dir.mkdir(parents=True, exist_ok=True)

    sim_yaml = textwrap.dedent(
        f"""
        # @package sim
        scenario_name: {scenario_name}
        jobname_format: "{scenario_name}_${{sim.num_steps}}"

        setting:
          name: Recsys E2E Setting
          background:
            - Compact scenario to validate recommendation generation and retrieval.

        event:
          name: Recsys E2E
          context: Validate pure recsys timeline retrieval with seeded startup posts.

        data: {{}}
        candidates: {{}}
        news_account: {{}}
        partisan_types: []
        """
    ).strip()

    agent_yaml = textwrap.dedent(
        """
        # @package agent

        persona_pipeline:
          processing_mode: raw
          defaults:
            params:
              scenario_context: Agents discuss local election updates.
              seed_post: default seed post
          classes:
            fixed_seed:
              count: 1
              prefab_module: silisocs.agents.fixed_entity
              sim_role_name: fixed_seed
              flow_tag: fixed_pre
              params:
                name: SeedBot
                context: deterministic seed broadcaster
                action_output_mode: parsed_action
                advance_without_episode_observation: true
                emit_finished_on_episode_end: true
                fixed_action_plan:
                  0:
                    - {action_type: post, content: "SeedBot update episode zero"}
                  1:
                    - {action_type: post, content: "SeedBot update episode one"}
                  2:
                    - {action_type: post, content: "SeedBot update episode two"}
            llm_user:
              count: 4
              prefab_module: silisocs.agents.entity
              sim_role_name: llm_user
              data:
                source: inline
                records:
                  - {name: Alice Analyst, persona: "Calm analyst posting concise political reactions.", seed_post: "Alice seed message"}
                  - {name: Bob Builder, persona: "Practical builder who asks direct questions.", seed_post: "Bob seed message"}
                  - {name: Cara Critic, persona: "Critical observer sharing concise viewpoints.", seed_post: "Cara seed message"}
                  - {name: Dan Debater, persona: "Debate-oriented user who likes replying to others.", seed_post: "Dan seed message"}
              field_map:
                name: name
                context: persona
                seed_post: seed_post

        shared_memories:
          - Users are in a recommendation retrieval validation experiment.

        initial_observations:
          - "{{name}} starts the recsys retrieval test."

        fixed_action_sets: {}

        """
    ).strip()

    evals_yaml = textwrap.dedent(
        """
        # @package evals

        probes: {}
        """
    ).strip()

    env_yaml = textwrap.dedent(
        """
        # @package env

        platform_type: twitter_like
        seed_posts:
          type: llm
        social_network:
          activity_transition_rates:
            fixed_seed:
              inactive_to_active: 1.0
              active_to_inactive: 0.0
            llm_user:
              inactive_to_active: 1.0
              active_to_inactive: 0.0
          fully_connected_targets:
            - llm_user
            - fixed_seed
          base_followership_probability: 1.0
          network_type: random
        """
    ).strip()

    (conf_dir / "sim.yaml").write_text(sim_yaml + "\n", encoding="utf-8")
    (conf_dir / "agent.yaml").write_text(agent_yaml + "\n", encoding="utf-8")
    (conf_dir / "evals.yaml").write_text(evals_yaml + "\n", encoding="utf-8")
    (conf_dir / "env.yaml").write_text(env_yaml + "\n", encoding="utf-8")


def _run_recsys_simulation(
    *,
    tmp_path: Path,
    scenario_name: str,
    recsys_type: str,
) -> Path:
    conf_dir = tmp_path / "conf"
    _write_scenario(conf_dir, scenario_name)

    llm_url = _llm_server_url()
    hydra_dir = tmp_path / "hydra"
    job_name = f"{scenario_name}_{recsys_type}"

    cmd = [
        sys.executable,
        "-m",
        "silisocs.runtime.runner",
        "--config-path",
        str(conf_dir),
        "env=twitter_like",
        "env.gm.preset=base",
        "env.enable_gm_multi_flow=false",
        "sim.engine.preset=base",
        "sim.engine.action_loop.built_in=single_action",
        "env.gm.components.next_acting.built_in=all_entities",
        "env.gm.components.resolve.built_in=parsed_action",
        "sim.action_mode=custom",
        "sim.tool_calling.mode=none",
        "env.timeline_mode=pure_recsys",
        "env.timeline_posts=10",
        f"env.gm.components.recommend.params.default_recsys_type={recsys_type}",
        f"env.gm.components.observe.params.recsys_type={recsys_type}",
        "env.gm.components.recommend.params.update_every_n_steps=1",
        "env.gm.components.recommend.params.max_posts=10",
        "sim.memory_backend=list",
        "sim.num_agents=5",
        "sim.num_steps=3",
        "sim.seed=13",
        "sim.write_html_log=false",
        "sim.max_concurrent_actions=8",
        "sim.llm_name=qwen3.5-4b",
        f"sim.llm_api_base={llm_url}",
        "sim.llm_api_key=test-key",
        "sim.engine.flow_routing.flow_order=[fixed_pre,default]",
        f"hydra.run.dir={hydra_dir}",
        f"hydra.job.name={job_name}",
        f"experiment_name=recsys_e2e_{recsys_type}",
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


def _read_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert rows, f"Expected non-empty JSONL: {path}"
    return rows


def _observation_section(prompt: str) -> str:
    start = "Observation:"
    end = "\n\nYou are currently operating on a Twitter like platform."
    sidx = prompt.find(start)
    eidx = prompt.find(end)
    if sidx == -1:
        return prompt
    if eidx == -1 or eidx <= sidx:
        return prompt[sidx:]
    return prompt[sidx:eidx]


def _assert_recommendations_and_retrieval(output_dir: Path, recsys_type: str) -> None:
    db_file = output_dir / "twitter_like.db"
    prompt_file = output_dir / "prompts_and_responses.jsonl"
    action_file = output_dir / "action_events.jsonl"

    assert db_file.exists(), f"Missing DB file: {db_file}"
    assert prompt_file.exists(), f"Missing prompts file: {prompt_file}"
    assert action_file.exists(), f"Missing action events file: {action_file}"

    with sqlite3.connect(db_file) as conn:
        total_recs = int(conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0])
        by_type = conn.execute(
            "SELECT recsys_type, COUNT(*) FROM recommendations GROUP BY recsys_type ORDER BY recsys_type"
        ).fetchall()

    assert total_recs > 0, (
        "Fail-fast: no recommendations generated. "
        f"recsys_type={recsys_type}, db={db_file}, by_type={by_type}"
    )

    rows = _read_jsonl(prompt_file)
    attempts = [r for r in rows if int(r.get("episode_idx", -1)) >= 1]
    assert attempts, "Expected action-episode prompt records"

    no_posts_prompts = 0
    timeline_prompts = 0
    posted_tweet_only_prompts = 0

    for row in attempts:
        obs = _observation_section(str(row.get("prompt", "") or ""))
        has_no_posts = "No posts available in your feed yet." in obs
        # Timeline formatter in twitter app emits User + Tweet ID lines for fetched timeline.
        has_timeline_rows = bool(re.search(r"User:\s+.*?Tweet ID:\s*\d+", obs, flags=re.DOTALL))
        has_posted_event_rows = bool(re.search(r"posted a tweet \(ID:\s*\d+\)", obs))

        if has_no_posts:
            no_posts_prompts += 1
        if has_timeline_rows:
            timeline_prompts += 1
        if has_posted_event_rows and not has_timeline_rows:
            posted_tweet_only_prompts += 1

    # Core fail-fast retrieval assertion.
    assert timeline_prompts > 0, (
        "Fail-fast: pure_recsys retrieval never surfaced timeline post rows in prompts. "
        f"recsys_type={recsys_type}, attempts={len(attempts)}, "
        f"no_posts_prompts={no_posts_prompts}, posted_tweet_only_prompts={posted_tweet_only_prompts}"
    )


@pytest.mark.llm_e2e
@pytest.mark.parametrize("recsys_type", ["twitter", "twhin"])
def test_llm_e2e_recsys_generation_and_retrieval_fail_fast(
    tmp_path: Path,
    recsys_type: str,
) -> None:
    output_dir = _run_recsys_simulation(
        tmp_path=tmp_path,
        scenario_name=f"llm_e2e_recsys_{recsys_type}",
        recsys_type=recsys_type,
    )
    _assert_recommendations_and_retrieval(output_dir, recsys_type)
