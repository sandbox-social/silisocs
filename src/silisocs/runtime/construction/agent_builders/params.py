"""Agent parameter mapping for persona-pipeline records."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from silisocs.runtime.construction.agent_builders.common import (
    coerce_text,
    derive_name,
    extract_path,
    normalize_memories,
    resolve_source,
)


def build_agent_params(
    record: dict,
    idx: int,
    class_name: str,
    field_map: dict,
    default_params: dict,
    class_params: dict,
    mem_field: str | None,
    sim_role: str,
    class_path: str,
    shared: list[str],
    news_posts: dict[str, str] | None,
    class_model: Any,
    *,
    resolve_file_path: Callable[[str], Path],
    derive_name_from_context: bool = False,
    name_words: int = 2,
    name_suffix_index: int = 0,
) -> dict[str, Any]:
    """Build one runtime agent params dict from one persona record."""
    mapped: dict[str, Any] = {}
    for target, source in field_map.items():
        value = resolve_source(record, source)
        if target in {"name", "context", "style", "goal", "bio", "seed_post"}:
            value = coerce_text(value, joiner=" " if target == "name" else "\n")
        mapped[target] = value

    params: dict[str, Any] = {}
    params.update(default_params)
    params.update(class_params)
    params.update({k: v for k, v in mapped.items() if v is not None})

    context = coerce_text(params.get("context"))
    if not context:
        fields = (
            ", ".join(sorted(str(k) for k in record))
            if isinstance(record, Mapping)
            else type(record).__name__
        )
        raise ValueError(
            f"Class `{class_name}` record {idx} missing `context` "
            f"(field_map.context={field_map.get('context')!r}). Fields: {fields}"
        )

    name = coerce_text(params.get("name"), joiner=" ")
    if not name and derive_name_from_context:
        name = derive_name(context, words=name_words)
    if not name:
        fields = (
            ", ".join(sorted(str(k) for k in record))
            if isinstance(record, Mapping)
            else type(record).__name__
        )
        raise ValueError(
            f"Class `{class_name}` record {idx} missing `name` "
            "(the builder must map or derive a unique Agent Name) "
            f"(field_map.name={field_map.get('name')!r}). Fields: {fields}"
        )
    if name_suffix_index > 0:
        # Recycled persona pass: disambiguate the repeated name (`Alex` -> `Alex 2`).
        name = f"{name} {name_suffix_index + 1}"

    if "specific_memories" not in params and mem_field:
        params["specific_memories"] = extract_path(record, mem_field)
    params["specific_memories"] = normalize_memories(params.get("specific_memories", []))

    if news_posts is not None:
        params["posts"] = news_posts

    params["name"] = name
    params["context"] = context
    params["sim_role"] = {"name": sim_role, "module_path": class_path}
    params["style"] = coerce_text(params.get("style", ""))
    params["seed_post"] = coerce_text(params.get("seed_post", ""))
    params["bio"] = coerce_text(params.get("bio", ""))

    plan_file = str(params.get("fixed_action_plan_file", "") or "").strip()
    if plan_file:
        params["fixed_action_plan_file"] = str(resolve_file_path(plan_file))

    goal = params.get("goal")
    params["goal"] = coerce_text(goal) if goal is not None else None
    if shared:
        params["shared_memories"] = shared

    # Per-class `model` may be a scalar name (today) or a full LLM block (dict).
    # A per-agent field_map name (mapped['model']) wins for the name but keeps the
    # class block's other override fields when present.
    mapped_model = mapped.get("model")
    if mapped_model and isinstance(class_model, dict):
        params["model"] = {**class_model, "name": str(mapped_model)}
    elif isinstance(class_model, dict) and not mapped_model:
        params["model"] = dict(class_model)
    else:
        model_name = mapped_model or class_model
        if model_name:
            params["model"] = {"name": str(model_name)}

    return params
