"""Persona-pipeline agent builder.

An agent builder translates the composed ``agents`` config into
``AgentConfig`` records. The runtime construction layer owns object
instantiation; builders only decide *which* agent specs should exist.

This is the default config-to-agent-spec translator. Custom builders should
subclass :class:`AgentBuilder` and implement ``build_agent_configs``.
"""

from __future__ import annotations

import json
import logging
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
from silisocs.runtime.model_fields import MODEL_FIELDS

_LOGGER = logging.getLogger(__name__)

# Every key this builder reads off `agents.persona_pipeline.classes.<class>`. It is
# the single source of truth for the config validator (`runtime.configuration.
# validation`), so an unknown sub-key — a typo like `flow_tags:` — fails the run
# instead of being silently ignored. Extend it in the same commit that teaches
# `_build_class` to read a new key.
PERSONA_CLASS_KEYS: frozenset[str] = frozenset(
    {
        "class_path",
        "compat",
        "count",
        "data",
        "derive_name_from_context",
        "field_map",
        "fixed_action",
        "flow_tag",
        "include_news_images",
        "model",
        "name_from_context_words",
        "params",
        "shared_memories",
        "sim_role_name",
        "specific_memories_field",
        "use_news_file_posts",
    }
)

# Per-class `model` block fields that override the matching global `sim.llm` field.
_ALLOWED_MODEL_KEYS = frozenset(MODEL_FIELDS)
# String-or-None subset of _ALLOWED_MODEL_KEYS, type-checked separately
# (temperature/disabled/extra_kwargs have their own dedicated checks below).
_STR_OR_NONE_MODEL_KEYS = ("name", "provider", "api_base", "api_key")


