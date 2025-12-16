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
)

# ============================================================================
# Agent Parameter Classes
# ============================================================================


@dataclass
class SimRole:
    name: str
    model_module_path: str


@dataclass
class SocialMediaUserParams:
    name: str
    seed_post: str
    simrole_dict: SimRole
    bio: str
    context: str
    style: str


@dataclass
class Voter(SocialMediaUserParams):
    policy_proposals: str
    goal: str
    gender: str


@dataclass
class NewsAccount(SocialMediaUserParams):
    posts: dict[str, str] = field(default_factory=dict)


@dataclass
class Candidate(SocialMediaUserParams):
    policy_proposals: str
    goal: str
    gender: str


@dataclass(kw_only=True)
class AgentConfig(prefab_lib.InstanceConfig):
    params: SocialMediaUserParams = field()
    prefab: str = ""
    role: prefab_lib.Role = field(default=prefab_lib.Role.ENTITY)


# ============================================================================
# Agent Inputs Configuration
# ============================================================================


@dataclass
class AgentInputs(AbstractAgentInputs):
    news_file: str = "default_news.json"
    persona_file: str = "personas.csv"


# ============================================================================
# Setting Details for Social System
# ============================================================================


@dataclass
class CandidateInfo:
    name: str
    gender: str
    policy_proposals: str


@dataclass
class CandidatesInfo:
    conservative: CandidateInfo
    progressive: CandidateInfo


@dataclass
class ActiveRatesPerStep:
    candidate: float = 0.7
    exogenous: float = 1.0
    voter: float = 0.8


@dataclass
class InitialFollowProb:
    candidate: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "news": 1.0, "voter": 0.4}
    )
    news: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "news": 1.0, "voter": 0.4}
    )
    voter: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "news": 1.0, "voter": 0.4}
    )


@dataclass
class SimRoleParameters:
    active_rates_per_episode: ActiveRatesPerStep = field(default_factory=ActiveRatesPerStep)
    initial_follow_prob: InitialFollowProb = field(default_factory=InitialFollowProb)


@dataclass
class UserData:
    simrole_parameters: SimRoleParameters
    simroles: dict[str, str] = field(default_factory=dict)


@dataclass
class SettingDetails(AbstractSettingDetails):
    candidate_info: CandidatesInfo
    simrole_parameters: SimRoleParameters


@dataclass
class SocialMediaParams(AbstractGameMasterParams):
    name: str = "Social Media Game Master"
    call_to_action: str = "Engage with social media"
    app_module: str = "mastodon_sim"
    sm_user_data: UserData = field(
        default_factory=lambda: UserData(simrole_parameters=SimRoleParameters(), simroles={})
    )
    use_server: bool = False
    app_description: str = "Social media platform simulation"


# ============================================================================
# Interaction Premise Template for Probes
# ============================================================================


@dataclass
class InteractionPremiseTemplate(AbstractInteractionPremiseTemplate):
    candidate: str | None = None
    candidate1: str | None = None
    candidate2: str | None = None


# ============================================================================
# Concrete Configuration Classes
# ============================================================================


@dataclass
class AgentsConfig:
    inputs: AgentInputs = field(default_factory=AgentInputs)
    directory: list[prefab_lib.InstanceConfig] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)


@dataclass
class SocSysConfig:
    exp_name: str = "election_experiment"
    game_masters: list[Any] = field(default_factory=list)  # Variable-length list
    setting_info: Any = None
    shared_agent_memories_template: list[str] = field(default_factory=list)
    scenario_name: str = "election"
    social_media_usage_instructions: str = ""


@dataclass
class ProbesConfig:
    queries_data: dict[int, Any] = field(default_factory=dict)
    query_lib_module: str = "scenarios.election.probe_lib"
