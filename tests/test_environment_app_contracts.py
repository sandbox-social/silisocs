from __future__ import annotations

from typing import Any

import pytest
from concordia.typing import entity as entity_lib

from silisocs.environments.backends.base import EnvironmentApp, app_action
from silisocs.environments.backends.factory import create_environment_app
from silisocs.environments.gm.components.observe import AppObservationComponent


class _GenericTestApp(EnvironmentApp):
    def __init__(self) -> None:
        super().__init__()
        self.initialized_with: dict[str, Any] | None = None
        self.observed_with: list[dict[str, Any]] = []

    def name(self) -> str:
        return "generic_test"

    def description(self) -> str:
        return "A generic non-social test app"

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        self.initialized_with = {"agent_names": agent_names, **kwargs}

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        self.observed_with.append({"actor_name": actor_name, **kwargs})
        return f"{actor_name} sees generic state"

    @app_action(selectable_name="WORK", description="Do work")
    def do_work(self, current_user: str, amount: int) -> str:
        return f"{current_user} worked {amount}"


def test_environment_app_keeps_action_catalog_generic() -> None:
    app = _GenericTestApp()

    catalog = app.action_catalog()
    selectable = {item["selectable_name"] for item in catalog}

    assert "WORK" in selectable
    assert "FINISHED" in selectable
    assert app.invoke_action_with_kwargs(
        "WORK",
        {"current_user": "Alice", "amount": "3"},
    ) == "Alice worked 3"


def test_app_observation_component_delegates_to_environment_observe() -> None:
    app = _GenericTestApp()
    component = AppObservationComponent(
        model=object(),
        player_names=("Alice",),
        env_app=app,
        entity_flow_tags={"Alice": "market"},
        observation_params={"limit": 4},
    )

    action_spec = entity_lib.ActionSpec(
        call_to_action="Alice",
        output_type=entity_lib.OutputType.MAKE_OBSERVATION,
    )

    result = component.pre_act(action_spec)

    assert result == "Alice sees generic state"
    assert app.observed_with == [
        {
            "actor_name": "Alice",
            "step": 0,
            "flow_tag": "market",
            "limit": 4,
        }
    ]


def test_custom_environment_app_rejects_unknown_params() -> None:
    with pytest.raises(ValueError, match="Unsupported config param"):
        create_environment_app(
            "custom",
            app_class_path="tests.test_environment_app_contracts._GenericTestApp",
            app_params={"unknown_param": True},
        )