def _validate_class_model(class_name: str, class_model: Any) -> None:
    """Fail fast on a malformed per-class ``model`` block.

    A scalar name (or ``None``) is the legacy form and always valid. A mapping is
    the per-class LLM override block: reject unknown keys and type-check the known
    ones so misconfiguration surfaces at build time, naming the offending class.
    """
    if not isinstance(class_model, Mapping):
        return
    unknown = sorted(str(k) for k in class_model if k not in _ALLOWED_MODEL_KEYS)
    if unknown:
        raise ValueError(
            f"Class `{class_name}` model block has unknown key(s) {unknown}; "
            f"allowed keys: {sorted(_ALLOWED_MODEL_KEYS)}."
        )
    for key in _STR_OR_NONE_MODEL_KEYS:
        if (
            key in class_model
            and class_model[key] is not None
            and not isinstance(class_model[key], str)
        ):
            raise ValueError(
                f"Class `{class_name}` model.{key} must be a string or null, "
                f"got {type(class_model[key]).__name__}."
            )
    if "temperature" in class_model and class_model["temperature"] is not None:
        try:
            float(class_model["temperature"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Class `{class_name}` model.temperature must be float-coercible, "
                f"got {class_model['temperature']!r}."
            ) from exc
    if "disabled" in class_model and not isinstance(class_model["disabled"], (bool, int)):
        raise ValueError(
            f"Class `{class_name}` model.disabled must be bool-like, "
            f"got {type(class_model['disabled']).__name__}."
        )
    if (
        "extra_kwargs" in class_model
        and class_model["extra_kwargs"] is not None
        and not isinstance(class_model["extra_kwargs"], Mapping)
    ):
        raise ValueError(
            f"Class `{class_name}` model.extra_kwargs must be a mapping, "
            f"got {type(class_model['extra_kwargs']).__name__}."
        )


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
        raise ValueError(
            "No agent classes defined: `agents.persona_pipeline.classes` is missing "
            "or empty, so no agents can be built (there is no implicit fallback). "
            "Define at least one class, e.g.\n"
            "  persona_pipeline:\n"
            "    classes:\n"
            "      user:\n"
            "        count: ${num_agents}\n"
            "        class_path: silisocs.agents.native.NativeAgent\n"
            "        data: {source: inline, records: [{name: Alex, persona: ...}]}\n"
            "        field_map: {name: name, context: persona}\n"
            "or run with the bundled default agents config (`agents=default`), which "
            "ships ready-made personas that scale to any num_agents."
        )

    def load_news_data(self, news_file: str) -> dict[str, Any]:
        """Load news headlines and image metadata from a world JSON file."""
        with open(self._resolve_file_path(f"{news_file}.json")) as f:
            return json.load(f)

    def _build_from_classes(self) -> list[AgentConfig]:
        pipeline_cfg = to_plain(self.config.persona_pipeline, where="agents.persona_pipeline")
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
        count = self._resolve_count(class_name, class_cfg.get("count"))
        records = self._load_records(data_cfg, max_records=count) if data_cfg else [{}]
        base_record_count = len(records)
        if count is not None:
            records = self._fit_records_to_count(
                records, count, class_name=class_name, allow_cycle=bool(data_cfg)
            )

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
        _validate_class_model(class_name, class_model)
        fixed_action_cfg = class_cfg.get("fixed_action") if isinstance(class_cfg, Mapping) else None
        derive_name_from_context = self._should_derive_name(class_cfg, data_cfg)
        name_words = int(class_cfg.get("name_from_context_words", 2) or 2)

        agents: list[AgentConfig] = []
        for idx, record in enumerate(records, start=1):
            # When personas are recycled to satisfy a larger `count`, each extra
            # pass over the base records gets a numbered suffix so agent names
            # stay unique (e.g. `Alex` -> `Alex 2`).
            copy_index = (idx - 1) // base_record_count if base_record_count else 0
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
                name_suffix_index=copy_index,
            )
            self._attach_fixed_action(
                params,
                class_path=class_path,
                class_cfg=fixed_action_cfg,
                fixed_action_sets=fixed_action_sets,
                record=record,
                sim_role=sim_role,
                class_name=class_name,
            )
            agents.append(
                AgentConfig(class_path=class_path, params=params, compat=class_compat or None)
            )
        return agents

    @staticmethod
    def _resolve_count(class_name: str, raw: Any) -> int | None:
        """Validate a class's ``count``, which decides how many agents it builds.

        ``None`` means "one agent per available record". Anything else must be a
        non-negative integer: a negative value used to reach ``records[:count]``
        and silently drop agents off the end of the list.
        """
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError(
                f"Class `{class_name}` count must be a non-negative integer; got {raw!r}."
            )
        try:
            count = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Class `{class_name}` count must be a non-negative integer; got {raw!r}."
            ) from exc
        if count < 0:
            raise ValueError(
                f"Class `{class_name}` count must be a non-negative integer; got {count}."
            )
        return count

    def _fit_records_to_count(
        self,
        records: list[dict[str, Any]],
        count: int,
        *,
        class_name: str,
        allow_cycle: bool,
    ) -> list[dict[str, Any]]:
        """Fit a record list to exactly ``count`` entries.

        When ``count`` is at most the number of available records the list is
        simply truncated. When ``count`` exceeds the available records and
        cycling is allowed, the base records are repeated (the build loop adds a
        numbered suffix to keep names unique) so any ``num_agents`` works
        out-of-the-box instead of silently capping at the record count.

        When cycling is NOT possible the shortfall raises: returning the short
        list would build fewer agents than the scenario asked for, and nothing
        downstream would ever say so.
        """
        if count <= len(records):
            return records[:count]
        if not records:
            raise ValueError(
                f"Class `{class_name}` requests {count} agent(s) but its data source "
                "produced no records. Check the class's `data` block."
            )
        if not allow_cycle:
            raise ValueError(
                f"Class `{class_name}` requests {count} agent(s) but declares no `data` "
                "block, so only 1 agent can be built. Add a `data` source with at least "
                f"{count} record(s) (or one record, which is recycled with numbered "
                "name suffixes), or set count: 1."
            )
        base = len(records)
        _LOGGER.warning(
            "Class `%s` requested %d agents but only %d persona record(s) are "
            "available; recycling personas with numbered suffixes to fill the "
            "remaining %d (e.g. `Alex` -> `Alex 2`). Supply more records via "
            "data.source (csv/jsonl/hf_dataset) for fully distinct personas.",
            class_name,
            count,
            base,
            count - base,
        )
        return [records[idx % base] for idx in range(count)]

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
        class_name: str = "",
    ) -> None:
        where = (
            f"agents.persona_pipeline.classes.{class_name}.fixed_action"
            if class_name
            else "fixed_action"
        )
        fixed_action = self.fixed_actions.build_fixed_action_config(
            class_cfg=class_cfg,
            fixed_action_sets=fixed_action_sets,
            render_context={
                **record,
                "name": params.get("name", ""),
                "context": params.get("context", ""),
                "sim_role": sim_role,
            },
            where=where,
        )
        if fixed_action is None:
            return
        if class_path == "silisocs.agents.fixed.FixedAgent":
            params["fixed_action_plan"] = self.fixed_actions.normalize_fixed_action_plan(
                fixed_action.get("actions", []), where=f"{where}.actions"
            )
            exhaustion = str(fixed_action.get("on_exhaustion", "")).strip().lower()
            if exhaustion in {"finish", "finished"}:
                params["emit_finished_on_episode_end"] = True
        else:
            params["fixed_action"] = fixed_action

    # Named entry points into the extracted loaders: the documented subclass API
    # (docs/building_agents.md) plus the aliases this builder itself calls.
    # Anything a subclass needs beyond these is reachable on `self.records` /
    # `self.fixed_actions` directly — do not add a forwarder without a caller.
    def _load_fixed_action_sets(self) -> dict[str, list[dict[str, Any]]]:
        return self.fixed_actions.load_fixed_action_sets()

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

    def _load_records(
        self,
        data_cfg: dict[str, Any],
        *,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.records.load_records(data_cfg, max_records=max_records)

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
