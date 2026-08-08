"""Cheap drift guards for the facts the first-run docs state about real files.

The reference docs stay accurate because they are edited alongside the code; the
*tutorial* surfaces drift silently, because nothing loads them. `election.md`
had promised "497 voters, 200 steps" against a scenario configured for 15 steps,
pointed at a `conf/eval/default.yaml` that does not exist, and described a
`${num_agents}`-driven voter count that was a hardcoded literal — none of it
detectable by the docs build, which only checks links between pages.

These tests are that detector. They are deliberately simple text matching, not a
Markdown parser: each asserts one high-value fact that a config edit would
invalidate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
ELECTION_CONF = REPO_ROOT / "scenarios" / "election" / "conf"
ELECTION_DOC = DOCS / "tutorials" / "election.md"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _doc_paths() -> list[Path]:
    return sorted(DOCS.rglob("*.md"))


def test_docs_tree_is_discovered() -> None:
    """Guard the globs: an empty sweep would make every test below vacuous."""
    assert _doc_paths(), "no docs/**/*.md files found"
    assert ELECTION_DOC.is_file(), f"missing {ELECTION_DOC}"


# --------------------------------------------------------------------------- #
# 1. election.md's stated counts match the scenario YAML.
# --------------------------------------------------------------------------- #


def test_election_tutorial_states_the_real_run_parameters() -> None:
    """`num_agents` / `num_steps` in the tutorial match world/default.yaml."""
    world = _load(ELECTION_CONF / "world" / "default.yaml")
    text = ELECTION_DOC.read_text(encoding="utf-8")

    num_agents = int(world["num_agents"])
    num_steps = int(world["num_steps"])

    assert f"num_agents: {num_agents}" in text, (
        f"election.md does not state the configured num_agents ({num_agents}); "
        "the scenario world file changed without the tutorial."
    )
    assert f"num_steps: {num_steps}" in text, (
        f"election.md does not state the configured num_steps ({num_steps})."
    )
    assert f"**{num_agents} agents, {num_steps} steps**" in text, (
        "election.md's headline scale claim no longer matches "
        f"num_agents={num_agents}, num_steps={num_steps}."
    )


def test_election_tutorial_states_the_real_class_counts() -> None:
    """Each persona-pipeline class `count` appears in the tutorial's table."""
    agents = _load(ELECTION_CONF / "agents" / "default.yaml")
    classes = agents["persona_pipeline"]["classes"]
    text = ELECTION_DOC.read_text(encoding="utf-8")

    for class_name, class_cfg in classes.items():
        count = int(class_cfg["count"])
        assert f"| `{class_name}` | {count} |" in text, (
            f"election.md's class table is missing or wrong for `{class_name}` "
            f"(configured count: {count})."
        )


def test_election_declared_total_matches_the_built_total() -> None:
    """`num_agents` equals the sum of class counts, as the tutorial claims.

    The runtime warns instead of failing when these diverge
    (``_warn_on_agent_count_mismatch``), so nothing else catches it.
    """
    world = _load(ELECTION_CONF / "world" / "default.yaml")
    agents = _load(ELECTION_CONF / "agents" / "default.yaml")
    built = sum(int(c["count"]) for c in agents["persona_pipeline"]["classes"].values())
    assert int(world["num_agents"]) == built, (
        f"election declares num_agents={world['num_agents']} but its class counts "
        f"sum to {built}; every run would log a mismatch warning."
    )


# --------------------------------------------------------------------------- #
# 2. Every docs reference to a scenarios/election/conf/ file resolves.
# --------------------------------------------------------------------------- #

_ELECTION_CONF_REF = re.compile(r"scenarios/election/conf/[A-Za-z0-9_./-]*[A-Za-z0-9_]")


@pytest.mark.parametrize("doc", _doc_paths(), ids=lambda p: str(p.relative_to(DOCS)))
def test_referenced_election_config_paths_exist(doc: Path) -> None:
    """A doc naming `scenarios/election/conf/...` must name something real."""
    missing: list[str] = []
    for ref in sorted(set(_ELECTION_CONF_REF.findall(doc.read_text(encoding="utf-8")))):
        target = REPO_ROOT / ref
        if not (target.is_file() or target.is_dir()):
            missing.append(ref)
    assert not missing, f"{doc.relative_to(REPO_ROOT)} references missing paths: {missing}"


def test_election_conf_reference_sweep_is_not_vacuous() -> None:
    """At least one doc really does reference the election config tree."""
    hits = [d for d in _doc_paths() if _ELECTION_CONF_REF.search(d.read_text(encoding="utf-8"))]
    assert hits, "no docs reference scenarios/election/conf/ — the regex has drifted"


# --------------------------------------------------------------------------- #
# 3. The first-run pages must not claim a `.hydra/` directory.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["usage.md", "quickstart.md"])
def test_no_dot_hydra_artifact_claims(name: str) -> None:
    """No page may list a `.hydra/<file>` as a run artifact.

    `hydra.output_subdir` is redirected, so those files are written to
    `configs/<jobname_format>/` instead. See `src/silisocs/conf/experiment.yaml`:
    ``output_subdir: configs/${jobname_format}``. Prose *denying* a `.hydra/`
    directory is allowed; a path into one is not.
    """
    text = (DOCS / name).read_text(encoding="utf-8")
    claimed = re.findall(r"\.hydra/[A-Za-z0-9_.-]+", text)
    assert not claimed, (
        f"docs/{name} lists {claimed} as run artifacts, but hydra.output_subdir "
        "is redirected to configs/<jobname_format>/ — no `.hydra/` directory is "
        "written."
    )


def test_experiment_yaml_still_redirects_the_hydra_output_subdir() -> None:
    """Guard the premise of the test above."""
    experiment = REPO_ROOT / "src" / "silisocs" / "conf" / "experiment.yaml"
    hydra_cfg = _load(experiment)["hydra"]
    assert hydra_cfg["output_subdir"] == "configs/${jobname_format}"
    assert "${now:" in hydra_cfg["job"]["name"], (
        "hydra.job.name lost its timestamp; the docs' claim that re-running never "
        "overwrites a previous run directory is no longer true."
    )
