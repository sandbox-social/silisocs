"""Per-instance LLM resolution and dedup for runtime construction.

Each runtime instance's *effective* model config is the global ``sim.llm`` layered
with that instance's optional per-class ``model`` override. Instances that resolve
to a byte-identical effective config share one built ``LanguageModel`` (so a
no-override run builds exactly one model). This module owns that resolution +
dedup so the runner entrypoint stays a thin orchestrator.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.language_models import LanguageModel, select_large_language_model
from silisocs.runtime.model_fields import MODEL_FIELDS

# MODEL_FIELDS are the fields that make up a fully-resolved (effective) per-instance
# LLM config: two instances share a built model iff these fields are byte-identical,
# keeping the resolver, dedup signature, and validators in lockstep.


def build_global_llm_config(llm_cfg: Any) -> dict[str, Any]:
    """Extract the global ``sim.llm`` block into the effective-config baseline dict.

    Reads exactly ``MODEL_FIELDS`` off the resolved ``sim.llm`` config (with
    ``extra_kwargs`` unwrapped from OmegaConf) so the global baseline can never omit a
    field the per-instance resolver expects — the drift the single ``MODEL_FIELDS``
    source exists to prevent. Per-field coercion (temperature float, ``disabled`` bool,
    empty ``api_base``/``api_key`` -> None) is applied by ``_effective_model_config``
    when overrides layer on top, so this stays a plain, drift-proof field walk.
    """
    baseline: dict[str, Any] = {field: getattr(llm_cfg, field, None) for field in MODEL_FIELDS}
    raw_extra = baseline.get("extra_kwargs") or {}
    baseline["extra_kwargs"] = (
        OmegaConf.to_container(raw_extra, resolve=True)
        if isinstance(raw_extra, DictConfig)
        else dict(raw_extra)
    )
    return baseline


def _effective_model_config(
    global_llm: Mapping[str, Any], override: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Resolve a per-instance LLM config by layering ``override`` over the global.

    Each field falls back to the global ``sim.llm`` value when the override omits
    it (or leaves it empty for ``name``). Scalar per-agent model names already
    arrive as ``{'name': ...}`` so only the seven known fields are consulted.
    """
    override = override or {}
    eff: dict[str, Any] = {field: global_llm.get(field) for field in MODEL_FIELDS}
    for field in MODEL_FIELDS:
        if field in override and override[field] is not None:
            eff[field] = override[field]
    if not eff.get("name"):
        eff["name"] = global_llm.get("name")
    temp_val = eff.get("temperature")
    eff["temperature"] = 0.5 if temp_val is None else float(temp_val)
    eff["disabled"] = bool(eff.get("disabled", False))
    eff["api_base"] = eff.get("api_base") or None
    eff["api_key"] = eff.get("api_key") or None
    eff["extra_kwargs"] = dict(eff.get("extra_kwargs") or {})
    return eff


def _effective_model_key(eff: Mapping[str, Any]) -> str:
    """Stable signature for an effective LLM config; api_key is hashed, never embedded raw."""
    normalized = {field: eff.get(field) for field in MODEL_FIELDS}
    api_key = normalized.get("api_key")
    if api_key:
        normalized["api_key"] = "sha256:" + hashlib.sha256(str(api_key).encode()).hexdigest()[:16]
    return f"{eff.get('name')}::{json.dumps(normalized, sort_keys=True, default=str)}"


def build_deduped_models(
    instances: Sequence[Any],
    global_llm: Mapping[str, Any],
    prompts_file: Any,
) -> tuple[dict[str, LanguageModel], dict[str, str], LanguageModel | None]:
    """Build one model per distinct effective config and map each instance to it.

    Returns ``(models, object_to_model, default_model)`` where ``models`` is keyed
    by effective-config signature, ``object_to_model`` maps instance name -> key,
    and ``default_model`` is the no-override (global) model reused for runtime init.
    """
    models: dict[str, LanguageModel] = {}
    object_to_model: dict[str, str] = {}

    def _build_for_effective(eff: Mapping[str, Any]) -> str:
        key = _effective_model_key(eff)
        if key not in models:
            models[key] = select_large_language_model(
                eff["name"],
                prompts_file,
                True,
                disable_language_model=eff["disabled"],
                api_base=eff["api_base"],
                api_key=eff["api_key"],
                temperature=float(eff["temperature"]),
                provider=eff["provider"],
                extra_kwargs=eff["extra_kwargs"],
            )
        return key

    for instance in instances:
        override = instance.params.get("model")
        if not isinstance(override, Mapping):
            override = None
        eff = _effective_model_config(global_llm, override)
        object_to_model[instance.params["name"]] = _build_for_effective(eff)

    # Default runtime-init model is the global (no-override) effective config;
    # reuse the already-built object when an instance shares that config.
    default_model = models.get(_build_for_effective(_effective_model_config(global_llm, None)))
    return models, object_to_model, default_model
