"""Tests for full per-class LLM block overrides.

Historically only the model *name* was per-instance; temperature, provider,
api_base, api_key, disabled and extra_kwargs were global. A per-class ``model``
may now be a scalar name (legacy) or a full mapping whose fields override the
matching global ``sim.llm`` field and fall back to the global when unset.

Two surfaces are exercised:

* the agent-builder side (``build_agent_params`` + ``PersonaPipelineAgentBuilder``)
  that materializes ``params['model']`` and validates the block; and
* the session model-creation dedup logic (the ``_effective_model_config`` /
  ``_effective_model_key`` helpers plus the build loop) that turns effective
  configs into a deduped ``models`` map and an ``object_to_model`` assignment.

The session loop is inlined in ``main()``; ``_build_models`` below mirrors it
verbatim so the dedup/fallback invariants can be asserted without invoking Hydra.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from omegaconf import DictConfig, OmegaConf

from silisocs.runtime.construction.agent_builders import PersonaPipelineAgentBuilder
from silisocs.runtime.construction.agent_builders.params import build_agent_params
from silisocs.runtime.execution.session import (
    _effective_model_config,
    _effective_model_key,
)
from silisocs.runtime.language_models import LanguageModel, select_large_language_model
from silisocs.runtime.language_models.base import NoLanguageModel

# A provider that builds with no real network/API key but records temperature and
# model name on the instance, so effective configs can be inspected post-build.
_GLOBAL_LLM: dict[str, Any] = {
    "name": "global-model",
    "temperature": 0.5,
    "provider": "openai_compatible",
    "api_base": "http://global.example/v1",
    "api_key": None,
    "disabled": False,
    "extra_kwargs": {},
}

_PROMPTS_FILE = "/tmp/per_class_model_override_prompts.jsonl"


class _Instance:
    """Minimal stand-in for an AgentConfig/GameMasterConfig in the model loop."""

    def __init__(self, name: str, model: Any = None):
        self.params: dict[str, Any] = {"name": name}
        if model is not None:
            self.params["model"] = model


def _build_models(
    global_llm: Mapping[str, Any], instances: list[_Instance]
) -> tuple[dict[str, LanguageModel], dict[str, str], LanguageModel]:
    """Faithful replica of session.main()'s model_creation loop."""
    models: dict[str, LanguageModel] = {}
    object_to_model: dict[str, str] = {}

    def _build_for_effective(eff: Mapping[str, Any]) -> str:
        key = _effective_model_key(eff)
        if key not in models:
            models[key] = select_large_language_model(
                eff["name"],
                _PROMPTS_FILE,
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

    model = models.get(_build_for_effective(_effective_model_config(global_llm, None)))
    assert model is not None
    return models, object_to_model, model


def _builder(classes: dict[str, Any]) -> PersonaPipelineAgentBuilder:
    cfg: DictConfig = OmegaConf.create(
        {"scenario_name": "default", "persona_pipeline": {"classes": classes}}
    )
    return PersonaPipelineAgentBuilder(cfg)


def _native_class(model: Any = None, *, field_map: dict[str, str] | None = None) -> dict[str, Any]:
    cls: dict[str, Any] = {
        "count": 1,
        "class_path": "silisocs.agents.native.NativeAgent",
        "data": {
            "source": "inline",
            "records": [{"name": "Alex", "persona": "P.", "m": "rec-mdl"}],
        },
        "field_map": field_map or {"name": "name", "context": "persona"},
    }
    if model is not None:
        cls["model"] = model
    return cls


# ---------------------------------------------------------------------------
# 1) No override -> a single shared model; resolved fields equal the global.
# ---------------------------------------------------------------------------
def test_no_override_single_shared_model() -> None:
    instances = [_Instance("a"), _Instance("b"), _Instance("c")]
    models, object_to_model, _ = _build_models(_GLOBAL_LLM, instances)

    assert len(models) == 1
    keys = set(object_to_model.values())
    assert len(keys) == 1
    only_model = next(iter(models.values()))
    # Resolved temperature/provider == global.
    assert only_model._temperature == _GLOBAL_LLM["temperature"]  # type: ignore[attr-defined]
    assert only_model._model_name == _GLOBAL_LLM["name"]  # type: ignore[attr-defined]
    eff = _effective_model_config(_GLOBAL_LLM, None)
    assert eff["provider"] == "openai_compatible"
    assert eff["temperature"] == 0.5


# ---------------------------------------------------------------------------
# 2) Two classes, same name + different temperature -> two distinct models.
# ---------------------------------------------------------------------------
def test_same_name_different_temperature_yields_two_models() -> None:
    # Both instances share the model NAME but differ only in temperature; each
    # must get its own model (distinct effective key carrying its own temp).
    instances = [
        _Instance("hot", {"name": "shared", "temperature": 0.9}),
        _Instance("cold", {"name": "shared", "temperature": 0.1}),
    ]
    models, object_to_model, _ = _build_models(_GLOBAL_LLM, instances)

    assert object_to_model["hot"] != object_to_model["cold"]
    assert models[object_to_model["hot"]]._temperature == 0.9  # type: ignore[attr-defined]
    assert models[object_to_model["cold"]]._temperature == 0.1  # type: ignore[attr-defined]
    # Same name, so the only thing distinguishing them is temperature.
    assert (
        models[object_to_model["hot"]]._model_name  # type: ignore[attr-defined]
        == models[object_to_model["cold"]]._model_name  # type: ignore[attr-defined]
        == "shared"
    )


# ---------------------------------------------------------------------------
# 3) Scalar string model still materializes to {'name': ...}.
# ---------------------------------------------------------------------------
def test_scalar_model_name_still_works() -> None:
    agents = _builder({"user": _native_class("scalar-model")}).build_agent_configs()
    assert agents[0].params["model"] == {"name": "scalar-model"}

    # And the session loop resolves it against the global baseline.
    models, object_to_model, _ = _build_models(
        _GLOBAL_LLM, [_Instance("user", {"name": "scalar-model"})]
    )
    built = models[object_to_model["user"]]
    assert built._model_name == "scalar-model"  # type: ignore[attr-defined]
    assert built._temperature == _GLOBAL_LLM["temperature"]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 4) Per-field fallback: a class that sets only provider keeps global for the rest.
