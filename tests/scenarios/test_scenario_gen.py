"""The scaffolder must emit a scenario the runtime actually accepts.

These tests used to yaml-load the generated files and assert on keys, which is
exactly why two always-invalid emissions shipped: a seed-post provider
``type: llm`` (no such provider) and the singular ``episode_observation_flow``
param (``timeline_every_turn`` takes the plural list, and component params are
strict). Nothing composed the output, so nothing noticed. The compose/validate
and dry-run tests below are the net.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra

from silisocs.runtime.configuration.external import (
    merge_external_group_overrides,
    register_search_path_plugin,
)
from silisocs.runtime.configuration.validation import validate_scenario_config
from silisocs.scenario_gen.specs import ScenarioSpec
from silisocs.scenario_gen.writer import write_scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_CONF = REPO_ROOT / "src" / "silisocs" / "conf"

# Same override set every bundled scenario must accept (docs/usage.md).
STANDARD_OVERRIDES = [
    "num_agents=3",
    "num_steps=1",
    "seed=2",
    "run_name=compose_check",
    "output_dir=/tmp/scenario_gen_compose_check",
]

_PROBES = [
    {
        "id": "believes_claim",
        "probe_type": "BinaryProbe",
        "name": "BelievesClaim",
        "question": "Do you believe the claim? Answer yes or no.",
        "context": "{agentname} is asked about the claim.",
    },
    {
        "id": "trust_rating",
        "probe_type": "NumericRatingProbe",
        "question": "Return a number from {lo} to {hi} for how much you trust strangers.",
        "lo": 1,
        "hi": 10,
        "deployment": {"start_step": 2, "include_agents": ["Alice"]},
    },
]


def _minimal_spec() -> dict:
    return {
        "name": "tiny_world",
        "scenario_name": "tiny_world",
        "jobname_format": "Tiny_N${num_agents}_T${num_steps}_${run_name}",
        # 3, not 1: the default barabasi_albert follow graph needs n > m (m=2).
        "num_agents": 3,
        "num_steps": 1,
        "run_name": "tiny_world",
        "setting": {"name": "Tiny", "background": ["A small test setting."]},
        "event": {"name": "Test", "context": "A small test event."},
        "agent_classes": [
            {
                "role": "user",
                "sim_role_name": "user",
                "count": 3,
                "agents": [
                    {
                        "name": name,
                        "username": name.lower(),
                        "context": f"{name} tests the generated world.",
                        "style": "Brief.",
                        "goal": "Run the smoke test.",
                        "seed_post": "",
                    }
                    for name in ("Alice", "Bob", "Cara")
                ],
            }
        ],
        "backend": {"type": "twitter_like", "timeline_mode": "follower_chronological"},
        "custom_agent_stubs": [],
    }


def test_scenario_spec_accepts_current_public_backend_and_agent_stub_fields() -> None:
    spec = ScenarioSpec.model_validate(_minimal_spec())

    assert spec.backend.type == "twitter_like"
    assert spec.custom_agent_stubs == []


def test_scenario_writer_emits_component_owned_env_config(tmp_path) -> None:
    spec = ScenarioSpec.model_validate(_minimal_spec())
    write_scenario(spec, tmp_path / spec.name)

    env_cfg = yaml.safe_load((tmp_path / spec.name / "conf" / "env.yaml").read_text())

    assert env_cfg["gm"]["backend"]["type"] == "twitter_like"
    assert env_cfg["gm"]["components"]["initialize"]["params"]["graph"]
    # Activity models are sim-level participation config, not env next_acting.
    assert env_cfg["gm"]["components"]["next_acting"]["built_in"] == "all_agents"

    sim_cfg = yaml.safe_load((tmp_path / spec.name / "conf" / "sim.yaml").read_text())
    participation = sim_cfg["engine"]["participation"]
    assert participation["built_in"] == "activity_probability"
    assert "activity_transition_rates" in participation["params"]


def test_writer_emits_only_real_component_params_and_provider_types(tmp_path) -> None:
    """The two blockers, pinned as unit assertions.

    ``timeline_every_turn`` takes the PLURAL ``episode_observation_flows`` list;
    ``seed_posts`` provider types are agent | csv | json | fallback | none.
    """
    spec = ScenarioSpec.model_validate(_minimal_spec())
    write_scenario(spec, tmp_path / spec.name)

    env_cfg = yaml.safe_load((tmp_path / spec.name / "conf" / "env.yaml").read_text())
    observe_params = env_cfg["gm"]["components"]["observe"]["params"]
    assert observe_params["episode_observation_flows"] == ["fixed_pre"]
    assert "episode_observation_flow" not in observe_params

    sim_cfg = yaml.safe_load((tmp_path / spec.name / "conf" / "sim.yaml").read_text())
    seed_posts = sim_cfg["initialization"]["simulation"]
    assert seed_posts["built_in"] == "seed_posts"
    assert seed_posts["params"]["type"] == "agent"


def test_writer_renders_declared_probes_into_eval_probes(tmp_path) -> None:
    spec = ScenarioSpec.model_validate({**_minimal_spec(), "probes": _PROBES})
    write_scenario(spec, tmp_path / spec.name)

    eval_cfg = yaml.safe_load((tmp_path / spec.name / "conf" / "eval.yaml").read_text())
    probes = eval_cfg["probes"]["probes"]

    assert set(probes) == {"believes_claim", "trust_rating"}
    binary = probes["believes_claim"]
    assert binary["probe_name"] == "believes_claim"
    assert binary["probe_type"] == "BinaryProbe"
    assert binary["probe_data"]["name"] == "BelievesClaim"
    assert binary["probe_data"]["question"].startswith("Do you believe")
    assert "deployment" not in binary  # inherits the global deployment block

    numeric = probes["trust_rating"]
    # `name` defaults to the entry id; unset bounds are omitted, set ones emitted.
    assert numeric["probe_data"]["name"] == "trust_rating"
    assert (numeric["probe_data"]["lo"], numeric["probe_data"]["hi"]) == (1, 10)
    # Only the fields the author set: nulls would shadow the global defaults.
    assert numeric["deployment"] == {"start_step": 2, "include_agents": ["Alice"]}


def test_no_probes_declared_keeps_the_empty_probe_block(tmp_path) -> None:
    spec = ScenarioSpec.model_validate(_minimal_spec())
    write_scenario(spec, tmp_path / spec.name)

    eval_cfg = yaml.safe_load((tmp_path / spec.name / "conf" / "eval.yaml").read_text())
    assert eval_cfg["probes"]["probes"] == {}
    assert eval_cfg["probes"]["deployment"]["enabled"] is True


def _compose_generated(conf_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SILISOCS_EXTERNAL_CONFIG_DIRS", str(conf_dir))
    register_search_path_plugin()
    overrides = ["world=default", *STANDARD_OVERRIDES]
    GlobalHydra.instance().clear()
    try:
        with initialize_config_dir(config_dir=str(BASE_CONF), version_base=None):
            cfg = compose(config_name="experiment", overrides=overrides)
    finally:
        GlobalHydra.instance().clear()
    return merge_external_group_overrides(cfg, value_overrides=overrides)


@pytest.mark.parametrize("with_probes", [False, True], ids=["no_probes", "with_probes"])
def test_generated_scenario_composes_and_passes_preflight_validation(
    with_probes: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Push the scaffolder's own output through real composition + validation.

    This is the test whose absence let `silisocs new-scenario` ship a scenario
    that failed its own `validate_scenario` dry run on every invocation.
    """
    raw = {**_minimal_spec(), **({"probes": _PROBES} if with_probes else {})}
    spec = ScenarioSpec.model_validate(raw)
    root = tmp_path / spec.name
    write_scenario(spec, root)

    cfg = _compose_generated(root / "conf", monkeypatch)

    assert cfg.num_steps == 1
    assert cfg.scenario_name == "tiny_world"
    expected_probes = {"believes_claim", "trust_rating"} if with_probes else set()
    assert set(cfg.eval.probes.probes) == expected_probes

    validate_scenario_config(cfg, root)
    capsys.readouterr()  # the validators print a per-check summary


@pytest.mark.subprocess
def test_generated_scenario_builds_a_runtime(tmp_path: Path) -> None:
    """Compose + preflight is still not enough: construction itself must accept it.

    The singular `episode_observation_flow` param composed and passed preflight —
    it only died when the component was built with strict params. Runs the
    generated scenario through the same dry run `silisocs-config-dry-run` uses.
    """
    from silisocs.runtime.config_dry_run import run_dry_runs

    spec = ScenarioSpec.model_validate({**_minimal_spec(), "probes": _PROBES})
    root = tmp_path / spec.name
    write_scenario(spec, root)

    results = run_dry_runs(REPO_ROOT, config_path=root / "conf")

    assert results, "generated scenario produced no dry-run target"
    failures = [
        f"{r.target.label}: {(r.stderr or r.stdout).strip()[-2000:]}" for r in results if not r.ok
    ]
    assert not failures, "generated scenario failed the dry run:\n" + "\n".join(failures)
