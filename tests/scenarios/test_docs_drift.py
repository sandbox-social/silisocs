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

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
ELECTION_CONF = REPO_ROOT / "scenarios" / "election" / "conf"
ELECTION_DOC = DOCS / "tutorials" / "election.md"
USAGE_DOC = DOCS / "usage.md"
CONFIGURATION_DOC = DOCS / "configuration.md"

_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _fenced_blocks(path: Path) -> list[str]:
    """Return the body of every fenced code block in a Markdown file."""
    return _FENCE.findall(path.read_text(encoding="utf-8"))


def _blocks_containing(path: Path, needle: str) -> list[str]:
    return [block for block in _fenced_blocks(path) if needle in block]


def _json_objects(block: str) -> list[dict]:
    """Parse a fenced block written either as one JSON object or as JSONL rows."""
    try:
        whole = json.loads(block)
    except ValueError:
        pass
    else:
        return [whole] if isinstance(whole, dict) else []

    rows: list[dict] = []
    for line in block.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


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


# --------------------------------------------------------------------------- #
# 4. The JSON examples in usage.md must use the keys the code really emits.
#
# Both examples had drifted wholesale: every top-level key of the documented
# `sim_metrics.json` was invented, and the `probe_events.jsonl` row was a flat
# shape the probe deployer has never written. Nothing detected it, because the
# docs build only checks links. These tests re-derive the real key sets from the
# emitting classes — no simulation run, no fixtures.
# --------------------------------------------------------------------------- #


def _real_sim_metrics_keys() -> set[str]:
    """Top-level keys of `sim_metrics.json`, straight from the collector."""
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    return set(SimMetricsCollector().to_dict())


def _real_probe_row_keys() -> set[str]:
    """Top-level keys of a `probe_events.jsonl` row.

    The row is the probe deployer's payload (`source_user`/`label`/`data`, plus
    the `anchor` stamped by `_stamp_and_log`) after `EventLogger._prepare_item`
    adds the shared envelope, so deriving it needs neither a model nor a run.
    """
    from silisocs.runtime.io.jsonl import EventLogger

    payload = {"source_user": "", "label": "", "data": {}, "anchor": "pre_step"}
    return set(EventLogger("probe", "/dev/null")._prepare_item(payload))


#: `data` sub-keys the probe deployer sets on a probe row. Asserted to exist in
#: the probe sources below, so renaming one in code trips this guard.
_PROBE_DATA_KEYS = frozenset(
    {"probe_type", "probe_return", "raw_response", "probe_mode", "probe_error"}
)

_TOP_LEVEL_JSON_KEY = re.compile(r'^  "([A-Za-z0-9_]+)"\s*:', re.MULTILINE)


def test_usage_documents_the_real_sim_metrics_top_level_keys() -> None:
    """The `sim_metrics.json` example uses only keys `to_dict()` emits."""
    blocks = _blocks_containing(USAGE_DOC, "total_sim_duration_s")
    assert blocks, (
        "docs/usage.md no longer shows a sim_metrics.json example containing "
        "`total_sim_duration_s` — the drift guard cannot find it."
    )

    real = _real_sim_metrics_keys()
    # Indentation, not json.loads: the example legitimately elides long arrays.
    documented = {key for block in blocks for key in _TOP_LEVEL_JSON_KEY.findall(block)}
    assert documented, "found no top-level keys in the documented sim_metrics.json example"
    assert documented <= real, (
        f"docs/usage.md documents sim_metrics.json keys the collector never emits: "
        f"{sorted(documented - real)} (real keys: {sorted(real)})"
    )
    # The blocks a reader navigates by must actually be shown.
    for required in ("meta", "counters", "episode_metrics"):
        assert required in documented, (
            f"docs/usage.md's sim_metrics.json example omits the `{required}` block"
        )