# ---------------------------------------------------------------------------
def test_partial_override_falls_back_per_field() -> None:
    agents = _builder(
        {"user": _native_class({"provider": "openai_compatible"})}
    ).build_agent_configs()
    block = agents[0].params["model"]
    assert block == {"provider": "openai_compatible"}

    eff = _effective_model_config(_GLOBAL_LLM, block)
    assert eff["provider"] == "openai_compatible"
    # Every other field falls back to the global baseline.
    assert eff["name"] == _GLOBAL_LLM["name"]
    assert eff["temperature"] == _GLOBAL_LLM["temperature"]
    assert eff["api_base"] == _GLOBAL_LLM["api_base"]
    assert eff["disabled"] == _GLOBAL_LLM["disabled"]
    assert eff["extra_kwargs"] == _GLOBAL_LLM["extra_kwargs"]
    # Same effective config as no-override -> shares the one model.
    assert _effective_model_key(eff) == _effective_model_key(
        _effective_model_config(_GLOBAL_LLM, None)
    )


# ---------------------------------------------------------------------------
# 5) The global default runtime model IS the global-key model, not a duplicate.
# ---------------------------------------------------------------------------
def test_default_runtime_model_reuses_global_object() -> None:
    instances = [_Instance("a"), _Instance("b", {"name": "other", "temperature": 0.9})]
    models, object_to_model, default_model = _build_models(_GLOBAL_LLM, instances)

    global_key = _effective_model_key(_effective_model_config(_GLOBAL_LLM, None))
    assert default_model is models[global_key]
    # The no-override instance maps to that same object.
    assert default_model is models[object_to_model["a"]]
    # No duplicate global model was built.
    assert len(models) == 2


# ---------------------------------------------------------------------------
# 6) field_map model name overrides the class block name, keeps class temperature.
# ---------------------------------------------------------------------------
def test_field_map_name_overrides_class_block_name() -> None:
    cls = _native_class(
        {"name": "block-model", "temperature": 0.2},
        field_map={"name": "name", "context": "persona", "model": "m"},
    )
    agents = _builder({"user": cls}).build_agent_configs()
    block = agents[0].params["model"]
    # Per-agent field_map name ('rec-mdl' from record key 'm') wins ...
    assert block["name"] == "rec-mdl"
    # ... but the class block's other fields survive.
    assert block["temperature"] == 0.2

    eff = _effective_model_config(_GLOBAL_LLM, block)
    assert eff["name"] == "rec-mdl"
    assert eff["temperature"] == 0.2


