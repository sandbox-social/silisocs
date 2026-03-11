from dataclasses import dataclass, field

from omegaconf import MISSING

from mastodon_sim.runtime.dataclasses import AbstractGameMasterParams, AgentParams, SimRole


@dataclass(frozen=True)
class SocialMediaUserParams(AgentParams):
    seed_post: str
    bio: str
    goal: str | None


@dataclass(frozen=True)
class SimRoleParameters:
    activity_transition_rates: dict[str, dict[str, int]] = MISSING
    initial_follow_prob: dict[str, dict[str, float]] = MISSING


@dataclass(frozen=True)
class UserData:
    sim_role_parameters: SimRoleParameters
    sim_roles: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SocialMediaParams(AbstractGameMasterParams):
    name: str
    calls_to_action: dict[str, str]
    app_module_path: str = "mastodon_sim"
    sim_role: SimRole
    sm_user_data: UserData = field(
        default_factory=lambda: UserData(sim_role_parameters=SimRoleParameters(), sim_roles={})
    )
    app_description: str = "Social media platform simulation"