def test_usage_documents_the_real_probe_event_row_keys() -> None:
    """The `probe_events.jsonl` example row matches the logged envelope."""
    blocks = _blocks_containing(USAGE_DOC, '"probe_return"')
    assert blocks, (
        "docs/usage.md no longer shows a probe_events.jsonl example containing "
        '`"probe_return"` — the drift guard cannot find it.'
    )

    real = _real_probe_row_keys()
    rows = [row for block in blocks for row in _json_objects(block)]

    assert rows, (
        "docs/usage.md's probe_events.jsonl example has no parseable JSON object; "
        "keep the example valid JSON (pretty-printed or one row per line) so this "
        "guard can check it."
    )
    for row in rows:
        assert set(row) <= real, (
            f"documented probe_events.jsonl row has keys the logger never writes: "
            f"{sorted(set(row) - real)} (real keys: {sorted(real)})"
        )
        data = row.get("data")
        assert isinstance(data, dict) and data, (
            "a probe_events.jsonl row carries its answer in a nested `data` object"
        )
        assert set(data) <= _PROBE_DATA_KEYS, (
            f"documented probe row `data` has unknown keys: {sorted(set(data) - _PROBE_DATA_KEYS)}"
        )


def test_probe_data_key_names_still_exist_in_the_probe_sources() -> None:
    """Guard the premise: `_PROBE_DATA_KEYS` is pinned, so pin it to the code."""
    sources = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (REPO_ROOT / "src" / "silisocs" / "evaluations" / "probes").rglob("*.py")
        )
    )
    assert sources, "probe sources not found; the path has moved"
    missing = [key for key in sorted(_PROBE_DATA_KEYS) if f'"{key}"' not in sources]
    assert not missing, (
        f"{missing} no longer appear in silisocs/evaluations/probes/; the documented "
        "probe row `data` shape (and this test's pinned key set) is stale."
    )


# --------------------------------------------------------------------------- #
# 5. Two one-line tripwires for corrections that had already regressed once.
# --------------------------------------------------------------------------- #


def test_agents_md_points_custom_backends_at_the_real_base_and_class_path() -> None:
    """AGENTS.md must not resurrect "subclass SocialBackendApp, register in app factory".

    `SocialBackendApp` is a pure capability interface; a custom backend subclasses
    `BackendApp` (or `PlatformBackedSocialApp`) and is selected with
    ``type: custom`` + ``class_path`` — no factory edit. See docs/backends.md.
    """
    lines = [
        line
        for line in AGENTS_MD.read_text(encoding="utf-8").splitlines()
        if "add custom backend" in line.lower()
    ]
    assert lines, "AGENTS.md lost its 'to add custom backend' guidance line"
    for line in lines:
        assert "BackendApp" in line, (
            f"AGENTS.md custom-backend guidance names no BackendApp base: {line!r}"
        )
        assert "class_path" in line, (
            "AGENTS.md custom-backend guidance must say registration is `class_path` "
            f"(``type: custom``), not an app-factory edit: {line!r}"
        )
        assert "app factory" not in line.lower(), (
            f"AGENTS.md still tells contributors to register in the app factory: {line!r}"
        )


def test_configuration_uses_the_plural_episode_observation_flows() -> None:
    """`timeline_every_turn` takes `episode_observation_flows: [...]` (a list).

    The singular `episode_observation_flow` belongs to the `episode_only`
    built-in; under `timeline_every_turn` it is an unknown component param and
    fails at build. The docs (and the scenario scaffolder) had shipped the
    singular spelling.
    """
    text = CONFIGURATION_DOC.read_text(encoding="utf-8")
    assert "episode_observation_flows" in text, (
        "docs/configuration.md no longer documents `episode_observation_flows`"
    )
    offenders = [
        block
        for block in _fenced_blocks(CONFIGURATION_DOC)
        if "timeline_every_turn" in block
        and re.search(r"^\s*episode_observation_flow\s*:", block, re.MULTILINE)
    ]
    assert not offenders, (
        "docs/configuration.md shows the singular `episode_observation_flow:` under a "
        "`timeline_every_turn` component — that param name fails at build."
    )