def test_build_agent_params_field_map_name_directly() -> None:
    params = build_agent_params(
        {"name": "Alex", "persona": "P.", "m": "rec-mdl"},
        1,
        "user",
        {"name": "name", "context": "persona", "model": "m"},
        {},
        {},
        None,
        "user",
        "silisocs.agents.native.NativeAgent",
        [],
        None,
        {"name": "block-model", "temperature": 0.2},
        resolve_file_path=lambda p: __import__("pathlib").Path(p),
    )
    assert params["model"] == {"name": "rec-mdl", "temperature": 0.2}


# ---------------------------------------------------------------------------
# 7) Unknown model key raises ValueError naming the class.
# ---------------------------------------------------------------------------
def test_unknown_model_key_raises_naming_class() -> None:
    with pytest.raises(ValueError, match="user") as excinfo:
        _builder({"user": _native_class({"name": "m", "frobnicate": True})}).build_agent_configs()
    assert "frobnicate" in str(excinfo.value)


def test_bad_temperature_type_raises() -> None:
    with pytest.raises(ValueError, match="temperature"):
        _builder({"user": _native_class({"temperature": "hot"})}).build_agent_configs()


# ---------------------------------------------------------------------------
# 8) class model={disabled: true} -> a disabled model for that class only.
# ---------------------------------------------------------------------------
def test_disabled_model_block_yields_no_language_model() -> None:
    agents = _builder({"muted": _native_class({"disabled": True})}).build_agent_configs()
    assert agents[0].params["model"] == {"disabled": True}

    instances = [
        _Instance("muted", {"disabled": True}),
        _Instance("live"),  # no override -> real provider
    ]
    models, object_to_model, _ = _build_models(_GLOBAL_LLM, instances)

    muted_model = models[object_to_model["muted"]]
    live_model = models[object_to_model["live"]]
    assert isinstance(muted_model, NoLanguageModel)
    assert not isinstance(live_model, NoLanguageModel)
    assert muted_model is not live_model
    assert len(models) == 2


# ---------------------------------------------------------------------------
# Backward-compat invariant: a no-mapping config calls the model factory with
# args identical to the legacy global-only path (one shared model).
# ---------------------------------------------------------------------------
def test_backward_compat_single_global_model_for_legacy_config() -> None:
    # Several agents, none declaring a mapping (some with a bare scalar name).
    instances = [_Instance("a"), _Instance("b"), _Instance("c")]
    models, _, _ = _build_models(_GLOBAL_LLM, instances)
    assert len(models) == 1


# ---------------------------------------------------------------------------
# Fix #1: the runtime initializer model is the GLOBAL no-override model, NOT the
# first-declared instance's model. When the FIRST instance carries a disabling /
# provider-changing override and a later instance has no override, the OLD
# default_model behavior (next(iter(models.values())) = first-inserted) would
# wrongly hand the initializer a disabled/wrong model. The new initializer is
# the global-keyed model, which must diverge from the first.
# ---------------------------------------------------------------------------
def test_initializer_model_is_global_not_first_declared_override() -> None:
    # First instance disables its model via a per-class block; a later instance
    # has no override and so resolves to the real global model.
    instances = [
        _Instance("muted_first", {"disabled": True}),
        _Instance("live_later"),  # no override -> global model
    ]
    models, object_to_model, initializer_model = _build_models(_GLOBAL_LLM, instances)

    # The OLD default_model behavior: first-inserted model in the dict. Because the
    # first instance was processed first and disables the model, this is the
    # NoLanguageModel that must NOT be used as the initializer.
    old_default_model = next(iter(models.values()))
    assert isinstance(old_default_model, NoLanguageModel)
    assert old_default_model is models[object_to_model["muted_first"]]

    # The NEW initializer model mirrors session.py: the global no-override config.
    global_key = _effective_model_key(_effective_model_config(_GLOBAL_LLM, None))
    assert initializer_model is models[global_key]
    # It is the live model the no-override instance resolved to ...
    assert initializer_model is models[object_to_model["live_later"]]
    # ... and is NOT a NoLanguageModel and NOT the first/override model.
    assert not isinstance(initializer_model, NoLanguageModel)
    assert initializer_model is not old_default_model
    # The two strategies genuinely diverge here (the whole point of the fix).
    assert old_default_model is not initializer_model


