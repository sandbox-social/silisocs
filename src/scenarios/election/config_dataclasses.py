# scenarios/election/config_dataclasses.py

from dataclasses import dataclass, field
from typing import Any

from concordia.typing import prefab as prefab_lib

# CRITICAL: Only import from abstract_scenario, NOT from config_schema
# Importing from config_schema creates a circular import
from sim.config_utils.abstract_scenario import (
    AbstractAgentInputs,
    AbstractGameMasterParams,
    AbstractInteractionPremiseTemplate,
    AbstractSettingDetails,
    AgentParams,
    SimRole,
)

# ============================================================================
# Agent Parameter Classes
# ============================================================================


@dataclass(frozen=True)
class SocialMediaUserParams(AgentParams):
    seed_post: str
    bio: str
    goal: str | None


@dataclass(frozen=True)
class VoterParams(SocialMediaUserParams):
    election_info: str


@dataclass(frozen=True)
class NewsAccountParams(SocialMediaUserParams):
    posts: dict[str, str] = field(default_factory=dict)


@dataclass(kw_only=True)
class AgentConfig(prefab_lib.InstanceConfig):
    params: SocialMediaUserParams = field()
    prefab: str = ""
    role: prefab_lib.Role = field(default=prefab_lib.Role.ENTITY)


# ============================================================================
# Agent Inputs Configuration
# ============================================================================


@dataclass(frozen=True)
class AgentInputs(AbstractAgentInputs):
    news_file: str = "default_news.json"
    persona_file: str = "personas.csv"


# ============================================================================
# Setting Details for Social System
# ============================================================================


@dataclass(frozen=True)
class CandidateInfo:
    name: str
    policy_proposals: str


@dataclass(frozen=True)
class CandidatesInfo:
    conservative: CandidateInfo
    progressive: CandidateInfo


@dataclass(frozen=True)
class ActiveRatesPerStep:
    candidate: float = 0.7
    news_account: float = 1.0
    voter: float = 0.8


@dataclass(frozen=True)
class InitialFollowProb:
    candidate: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "news_account": 1.0, "voter": 0.4}
    )
    news_account: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "news_account": 1.0, "voter": 0.4}
    )
    voter: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "news_account": 1.0, "voter": 0.4}
    )


@dataclass(frozen=True)
class SimRoleParameters:
    active_rates_per_episode: ActiveRatesPerStep = field(default_factory=ActiveRatesPerStep)
    initial_follow_prob: InitialFollowProb = field(default_factory=InitialFollowProb)


@dataclass(frozen=True)
class UserData:
    sim_role_parameters: SimRoleParameters
    sim_roles: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SettingDetails(AbstractSettingDetails):
    candidate_info: CandidatesInfo
    sim_role_parameters: SimRoleParameters


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


# ============================================================================
# Interaction Premise Template for Probes
# ============================================================================


@dataclass(frozen=True)
class InteractionPremiseTemplate(AbstractInteractionPremiseTemplate):
    candidate: str | None = None
    candidate1: str | None = None
    candidate2: str | None = None


# ============================================================================
# Concrete Configuration Classes
# ============================================================================


@dataclass()
class AgentsConfig:
    inputs: AgentInputs = field(default_factory=AgentInputs)
    directory: list[prefab_lib.InstanceConfig] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)


@dataclass()
class SocSysConfig:
    exp_name: str = "election_experiment"
    game_masters: list[Any] = field(default_factory=list)  # Variable-length list
    setting_info: Any = None
    shared_agent_memories_template: list[str] = field(default_factory=list)
    scenario_name: str = "election"
    social_media_usage_instructions: str = ""


@dataclass()
class ProbesConfig:
    queries_data: dict[int, Any] = field(default_factory=dict)
    query_lib_module: str = "scenarios.election.config_utils.probe_lib"
