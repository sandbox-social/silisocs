"""Every ``class_path``/provider seam builds through one strict helper.

`silisocs.runtime.class_loading.instantiate_with_supported_kwargs` is the single
implementation of "construct this configured target with these params". These tests
pin its contract (unknown key -> named config error, framework kwargs still
filterable, a target's own TypeError untouched) and one error path per seam that was
migrated onto it, so a future re-implementation at any one site fails here.
"""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.agents.base_agent import Agent
from silisocs.runtime.class_loading import (
    instantiate_with_supported_kwargs,
    supported_kwargs,
)
from silisocs.runtime.language_models.base import LanguageModel


class _Target:
    def __init__(self, alpha: int = 1, beta: str = "") -> None:
        self.alpha = alpha
        self.beta = beta


class _Open:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _Exploding:
    def __init__(self, alpha: int = 1) -> None:
        raise TypeError("target-internal failure")


def _factory(alpha: int = 1) -> _Target:
    return _Target(alpha=alpha)


# --- the shared helper -------------------------------------------------------


def test_unknown_kwarg_names_key_target_and_config_path() -> None:
    with pytest.raises(ValueError) as exc:
        instantiate_with_supported_kwargs(
            _Target, {"alpha": 2, "gamma": 3}, config_path="sim.somewhere.params"
        )
    message = str(exc.value)
    assert "'gamma'" in message
    assert "_Target" in message
    assert "sim.somewhere.params" in message
    assert "'alpha', 'beta'" in message  # supported params are listed


def test_plain_callable_target_is_supported() -> None:
    built = instantiate_with_supported_kwargs(_factory, {"alpha": 7})
    assert isinstance(built, _Target) and built.alpha == 7


def test_var_keyword_target_receives_everything() -> None:
    built = instantiate_with_supported_kwargs(_Open, {"anything": 1})
    assert built.kwargs == {"anything": 1}
    assert supported_kwargs(_Open) is None


def test_strict_keys_scopes_the_check_to_user_authored_params() -> None:
    # 'injected' is a framework kwarg the target need not declare: filtered, not fatal.
    built = instantiate_with_supported_kwargs(
        _Target, {"alpha": 4, "injected": object()}, strict_keys={"alpha"}
    )
    assert built.alpha == 4


def test_target_internal_type_error_is_not_disguised_as_a_config_error() -> None:
    with pytest.raises(TypeError, match="target-internal failure"):
        instantiate_with_supported_kwargs(_Exploding, {"alpha": 1})


# --- LLM providers -----------------------------------------------------------


class _NoExtraKwargsProvider(LanguageModel):
    """A custom provider that cannot carry request-body pass-through."""

    def __init__(self, *, model_name: str = "", temperature: float = 0.0) -> None:
        self.model_name = model_name
        self.temperature = temperature

    def sample_text(self, prompt: str, **kwargs: object) -> str:  # type: ignore[override]
        return "custom"


def _register(monkeypatch: pytest.MonkeyPatch, name: str, factory: object) -> None:
    from silisocs.runtime.language_models import registry

    patched = dict(registry._PROVIDERS)
    patched[name] = factory  # type: ignore[assignment]
    monkeypatch.setattr(registry, "_PROVIDERS", patched)


def test_provider_that_cannot_accept_a_set_field_fails_naming_provider_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from silisocs.runtime.language_models.factory import select_large_language_model

    _register(monkeypatch, "strictness_test_provider", _NoExtraKwargsProvider)
    with pytest.raises(ValueError) as exc:
        select_large_language_model(
            model_name="m",
            log_file="",
            debug_mode=False,
            provider="strictness_test_provider",
            extra_kwargs={"top_p": 0.1},
        )
    message = str(exc.value)
    assert "strictness_test_provider" in message
    assert "extra_kwargs" in message


def test_provider_still_ignores_unset_framework_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    # log_file/debug/api_base are framework-supplied; a provider may ignore them.
    from silisocs.runtime.language_models.factory import select_large_language_model

    _register(monkeypatch, "strictness_test_provider", _NoExtraKwargsProvider)
    model = select_large_language_model(
        model_name="m", log_file="p.jsonl", debug_mode=True, provider="strictness_test_provider"
    )
    assert isinstance(model, _NoExtraKwargsProvider)


def test_extra_kwargs_stay_an_open_pass_through_for_api_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # extra_kwargs is the request body: unknown keys are the API's business, not ours.
    from silisocs.runtime.language_models.factory import select_large_language_model

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = select_large_language_model(
        model_name="m",
        log_file="p.jsonl",
        debug_mode=False,
        provider="openai_compatible",
        api_base="http://localhost:9/v1",
        extra_kwargs={"reasoning_effort": "high", "totally_made_up": 1},
    )
    assert model._extra_kwargs == {"reasoning_effort": "high", "totally_made_up": 1}  # type: ignore[attr-defined]


def test_scripted_provider_extra_kwargs_are_constructor_params_and_are_strict() -> None:
    from silisocs.runtime.language_models.factory import select_large_language_model

    with pytest.raises(ValueError) as exc:
        select_large_language_model(
            model_name="m",
            log_file="",
            debug_mode=False,
            provider="scripted",
            extra_kwargs={"text_respons": "typo"},
        )
    message = str(exc.value)
    assert "text_respons" in message
    assert "scripted" in message

    model = select_large_language_model(
        model_name="m",
        log_file="",
        debug_mode=False,
        provider="scripted",
        extra_kwargs={"text_response": "ok"},
    )
    assert model.sample_text("hi") == "ok"  # type: ignore[attr-defined]