def test_initializer_model_diverges_when_first_override_changes_temperature() -> None:
    # Even a non-disabling override on the FIRST instance (e.g. a different
    # temperature) makes next(iter(...)) the wrong model; the global must win.
    instances = [
        _Instance("hot_first", {"temperature": 0.95}),
        _Instance("plain_later"),  # no override
    ]
    models, object_to_model, initializer_model = _build_models(_GLOBAL_LLM, instances)

    old_default_model = next(iter(models.values()))
    # First-inserted carries the overridden temperature, not the global's.
    assert old_default_model._temperature == 0.95  # type: ignore[attr-defined]
    assert old_default_model is models[object_to_model["hot_first"]]

    # The global initializer carries the global temperature and is a distinct object.
    assert initializer_model._temperature == _GLOBAL_LLM["temperature"]  # type: ignore[attr-defined]
    assert initializer_model is not old_default_model
    global_key = _effective_model_key(_effective_model_config(_GLOBAL_LLM, None))
    assert initializer_model is models[global_key]


# ---------------------------------------------------------------------------
# Fix #5: _effective_model_key hashes the api_key (never embeds it raw) yet two
# configs differing only by api_key still dedup to DISTINCT keys.
# ---------------------------------------------------------------------------
def test_effective_model_key_hashes_api_key_and_stays_distinct() -> None:
    raw_secret = "sk-super-secret-RAW-key-123456"
    global_with_key = dict(_GLOBAL_LLM)
    global_with_key["api_key"] = raw_secret

    eff = _effective_model_config(global_with_key, None)
    assert eff["api_key"] == raw_secret  # the effective config keeps the real key
    key = _effective_model_key(eff)

    # The raw secret never appears in the dedup signature; a sha256 marker does.
    assert raw_secret not in key
    assert "sha256:" in key

    # Two configs differing ONLY by api_key must still produce DISTINCT keys, so
    # dedup does not collapse them into one shared model.
    eff_a = _effective_model_config(global_with_key, {"api_key": "key-AAAA"})
    eff_b = _effective_model_config(global_with_key, {"api_key": "key-BBBB"})
    key_a = _effective_model_key(eff_a)
    key_b = _effective_model_key(eff_b)
    assert key_a != key_b
    assert "key-AAAA" not in key_a
    assert "key-BBBB" not in key_b
    assert "sha256:" in key_a and "sha256:" in key_b

    # No api_key -> no sha256 marker (and the None value is preserved verbatim).
    no_key = _effective_model_key(_effective_model_config(_GLOBAL_LLM, None))
    assert "sha256:" not in no_key


# ---------------------------------------------------------------------------
# Single source of truth for the LLM field set
# ---------------------------------------------------------------------------
def test_model_field_lists_share_one_source_of_truth() -> None:
    """The dedup/resolver field set and the per-class validator both derive from the
    single ``MODEL_FIELDS`` constant. This guards the silent-collision footgun: a
    field accepted by validation but missing from the dedup signature would collapse
    two genuinely-different configs onto one shared model.
    """
    from silisocs.runtime.construction.agent_builders.persona_pipeline import (
        _ALLOWED_MODEL_KEYS,
    )
    from silisocs.runtime.model_fields import MODEL_FIELDS

    # The session dedup signature iterates MODEL_FIELDS directly, and the per-class
    # `model` block allowlist is exactly MODEL_FIELDS, so adding a field there keeps
    # the resolver, dedup signature, and validator in lockstep.
    assert frozenset(MODEL_FIELDS) == _ALLOWED_MODEL_KEYS


def test_per_class_model_block_rejects_key_outside_model_fields() -> None:
    """A per-class `model` key not in MODEL_FIELDS is rejected at build time."""
    from silisocs.runtime.construction.agent_builders.persona_pipeline import (
        _validate_class_model,
    )
    from silisocs.runtime.model_fields import MODEL_FIELDS

    # A full, correctly-typed block covering exactly every MODEL_FIELDS key validates
    # (this also fails loudly if a field is ever added to MODEL_FIELDS without updating
    # the block, keeping the allowlist and the field set provably in sync).
    valid_block = {
        "name": "gpt-4o",
        "temperature": 0.3,
        "provider": "openai",
        "api_base": None,
        "api_key": None,
        "disabled": False,
        "extra_kwargs": {},
    }
    assert frozenset(valid_block) == frozenset(MODEL_FIELDS)
    _validate_class_model("influencer", valid_block)  # no raise

    # An unknown key (e.g. a future provider knob added to only one allowlist) raises.
    with pytest.raises(ValueError, match="unknown key"):
        _validate_class_model("influencer", {"name": "gpt-4o", "max_tokens": 128})
