"""Persona-pipeline agent builder.

An agent builder translates the composed ``agents`` config into
``AgentConfig`` records. The runtime construction layer owns object
instantiation; builders only decide *which* agent specs should exist.

This is the default config-to-agent-spec translator. Custom builders should
subclass :class:`AgentBuilder` and implement ``build_agent_configs``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from silisocs.runtime.construction.agent_builders.base import AgentBuilder
from silisocs.runtime.construction.agent_builders.common import (
    coerce_text,
    derive_name,
    extract_path,
    normalize_memories,
    resolve_source,
    safe_path_exists,
    to_plain,
    validate_unique_agent_names,
)
from silisocs.runtime.construction.agent_builders.fixed_actions import FixedActionBuilder
from silisocs.runtime.construction.agent_builders.params import build_agent_params
from silisocs.runtime.construction.agent_builders.records import RecordLoader
from silisocs.runtime.construction.specs import AgentConfig


class PersonaPipelineAgentBuilder(AgentBuilder):
    """Build runtime agent specs from ``agents.persona_pipeline``."""

    def __init__(self, agents_config: Any, params: Mapping[str, Any] | None = None):
        super().__init__(agents_config, params)
        self.records = RecordLoader(
            agents_config,
            scenario_name=str(self.params.get("scenario_name", "default")),
            project_root=self.params.get("project_root"),
        )
        self.fixed_actions = FixedActionBuilder(agents_config, self.records.resolve_file_path)

    def build_agent_configs(self) -> list[AgentConfig]:
        """Build all agent specs from the class-based persona pipeline."""
        pipeline = getattr(self.config, "persona_pipeline", None)
        if pipeline and getattr(pipeline, "classes", None):
            return validate_unique_agent_names(self._build_from_classes())
        raise ValueError("Scenario must define persona_pipeline.classes.")

    def load_news_data(self, news_file: str) -> dict[str, Any]:
        """Load news headlines and image metadata from a scenario JSON file."""
        with open(self._resolve_file_path(f"{news_file}.json")) as f:
            return json.load(f)

    def _build_from_classes(self) -> list[AgentConfig]:
        pipeline_cfg = self._to_plain(self.config.persona_pipeline)
        defaults = pipeline_cfg.get("defaults", {})
        classes = pipeline_cfg.get("classes", {})
        fixed_action_sets = self._load_fixed_action_sets()

        default_params = defaults.get("params", {}) or {}
        default_field_map = defaults.get("field_map", {}) or {}
        default_mem_field = defaults.get("specific_memories_field")
        default_shared = self._load_memories(defaults.get("shared_memories", []))

        agents: list[AgentConfig] = []
        for class_name, class_cfg_raw in classes.items():
            agents.extend(
                self._build_class(
                    class_name,
                    class_cfg_raw or {},
                    default_params,
                    default_field_map,
                    default_mem_field,
                    default_shared,
                    fixed_action_sets,
                )
            )
        return agents

    def _build_class(
        self,
        class_name: str,
        class_cfg: dict,
        default_params: dict,
        default_field_map: dict,
        default_mem_field: str | None,
        default_shared: list[str],
        fixed_action_sets: dict[str, list[dict[str, Any]]],
    ) -> list[AgentConfig]:
        data_cfg = class_cfg.get("data", {})
        count = class_cfg.get("count")
        records = (
            self._load_records(data_cfg, max_records=int(count) if count is not None else None)
            if data_cfg
            else [{}]
        )
        if count is not None:
            records = records[: int(count)]

        class_path = str(class_cfg.get("class_path", "") or "").strip()
        if not class_path:
            raise ValueError(f"Class `{class_name}` must define `class_path`")

        class_compat = self._normalize_compat(class_name, class_cfg.get("compat"))
        sim_role = class_cfg.get("sim_role_name", class_name)
        class_params = dict(class_cfg.get("params", {}) or {})
        class_flow_tag = str(class_cfg.get("flow_tag", "") or "").strip()
        if class_flow_tag:
            class_params.setdefault("flow_tag", class_flow_tag)

        shared = list(default_shared)
        for memory in self._load_memories(class_cfg.get("shared_memories", [])):
            if memory not in shared:
                shared.append(memory)

        field_map = {**default_field_map, **(class_cfg.get("field_map", {}) or {})}
        mem_field = class_cfg.get("specific_memories_field", default_mem_field)
        news_posts = self._load_news_posts(class_name, class_cfg)
        class_model = class_cfg.get("model") or default_params.get("model")
        fixed_action_cfg = class_cfg.get("fixed_action") if isinstance(class_cfg, Mapping) else None
        derive_name_from_context = self._should_derive_name(class_cfg, data_cfg)
        name_words = int(class_cfg.get("name_from_context_words", 2) or 2)

        agents: list[AgentConfig] = []
        for idx, record in enumerate(records, start=1):
            params = build_agent_params(
                record,
                idx,
                class_name,
                field_map,
                default_params,
                class_params,
                mem_field,
                sim_role,
                class_path,
                shared,
                news_posts,
                class_model,
                resolve_file_path=self._resolve_file_path,
                derive_name_from_context=derive_name_from_context,
                name_words=name_words,
            )
            self._attach_fixed_action(
                params,
                class_path=class_path,
                class_cfg=fixed_action_cfg,
                fixed_action_sets=fixed_action_sets,
                record=record,
                sim_role=sim_role,
            )
            agents.append(
                AgentConfig(class_path=class_path, params=params, compat=class_compat or None)
            )
        return agents

    def _should_derive_name(
        self, class_cfg: Mapping[str, Any], data_cfg: Mapping[str, Any]
    ) -> bool:
        """Return whether this class intentionally derives names from context."""
        if bool(class_cfg.get("derive_name_from_context", False)):
            return True
        if str(data_cfg.get("source", "")).strip().lower() != "hf_dataset":
            return False
        dataset = str(data_cfg.get("dataset", "")).strip().lower()
        return dataset == "nvidia/nemotron-personas-usa"

    def _normalize_compat(self, class_name: str, value: Any) -> str:
        compat = str(value or "").strip().lower()
        if compat and compat != "concordia":
            raise ValueError(
                f"Class `{class_name}` has unsupported compat value `{compat}`. "
                "Supported value: concordia."
            )
        return compat

    def _load_news_posts(self, class_name: str, class_cfg: dict[str, Any]) -> dict[str, str] | None:
        if not bool(class_cfg.get("use_news_file_posts", False)):
            return None
        news_file = getattr(self.config.data, "news_file", None)
        if not news_file:
            raise ValueError(
                f"Class `{class_name}` requested news posts but data.news_file is unset."
            )
        include_images = bool(class_cfg.get("include_news_images", False))
        return {
            headline: (content[0] if include_images and isinstance(content, list) else "")
            for headline, content in self.load_news_data(str(news_file)).items()
        }

    def _attach_fixed_action(
        self,
        params: dict[str, Any],
        *,
        class_path: str,
        class_cfg: Any,
        fixed_action_sets: dict[str, list[dict[str, Any]]],
        record: Mapping[str, Any],
        sim_role: str,
    ) -> None:
        fixed_action = self._build_fixed_action_config(
            class_cfg=class_cfg,
            fixed_action_sets=fixed_action_sets,
            render_context={
                **record,
                "name": params.get("name", ""),
                "context": params.get("context", ""),
                "sim_role": sim_role,
            },
        )
        if fixed_action is None:
            return
        if class_path == "silisocs.agents.fixed.FixedAgent":
            params["fixed_action_plan"] = self._normalize_fixed_action_plan(
                fixed_action.get("actions", [])
            )
            exhaustion = str(fixed_action.get("on_exhaustion", "")).strip().lower()
            if exhaustion in {"finish", "finished"}:
                params["emit_finished_on_episode_end"] = True
        else:
            params["fixed_action"] = fixed_action

    # Compatibility wrappers for custom builder subclasses/tests that reuse pieces.
    def _load_fixed_action_sets(self) -> dict[str, list[dict[str, Any]]]:
        return self.fixed_actions.load_fixed_action_sets()

    def _parse_fixed_action_sets(self, data: Any) -> dict[str, list[dict[str, Any]]]:
        return self.fixed_actions.parse_fixed_action_sets(data)

    def _build_fixed_action_config(
        self,
        *,
        class_cfg: Any,
        fixed_action_sets: dict[str, list[dict[str, Any]]],
        render_context: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        return self.fixed_actions.build_fixed_action_config(
            class_cfg=class_cfg,
            fixed_action_sets=fixed_action_sets,
            render_context=render_context,
        )

    def _normalize_fixed_action_plan(self, actions: Any) -> dict[int, list[dict[str, Any]]]:
        return self.fixed_actions.normalize_fixed_action_plan(actions)

    def _render_template(self, value: Any, context: Mapping[str, Any]) -> Any:
        return self.fixed_actions.render_template(value, context)

    def _load_records(
        self,
        data_cfg: dict[str, Any],
        *,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.records.load_records(data_cfg, max_records=max_records)

    def _load_hf_dataset(
        self,
        data_cfg: dict[str, Any],
        *,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.records.load_hf_dataset(data_cfg, max_records=max_records)

    def _materialize_hf_records(self, ds: Any, *, max_records: int | None) -> list[dict[str, Any]]:
        return self.records.materialize_hf_records(ds, max_records=max_records)

    def _load_csv(self, path: str, *, max_records: int | None = None) -> list[dict[str, Any]]:
        return self.records.load_csv(path, max_records=max_records)

    def _load_jsonl(self, path: str, *, max_records: int | None = None) -> list[dict[str, Any]]:
        return self.records.load_jsonl(path, max_records=max_records)

    def _scenario_paths(self, path_str: str) -> list[Path]:
        return self.records.scenario_paths(path_str)

    def _resolve_file_path(self, path_str: str) -> Path:
        return self.records.resolve_file_path(path_str)

    def _load_memories(self, value: Any) -> list[str]:
        return self.records.load_memories(value)

    _to_plain = staticmethod(to_plain)
    _normalize_memories = staticmethod(normalize_memories)
    _coerce_text = staticmethod(coerce_text)
    _extract_path = staticmethod(extract_path)
    _resolve_source = staticmethod(resolve_source)
    _derive_name = staticmethod(derive_name)
    _safe_path_exists = staticmethod(safe_path_exists)
