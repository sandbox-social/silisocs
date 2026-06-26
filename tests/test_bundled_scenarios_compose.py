"""Every bundled scenario must compose with the documented override set.

Regression net for the config-composition contract: scenario `world/` files
REPLACE the base world group (Hydra searchpath shadowing), so each scenario
must re-declare the universal run params (num_steps, seed, output_rootname,
...) or documented invocations like

    silisocs --config-path scenarios/election/conf num_steps=15

fail at compose time with "Key 'num_steps' is not in struct".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra

from silisocs.runtime.configuration.external import register_search_path_plugin

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_CONF = REPO_ROOT / "src" / "silisocs" / "conf"
SCENARIOS = REPO_ROOT / "scenarios"

# Plain (non-`++`) overrides every scenario must accept, per docs/usage.md.
STANDARD_OVERRIDES = [
    "num_agents=3",
    "num_steps=1",
    "seed=2",
    "run_name=compose_check",
    "output_rootname=/tmp/compose_check",
]


def _scenario_targets() -> list[tuple[str, Path, str]]:
    targets: list[tuple[str, Path, str]] = []
    for scenario_dir in sorted(SCENARIOS.iterdir()):
        conf = scenario_dir / "conf"
        world_dir = conf / "world"
        if not world_dir.is_dir():
            continue
        for world_file in sorted(world_dir.glob("*.yaml")):
            targets.append((scenario_dir.name, conf, world_file.stem))
    return targets


@pytest.mark.parametrize(
    ("scenario", "conf_dir", "world"),
    _scenario_targets(),
    ids=[f"{name}:{world}" for name, _, world in _scenario_targets()],
)
def test_bundled_scenario_composes_with_standard_overrides(
    scenario: str,
    conf_dir: Path,
    world: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILISOCS_EXTERNAL_CONFIG_DIRS", str(conf_dir))
    register_search_path_plugin()

    overrides = [f"world={world}", *STANDARD_OVERRIDES]
    # Mirror runner behavior: select agents/env variants when the scenario ships them.
    if (conf_dir / "agents" / f"{world}.yaml").is_file() and world != "default":
        overrides.append(f"agents={world}")
    if (conf_dir / "env" / f"{world}.yaml").is_file() and world != "default":
        overrides.append(f"env={world}")

    GlobalHydra.instance().clear()
    try:
        with initialize_config_dir(config_dir=str(BASE_CONF), version_base=None):
            cfg = compose(config_name="experiment", overrides=overrides)
    finally:
        GlobalHydra.instance().clear()

    assert cfg.num_steps == 1, f"{scenario}: num_steps override not applied"
    assert cfg.num_agents == 3, f"{scenario}: num_agents override not applied"
    assert cfg.seed == 2, f"{scenario}: seed override not applied"
    assert cfg.output_rootname == "/tmp/compose_check"