# --- checkpoint save / restore ----------------------------------------------


def test_checkpoint_save_built_in_rejects_unknown_param() -> None:
    from silisocs.runtime.checkpointing.save import build_checkpoint_save_strategy

    with pytest.raises(ValueError) as exc:
        build_checkpoint_save_strategy(
            OmegaConf.create({"built_in": "sharded", "params": {"objects_per_shrd": 10}})
        )
    assert "objects_per_shrd" in str(exc.value)
    assert "sim.checkpoint.save.params" in str(exc.value)


def test_checkpoint_save_monolithic_no_longer_swallows_params() -> None:
    from silisocs.runtime.checkpointing.save import build_checkpoint_save_strategy

    with pytest.raises(ValueError, match="objects_per_shard"):
        build_checkpoint_save_strategy(
            OmegaConf.create({"built_in": "monolithic_json", "params": {"objects_per_shard": 10}})
        )


def test_checkpoint_restore_built_in_rejects_unknown_param() -> None:
    from silisocs.runtime.checkpointing.restore import build_checkpoint_restore

    with pytest.raises(ValueError) as exc:
        build_checkpoint_restore(
            OmegaConf.create({"built_in": "social_action_event_replay", "params": {"bogus": True}})
        )
    assert "bogus" in str(exc.value)
    assert "sim.checkpoint.restore.params" in str(exc.value)


# --- engine policies ---------------------------------------------------------


def test_turn_policy_param_typo_names_the_config_block() -> None:
    from silisocs.simulation_engines.policies.factory import build_turn_policy

    with pytest.raises(ValueError) as exc:
        build_turn_policy({"built_in": "fixed_count", "params": {"conut": 3}})
    assert "conut" in str(exc.value)
    assert "sim.engine.turn_policy.params" in str(exc.value)


def test_named_turn_policy_error_names_the_map_entry() -> None:
    from silisocs.simulation_engines.policies.factory import build_gm_turn_policies

    with pytest.raises(ValueError) as exc:
        build_gm_turn_policies({"social_gm": {"built_in": "fixed_count", "params": {"conut": 3}}})
    assert "gm_turn_policies['social_gm']" in str(exc.value)


# --- agent construction ------------------------------------------------------


class _StrictAgent(Agent):
    """Custom agent with a closed constructor signature."""

    def __init__(self, *, model: object, name: str, context: str = "") -> None:
        super().__init__(model)  # type: ignore[arg-type]
        self._name = name
        self._context = context

    @property
    def name(self) -> str:
        return self._name

    def observe(self, observation: str) -> None:
        self._context += observation

    def act(self, action_spec: object) -> str:  # type: ignore[override]
        return "noop"


def test_agent_param_typo_names_the_agent() -> None:
    # The shipped agents accept **extra_params by design; a custom agent with a
    # closed signature is where a mistyped persona param must fail loudly.
    from silisocs.runtime.construction.assembly import RuntimeObjects, add_agent
    from silisocs.runtime.construction.specs import AgentConfig
    from silisocs.runtime.language_models.base import NoLanguageModel

    spec = AgentConfig(
        class_path=f"{__name__}._StrictAgent",
        params={"name": "Alex", "context": "ctx", "styl": "terse"},
    )
    with pytest.raises(ValueError) as exc:
        add_agent(
            runtime=RuntimeObjects(),
            spec=spec,
            models={"m": NoLanguageModel()},
            object_to_model={"Alex": "m"},
        )
    message = str(exc.value)
    assert "styl" in message
    assert "Alex" in message


# --- persona class allowlist -------------------------------------------------


def _persona_cfg(class_cfg: dict) -> object:
    return OmegaConf.create({"agents": {"persona_pipeline": {"classes": {"voter": class_cfg}}}})


def test_unknown_persona_class_key_suggests_the_nearest_valid_key() -> None:
    from silisocs.runtime.configuration.validation import _validate_persona_classes

    with pytest.raises(ValueError) as exc:
        _validate_persona_classes(
            _persona_cfg({"count": 1, "class_path": "x.Y", "flow_tags": "influencer"})  # type: ignore[arg-type]
        )
    message = str(exc.value)
    assert "flow_tags" in message
    assert "Did you mean `flow_tag`?" in message
    assert "agents.persona_pipeline.classes.voter" in message


def test_known_persona_class_keys_pass() -> None:
    from silisocs.runtime.configuration.validation import _validate_persona_classes

    _validate_persona_classes(
        _persona_cfg(  # type: ignore[arg-type]
            {
                "count": 2,
                "class_path": "silisocs.agents.native.NativeAgent",
                "sim_role_name": "voter",
                "flow_tag": "default",
                "data": {"source": "inline", "records": []},
                "field_map": {"name": "name", "context": "persona"},
                "params": {"style": "terse"},
                "model": {"name": "gpt-4o-mini"},
            }
        )
    )


def test_custom_agent_builder_keeps_its_own_class_vocabulary() -> None:
    from silisocs.runtime.configuration.validation import _validate_persona_classes

    cfg = OmegaConf.create(
        {
            "agents": {
                "builder": {"class_path": "mypkg.MyBuilder"},
                "persona_pipeline": {"classes": {"voter": {"whatever": 1}}},
            }
        }
    )
    _validate_persona_classes(cfg)
