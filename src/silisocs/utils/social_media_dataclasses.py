"""Environment runtime dataclasses with social-media compatibility aliases."""

from dataclasses import dataclass, field

from omegaconf import MISSING

from silisocs.runtime.dataclasses import AbstractGameMasterParams, AgentParams, SimRole


@dataclass(frozen=True)
class SocialMediaUserParams(AgentParams):
    """SocialMediaUserParams."""

    seed_post: str
    bio: str
    goal: str | None


@dataclass(frozen=True)
class SimRoleParameters:
    """SimRoleParameters."""

    activity_transition_rates: dict[str, dict[str, int]] = MISSING
    initial_follow_prob: dict[str, dict[str, float]] = MISSING


@dataclass(frozen=True)
class UserData:
    """UserData."""

    sim_role_parameters: SimRoleParameters
    sim_roles: dict[str, str] = field(default_factory=dict)
    entity_flow_tags: dict[str, str] = field(default_factory=dict)
    gm_orchestration: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EnvironmentParams(AbstractGameMasterParams):
    """Generic environment game-master parameters."""

    name: str
    calls_to_action: dict[str, str]
    app_module_path: str = "silisocs"
    sim_role: SimRole
    environment_data: UserData = field(
        default_factory=lambda: UserData(sim_role_parameters=SimRoleParameters(), sim_roles={})
    )
    app_description: str = "Social media platform simulation"

    @property
    def sm_user_data(self) -> UserData:
        """Compatibility accessor for existing social-media call sites."""
        return self.environment_data


EnvironmentRuntimeData = UserData


@dataclass(frozen=True)
class SocialMediaParams(AbstractGameMasterParams):
    """Compatibility dataclass for social-media game-master parameters."""

    name: str
    calls_to_action: dict[str, str]
    app_module_path: str = "silisocs"
    sim_role: SimRole
    sm_user_data: UserData = field(
        default_factory=lambda: UserData(sim_role_parameters=SimRoleParameters(), sim_roles={})
    )
    app_description: str = "Social media platform simulation"

    @property
    def environment_data(self) -> UserData:
        """Expose social-media params through the generic field name."""
        return self.sm_user_data
