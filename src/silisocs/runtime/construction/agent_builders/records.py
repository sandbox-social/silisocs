"""Record, memory, and path loading for agent builders."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from silisocs.runtime.construction.agent_builders.common import (
    extract_path,
    normalize_memories,
    safe_path_exists,
    to_plain,
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_ROOT = _PACKAGE_ROOT.parents[1]


class RecordLoader:
    """Load persona records and scenario-local support files."""

    def __init__(
        self,
        config: Any,
        *,
        scenario_name: str | None = None,
        project_root: str | Path | None = None,
    ):
        self.config = config
        self.scenario_name = scenario_name
        self.project_root = Path(project_root) if project_root is not None else _PROJECT_ROOT

    def load_records(
        self,
        data_cfg: dict[str, Any],
        *,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        source = data_cfg.get("source", "local_json")
        if source == "inline":
            records = data_cfg.get("records", [])
        elif source == "config_path":
            path = data_cfg.get("path")
            if not path:
                raise ValueError("config_path source requires a `path` field")
            records = extract_path(to_plain(self.config), path)
        elif source == "csv":
            path = data_cfg.get("path")
            if not path:
                raise ValueError("csv source requires a `path` field")
            records = self.load_csv(path, max_records=max_records)
        elif source == "jsonl":
            path = data_cfg.get("path")
            if not path:
                raise ValueError("jsonl source requires a `path` field")
            records = self.load_jsonl(path, max_records=max_records)
        elif source == "hf_dataset":
            records = self.load_hf_dataset(data_cfg, max_records=max_records)
        else:
            path = data_cfg.get("path") or data_cfg.get("dataset")
            if not path:
                raise ValueError(f"{source} source requires a `path` or `dataset` field")
            with open(self.resolve_file_path(str(path))) as f:
                records = json.load(f)

        records = to_plain(records)
        if isinstance(records, dict):
            records = list(records.values()) if data_cfg.get("expand_values") else [records]
        if not isinstance(records, list):
            raise ValueError(f"Expected list of records, got {type(records).__name__}")
        return [record if isinstance(record, dict) else {"value": record} for record in records]

    def load_hf_dataset(
        self,
        data_cfg: dict[str, Any],
        *,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        dataset_name = data_cfg.get("dataset")
        split = data_cfg.get("split", "train")
        subset = data_cfg.get("subset")
        if not dataset_name:
            raise ValueError("hf_dataset source requires a `dataset` field")

        try:
            from datasets import load_dataset
        except ImportError as exc:
            raise ImportError(
                "This scenario's persona pipeline loads a Hugging Face dataset, "
                "which requires the optional `hf` extra. Install it with "
                "`uv sync --extra hf` (or `pip install 'silisocs[hf]'`)."
            ) from exc

        load_kwargs: dict[str, Any] = {"split": split}
        load_args: tuple[Any, ...] = (dataset_name, subset) if subset else (dataset_name,)
        dataset = load_dataset(*load_args, **load_kwargs)
        return self.materialize_hf_records(dataset, max_records=max_records)

    @staticmethod
    def materialize_hf_records(ds: Any, *, max_records: int | None) -> list[dict[str, Any]]:
        """Materialize at most ``max_records`` rows from a dataset-like object."""
        if max_records is None:
            return list(ds)
        limit = max(0, int(max_records))
        if limit == 0:
            return []

        if hasattr(ds, "select"):
            try:
                total = len(ds)
            except Exception:
                total = limit
            try:
                return list(ds.select(range(min(limit, total))))
            except Exception:
                pass

        records: list[dict[str, Any]] = []
        for idx, item in enumerate(ds):
            if idx >= limit:
                break
            records.append(item)
        return records

    def load_csv(self, path: str, *, max_records: int | None = None) -> list[dict[str, Any]]:
        file_path = self.resolve_file_path(str(path))
        records: list[dict[str, Any]] = []
        with open(file_path, encoding="utf-8") as f:
            for idx, row in enumerate(csv.DictReader(f)):
                if max_records and idx >= max_records:
                    break
                records.append(dict(row))
        return records

    def load_jsonl(self, path: str, *, max_records: int | None = None) -> list[dict[str, Any]]:
        file_path = self.resolve_file_path(str(path))
        records: list[dict[str, Any]] = []
        with open(file_path, encoding="utf-8") as f:
            for idx, raw in enumerate(f):
                if max_records and idx >= max_records:
                    break
                line = raw.strip()
                if not line:
                    continue
                row = json.loads(line)
                records.append(row if isinstance(row, dict) else {"value": row})
        return records

    def scenario_paths(self, path_str: str) -> list[Path]:
        try:
            raw = Path(path_str)
        except (TypeError, ValueError, OSError):
            return []
        if raw.is_absolute():
            return [raw]

        scenario = str(self.scenario_name or "default")
        pkg_scenario = _PACKAGE_ROOT / "scenarios" / scenario
        top_scenario = self.project_root / "scenarios" / scenario
        subdirs = ["", "input", "input/personas", "input/news_data"]
        candidates = [raw, _PACKAGE_ROOT / raw]
        for base in (pkg_scenario, top_scenario):
            for subdir in subdirs:
                candidates.append(base / subdir / raw if subdir else base / raw)
        return candidates

    def resolve_file_path(self, path_str: str) -> Path:
        for candidate in self.scenario_paths(path_str):
            if safe_path_exists(candidate):
                return candidate
        raise FileNotFoundError(f"Unable to resolve path: {path_str}")

    def load_memories(self, value: Any) -> list[str]:
        value = to_plain(value)
        if value is None:
            return []
        if isinstance(value, list):
            merged: list[str] = []
            for item in value:
                merged.extend(self.load_memories(item))
            return merged
        if isinstance(value, dict):
            if path := value.get("path"):
                return self.load_memories(str(self.resolve_file_path(str(path))))
            return normalize_memories(value.get("text"))
        if not isinstance(value, str):
            return normalize_memories(value)

        for candidate in self.scenario_paths(value):
            if safe_path_exists(candidate):
                if str(candidate).endswith(".json"):
                    with open(candidate) as f:
                        return normalize_memories(json.load(f))
                with open(candidate) as f:
                    return [line.strip() for line in f if line.strip()]
        return normalize_memories(value)
