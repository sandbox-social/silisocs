"""Agent builder — constructs ``AgentConfig`` objects from scenario YAML.

The ``BaseAgentBuilder`` reads the ``persona_pipeline`` config section and
builds agents from class definitions. It handles data loading (local JSON,
HuggingFace datasets, inline records), field mapping, memory loading, and
name derivation.

**Extending the builder** — for simple scenarios, ``BaseAgentBuilder`` works
out of the box with the class-based pipeline.  Override ``build_role_agents``
only if you need legacy role-count based building::

    class MyBuilder(BaseAgentBuilder):
        def build_role_agents(self, role: str, count: int) -> list[AgentConfig]:
            ...
"""

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf

from mastodon_sim.runtime.dataclasses import AgentConfig

logger = logging.getLogger(__name__)

# Cached project paths derived from this file's location.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _PACKAGE_ROOT.parents[2]
_HF_CACHE_MAX_READ_BYTES = 100 * 1024 * 1024


class BaseAgentBuilder:
    """Build agent configurations from scenario YAML persona pipeline.

    The primary entry point is ``build_agents(roles)``, which dispatches
    to the class-based pipeline (``persona_pipeline.classes``) when available,
    falling back to ``build_role_agents()`` for legacy role-count configs.
    """

    def __init__(self, scenario_config: Any):
        self.config = scenario_config

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def build_agents(self, roles: dict[str, int]) -> list[AgentConfig]:
        """Build all agent configs from persona pipeline or role counts."""
        pipeline = getattr(self.config, "persona_pipeline", None)
        if pipeline and getattr(pipeline, "classes", None):
            return self._deduplicate(self._build_from_classes())

        agents: list[AgentConfig] = []
        for role, count in roles.items():
            agents.extend(self.build_role_agents(role, count))
        return self._deduplicate(agents)

    def build_role_agents(self, role: str, count: int) -> list[AgentConfig]:
        """Build agents for a specific role (legacy path).

        Override this when using role-count based building instead of the
        class-based persona pipeline.
        """
        raise NotImplementedError(
            f"build_role_agents() not implemented. Define persona_pipeline.classes "
            f"in your scenario YAML or override this method for role '{role}'."
        )

    def load_news_data(self, news_file: str) -> dict[str, Any]:
        """Load news headlines and images from a JSON file."""
        file_path = self._resolve_file_path(f"{news_file}.json")
        with open(file_path) as f:
            return json.load(f)

    # ------------------------------------------------------------------ #
    # Class-based pipeline
    # ------------------------------------------------------------------ #

    def _build_from_classes(self) -> list[AgentConfig]:
        pipeline_cfg = self._to_plain(self.config.persona_pipeline)
        defaults = pipeline_cfg.get("defaults", {})
        classes = pipeline_cfg.get("classes", {})

        default_params = defaults.get("params", {}) or {}
        default_field_map = defaults.get("field_map", {}) or {}
        default_mem_field = defaults.get("specific_memories_field")
        default_shared = self._load_memories(defaults.get("shared_memories", []))

        all_agents: list[AgentConfig] = []
        for class_name, class_cfg in classes.items():
            class_cfg = class_cfg or {}
            agents = self._build_class(
                class_name, class_cfg, default_params, default_field_map,
                default_mem_field, default_shared,
            )
            all_agents.extend(agents)
        return all_agents

    def _build_class(
        self,
        class_name: str,
        class_cfg: dict,
        default_params: dict,
        default_field_map: dict,
        default_mem_field: str | None,
        default_shared: list[str],
    ) -> list[AgentConfig]:
        data_cfg = class_cfg.get("data", {})
        count = class_cfg.get("count")
        records = (
            self._load_records(data_cfg, max_records=int(count) if count is not None else None)
            if data_cfg else [{}]
        )
        if count is not None:
            records = records[:int(count)]

        prefab_module = class_cfg.get("prefab_module")
        if not prefab_module:
            raise ValueError(f"Class `{class_name}` must define `prefab_module`")
        prefab_name = class_cfg.get("prefab", prefab_module.split(".")[-1] + "__Entity")
        sim_role = class_cfg.get("sim_role_name", class_name)

        field_map = {**default_field_map, **(class_cfg.get("field_map", {}) or {})}
        class_params = class_cfg.get("params", {}) or {}

        shared = list(default_shared)
        for m in self._load_memories(class_cfg.get("shared_memories", [])):
            if m not in shared:
                shared.append(m)

        mem_field = class_cfg.get("specific_memories_field", default_mem_field)

        # Name derivation settings.
        derive_name = bool(class_cfg.get("derive_name_from_context", False))
        if data_cfg.get("source") == "hf_dataset":
            ds_name = str(data_cfg.get("dataset", "")).strip().lower()
            derive_name = derive_name or ds_name == "nvidia/nemotron-personas-usa"
        name_words = int(class_cfg.get("name_from_context_words", 2))

        # News posts (election-specific feature).
        news_posts: dict[str, str] | None = None
        if bool(class_cfg.get("use_news_file_posts", False)):
            news_file = getattr(self.config.data, "news_file", None)
            if not news_file:
                raise ValueError(f"Class `{class_name}` requested news posts but data.news_file is unset.")
            include_images = bool(class_cfg.get("include_news_images", False))
            raw_news = self.load_news_data(str(news_file))
            news_posts = {
                h: (c[0] if include_images and isinstance(c, list) else "")
                for h, c in raw_news.items()
            }

        agents: list[AgentConfig] = []
        for idx, record in enumerate(records, start=1):
            params = self._build_agent_params(
                record, idx, class_name, field_map,
                default_params, class_params, mem_field,
                sim_role, prefab_module, shared,
                derive_name, name_words, news_posts,
            )
            agents.append(AgentConfig(prefab=prefab_name, params=params))
        return agents

    def _build_agent_params(
        self,
        record: dict,
        idx: int,
        class_name: str,
        field_map: dict,
        default_params: dict,
        class_params: dict,
        mem_field: str | None,
        sim_role: str,
        prefab_module: str,
        shared: list[str],
        derive_name: bool,
        name_words: int,
        news_posts: dict[str, str] | None,
    ) -> dict[str, Any]:
        # Map fields from record.
        mapped: dict[str, Any] = {}
        for target, source in field_map.items():
            value = self._resolve_source(record, source)
            if target in {"name", "context", "style", "goal", "bio", "seed_post"}:
                value = self._coerce_text(value, joiner=" " if target == "name" else "\n")
            mapped[target] = value

        params: dict[str, Any] = {}
        params.update(default_params)
        params.update(class_params)
        params.update({k: v for k, v in mapped.items() if v is not None})

        context = self._coerce_text(params.get("context"))
        if not context:
            fields = (
                ", ".join(sorted(str(k) for k in record))
                if isinstance(record, Mapping) else type(record).__name__
            )
            raise ValueError(
                f"Class `{class_name}` record {idx} missing `context` "
                f"(field_map.context={field_map.get('context')!r}). Fields: {fields}"
            )

        name = self._coerce_text(params.get("name"), joiner=" ")
        if not name and derive_name:
            name = self._derive_name(context, words=name_words)
        if not name:
            name = f"{class_name}_{idx}"

        if "specific_memories" not in params and mem_field:
            params["specific_memories"] = self._extract_path(record, mem_field)
        params["specific_memories"] = self._normalize_memories(params.get("specific_memories", []))

        if news_posts is not None:
            params["posts"] = news_posts

        params["name"] = name
        params["context"] = context
        params["sim_role"] = {"name": sim_role, "module_path": prefab_module}
        params["style"] = self._coerce_text(params.get("style", ""))
        params["seed_post"] = self._coerce_text(params.get("seed_post", ""))
        params["bio"] = self._coerce_text(params.get("bio", ""))
        goal = params.get("goal")
        params["goal"] = self._coerce_text(goal) if goal is not None else None
        if shared:
            params["shared_memories"] = shared
        return params

    # ------------------------------------------------------------------ #
    # Deduplication
    # ------------------------------------------------------------------ #

    @staticmethod
    def _deduplicate(configs: list[AgentConfig]) -> list[AgentConfig]:
        result: list[AgentConfig] = []
        seen: set[str] = set()
        skipped: list[str] = []
        for cfg in configs:
            name = str((cfg.params or {}).get("name", "")).strip()
            if not name:
                result.append(cfg)
                continue
            if name in seen:
                skipped.append(name)
                continue
            seen.add(name)
            result.append(cfg)
        if skipped:
            unique = sorted(set(skipped))
            preview = ", ".join(unique[:10]) + (", ..." if len(unique) > 10 else "")
            logger.warning("Skipped %d duplicate agent names (%d unique): %s",
                           len(skipped), len(unique), preview)
        return result

    # ------------------------------------------------------------------ #
    # Data loading
    # ------------------------------------------------------------------ #

    def _load_records(
        self, data_cfg: dict[str, Any], *, max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        source = data_cfg.get("source", "local_json")
        if source == "inline":
            records = data_cfg.get("records", [])
        elif source == "config_path":
            path = data_cfg.get("path")
            if not path:
                raise ValueError("config_path source requires a `path` field")
            records = self._extract_path(self._to_plain(self.config), path)
        elif source == "hf_dataset":
            records = self._load_hf_dataset(data_cfg, max_records=max_records)
        else:
            path = data_cfg.get("path")
            if not path:
                raise ValueError(f"{source} source requires a `path` field")
            with open(self._resolve_file_path(str(path))) as f:
                records = json.load(f)

        records = self._to_plain(records)
        if isinstance(records, dict):
            records = list(records.values()) if data_cfg.get("expand_values") else [records]
        if not isinstance(records, list):
            raise ValueError(f"Expected list of records, got {type(records).__name__}")
        return [r if isinstance(r, dict) else {"value": r} for r in records]

    def _load_hf_dataset(
        self, data_cfg: dict[str, Any], *, max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        dataset_name = data_cfg.get("dataset")
        split = data_cfg.get("split", "train")
        subset = data_cfg.get("subset")
        if not dataset_name:
            raise ValueError("hf_dataset source requires a `dataset` field")

        cache_file = self._hf_cache_path(dataset_name, split, subset)

        def _load_cache() -> list[dict[str, Any]]:
            if not self._safe_path_exists(cache_file):
                raise FileNotFoundError(cache_file)
            cache_size = cache_file.stat().st_size
            if cache_size > _HF_CACHE_MAX_READ_BYTES:
                raise ValueError(f"HF cache too large to read safely: {cache_file}")
            logger.info("Loading HF dataset from cache: %s", cache_file)
            with open(cache_file) as f:
                cached = json.load(f)
            return cached[:max_records] if max_records and len(cached) >= max_records else cached

        try:
            from datasets import load_dataset
        except ImportError as exc:
            try:
                return _load_cache()
            except (FileNotFoundError, ValueError) as cache_exc:
                raise ImportError(
                    "hf_dataset source requires `datasets` or a readable local cache. "
                    "Install: pip install datasets"
                ) from cache_exc

        try:
            ds = (
                load_dataset(dataset_name, subset, split=split)
                if subset
                else load_dataset(dataset_name, split=split)
            )
            records = list(ds)
            self._persist_hf_cache(dataset_name, split, subset, records)
            return records[:max_records] if max_records else records
        except Exception as exc:
            logger.warning("Falling back to HF cache for %s due to dataset load error: %s", dataset_name, exc)
            return _load_cache()

    # ------------------------------------------------------------------ #
    # HuggingFace cache
    # ------------------------------------------------------------------ #

    def _hf_cache_path(self, dataset_name: str, split: str, subset: str | None) -> Path:
        slug = f"{self._slugify(dataset_name)}__{self._slugify(subset) if subset else 'default'}__{self._slugify(split)}.json"
        pkg = _PACKAGE_ROOT / "scenarios" / str(self.config.scenario_name) / "input" / "personas" / ".hf_cache" / slug
        if self._safe_path_exists(pkg):
            return pkg
        top = _PROJECT_ROOT / "scenarios" / str(self.config.scenario_name) / "input" / "personas" / ".hf_cache" / slug
        return top if self._safe_path_exists(top) else pkg

    def _persist_hf_cache(
        self, dataset_name: str, split: str, subset: str | None, records: list[dict],
    ) -> None:
        pkg = _PACKAGE_ROOT / "scenarios" / str(self.config.scenario_name)
        top = _PROJECT_ROOT / "scenarios" / str(self.config.scenario_name)
        base = pkg if pkg.is_dir() else top
        cache_dir = base / "input" / "personas" / ".hf_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        slug = f"{self._slugify(dataset_name)}__{self._slugify(subset) if subset else 'default'}__{self._slugify(split)}.json"
        with open(cache_dir / slug, "w") as f:
            json.dump(records, f, indent=2, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------ #
    # Path resolution
    # ------------------------------------------------------------------ #

    def _scenario_paths(self, path_str: str) -> list[Path]:
        try:
            raw = Path(path_str)
        except (TypeError, ValueError, OSError):
            return []
        if raw.is_absolute():
            return [raw]
        scenario = str(self.config.scenario_name)
        pkg_scenario = _PACKAGE_ROOT / "scenarios" / scenario
        top_scenario = _PROJECT_ROOT / "scenarios" / scenario
        subdirs = ["", "input", "input/personas", "input/news_data"]
        candidates = [raw, _PACKAGE_ROOT / raw]
        for base in (pkg_scenario, top_scenario):
            for sub in subdirs:
                candidates.append(base / sub / raw if sub else base / raw)
        return candidates

    def _resolve_file_path(self, path_str: str) -> Path:
        for c in self._scenario_paths(path_str):
            if isinstance(c, Path) and self._safe_path_exists(c):
                return c
        raise FileNotFoundError(f"Unable to resolve path: {path_str}")

    # ------------------------------------------------------------------ #
    # Memory loading
    # ------------------------------------------------------------------ #

    def _load_memories(self, value: Any) -> list[str]:
        value = self._to_plain(value)
        if value is None:
            return []
        if isinstance(value, list):
            merged: list[str] = []
            for item in value:
                merged.extend(self._load_memories(item))
            return merged
        if isinstance(value, dict):
            if path := value.get("path"):
                return self._load_memories(str(self._resolve_file_path(str(path))))
            return self._normalize_memories(value.get("text"))
        if not isinstance(value, str):
            return self._normalize_memories(value)
        # Check if it's a file path.
        for c in self._scenario_paths(value):
            if self._safe_path_exists(c):
                if str(c).endswith(".json"):
                    with open(c) as f:
                        return self._normalize_memories(json.load(f))
                with open(c) as f:
                    return [l.strip() for l in f if l.strip()]
        return self._normalize_memories(value)

    # ------------------------------------------------------------------ #
    # Static helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_plain(data: Any) -> Any:
        if isinstance(data, (DictConfig, ListConfig)):
            try:
                return OmegaConf.to_container(data, resolve=True)
            except Exception:
                return OmegaConf.to_container(data, resolve=False)
        return data

    @staticmethod
    def _normalize_memories(memories: Any) -> list[str]:
        if memories is None:
            return []
        if isinstance(memories, str):
            lines = [l.strip() for l in memories.splitlines() if l.strip()]
            return lines or ([memories.strip()] if memories.strip() else [])
        if isinstance(memories, list):
            return [str(x).strip() for x in memories if str(x).strip()]
        return [str(memories).strip()] if str(memories).strip() else []

    @staticmethod
    def _coerce_text(value: Any, *, joiner: str = "\n") -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return joiner.join(str(x).strip() for x in value if str(x).strip())
        return str(value).strip()

    @staticmethod
    def _extract_path(record: Any, dotted_path: str) -> Any:
        if dotted_path is None:
            return None
        if not isinstance(dotted_path, str):
            return dotted_path
        current = record
        for chunk in dotted_path.split("."):
            if isinstance(current, Mapping):
                if chunk in current:
                    current = current[chunk]
                else:
                    lowered = {str(k).lower(): k for k in current if isinstance(k, str)}
                    key = lowered.get(chunk.lower())
                    current = current[key] if key is not None else None
            elif isinstance(current, list) and chunk.isdigit():
                idx = int(chunk)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None
            if current is None:
                return None
        return current

    def _resolve_source(self, record: Any, spec: Any) -> Any:
        """Resolve a field_map source — dot-path or ``"{field1}\n{field2}"`` template."""
        if not isinstance(spec, str) or "{" not in spec:
            return self._extract_path(record, spec)
        def _sub(m: re.Match) -> str:
            v = self._extract_path(record, m.group(1).strip())
            if v is None:
                return ""
            return "\n".join(str(x).strip() for x in v) if isinstance(v, list) else str(v)
        return re.sub(r"\{([^{}]+)\}", _sub, spec)

    @staticmethod
    def _slugify(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")

    @staticmethod
    def _derive_name(context: str, words: int = 2) -> str:
        tokens = re.findall(r"[A-Za-z0-9']+", context or "")
        return " ".join(tokens[:max(1, words)]) if tokens else ""

    @staticmethod
    def _safe_path_exists(candidate: Any) -> bool:
        try:
            return bool(getattr(candidate, "exists", lambda: False)())
        except OSError:
            return False
