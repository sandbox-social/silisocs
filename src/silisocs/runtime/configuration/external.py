"""External Hydra config-path support."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import yaml
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf


def _hydra_task_overrides() -> list[str]:
    """The run's CLI overrides, or ``[]`` when not running under a Hydra app.

    ``HydraConfig.get()`` raising ``ValueError`` means there is no Hydra context
    (programmatic use, e.g. tests calling the merge directly); anything else is a
    real failure and must propagate.
    """
    try:
        hydra_cfg = HydraConfig.get()
    except ValueError:
        return []
    return [str(item) for item in hydra_cfg.overrides.task]


def merge_external_group_overrides(cfg: DictConfig) -> DictConfig:
    paths_csv = os.environ.get("SILISOCS_EXTERNAL_CONFIG_DIRS", "").strip()
    if not paths_csv:
        return cfg

    task_overrides = _hydra_task_overrides()
    world_variant = ""
    for item in task_overrides:
        if item.startswith("world="):
            world_variant = item.split("=", 1)[1].strip()
            break

    OmegaConf.set_struct(cfg, False)
    merged_cfg: DictConfig = cfg
    for raw_dir in [p for p in paths_csv.split(":") if p]:
        conf_dir = Path(raw_dir)

        for group in ("agents", "env", "eval", "sim"):
            file_path = conf_dir / f"{group}.yaml"
            if not file_path.is_file():
                continue
            loaded = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"Expected mapping in {file_path}, got {type(loaded).__name__}")
            merged_cfg = cast(
                DictConfig,
                OmegaConf.merge(merged_cfg, OmegaConf.create({group: loaded})),
            )

        if world_variant:
            for group in ("env", "sim", "eval"):
                variant_path = conf_dir / group / f"{world_variant}.yaml"
                if not variant_path.is_file():
                    continue
                loaded = yaml.safe_load(variant_path.read_text(encoding="utf-8")) or {}
                if not isinstance(loaded, dict):
                    raise ValueError(
                        f"Expected mapping in {variant_path}, got {type(loaded).__name__}"
                    )
                merged_cfg = cast(
                    DictConfig,
                    OmegaConf.merge(merged_cfg, OmegaConf.create({group: loaded})),
                )

    # Re-apply the user's CLI value overrides on top of the external merges so
    # `--config-path ... sim.llm.temperature=0.9` keeps winning. A malformed
    # override must fail the run here, not be silently dropped.
    external_group_overrides = {"world", "agents", "env", "eval", "sim"}
    value_overrides = []
    for text in task_overrides:
        if text.startswith("~"):
            # Hydra deletions were applied at compose time and have no dotlist
            # equivalent to re-apply.
            continue
        # `+key=value` additions merge exactly like plain assignments here.
        text = text.lstrip("+")
        if "=" not in text:
            continue
        key = text.split("=", 1)[0].strip()
        if key in external_group_overrides:
            continue
        value_overrides.append(text)
    if value_overrides:
        override_cfg = OmegaConf.from_dotlist(value_overrides)
        merged_cfg = cast(DictConfig, OmegaConf.merge(merged_cfg, override_cfg))

    return merged_cfg


def _resolve_config_dir_argument(raw: str) -> Path | None:
    """Resolve a --config-path argument to a directory.

    Accepts a filesystem path, or — when that does not exist — a repository
    scenario reference (``election``, ``scenarios/election/conf``), so
    documented invocations work from any directory within a repo checkout.
    """
    directory = Path(raw).resolve()
    if directory.is_dir():
        return directory
    from silisocs.scenario_library import scenario_conf_path

    try:
        return scenario_conf_path(raw)
    except FileNotFoundError:
        return None


def set_external_config_dirs(dirs: Sequence[str | Path]) -> None:
    """Programmatic twin of the CLI's ``--config-path``/``--overlay-config-path``.

    Resolves each entry like the CLI flag does (a filesystem path, or a
    repository scenario reference such as ``election``) and points the
    search-path plugin and group merge at them. An empty sequence clears any
    previous selection.
    """
    resolved: list[Path] = []
    for raw in dirs:
        directory = _resolve_config_dir_argument(str(raw))
        if directory is None:
            from silisocs.scenario_library import list_scenarios

            available = ", ".join(list_scenarios()) or "<none>"
            raise FileNotFoundError(
                f"'{raw}' is neither an existing directory nor a known repository "
                f"scenario. Available scenarios: {available}"
            )
        resolved.append(directory)
    os.environ["SILISOCS_EXTERNAL_CONFIG_DIRS"] = ":".join(str(path) for path in resolved)


def inject_external_config_path() -> None:
    primary_flag = "--config-path"
    overlay_flag = "--overlay-config-path"
    if primary_flag not in sys.argv and overlay_flag not in sys.argv:
        return

    external_dir: Path | None = None
    overlay_dirs: list[Path] = []
    cleaned_argv = [sys.argv[0]]
    i = 1
    while i < len(sys.argv):
        token = sys.argv[i]
        if token in {primary_flag, overlay_flag}:
            if i + 1 >= len(sys.argv):
                print(f"ERROR: {token} requires a directory argument.")
                sys.exit(1)
            directory = _resolve_config_dir_argument(sys.argv[i + 1])
            if directory is None:
                from silisocs.scenario_library import list_scenarios

                available = ", ".join(list_scenarios()) or "<none>"
                print(
                    f"ERROR: {token} '{sys.argv[i + 1]}' is neither an existing "
                    f"directory nor a known repository scenario. "
                    f"Available scenarios: {available}"
                )
                sys.exit(1)
            if token == primary_flag:
                external_dir = directory
            else:
                overlay_dirs.append(directory)
            i += 2
            continue
        cleaned_argv.append(token)
        i += 1

    if external_dir is None and not overlay_dirs:
        return

    sys.argv[:] = cleaned_argv
    if external_dir is not None:
        print(f"External config path: {external_dir}")
    for overlay in overlay_dirs:
        print(f"Overlay config path: {overlay}")

    merge_dirs: list[Path] = []
    if external_dir is not None:
        merge_dirs.append(external_dir)
    merge_dirs.extend(overlay_dirs)
    os.environ["SILISOCS_EXTERNAL_CONFIG_DIRS"] = ":".join(str(path) for path in merge_dirs)


def register_search_path_plugin() -> None:
    import os as _os

    from hydra.core.plugins import Plugins
    from hydra.plugins.search_path_plugin import SearchPathPlugin

    class _ScenarioSearchPathPlugin(SearchPathPlugin):
        def manipulate_search_path(self, search_path: Any) -> None:  # type: ignore[override]
            paths_csv = _os.environ.get("SILISOCS_EXTERNAL_CONFIG_DIRS", "").strip()
            if not paths_csv:
                return
            for raw_dir in [p for p in paths_csv.split(":") if p]:
                search_path.prepend("file", raw_dir)

    Plugins.instance().register(_ScenarioSearchPathPlugin)
