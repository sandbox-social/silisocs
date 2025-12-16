# src/sim/config_utils/abstract_scenario.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

from concordia.typing import prefab as prefab_lib
from omegaconf import MISSING


# ==================================================
@dataclass
class SimConfig:
    app_module_path: str = "mastodon_sim"
    load_path: str = ""
    llm_name: str = "gpt-4o-mini"
    num_agents: int = 20
    num_steps: int = 1
    run_name: str = "run1"
    seed: int = 1
    sentence_encoder: str = "sentence-transformers/all-mpnet-base-v2"
    output_rootname: str = ""
    roleplaying_instructions: str = (
        "<s>"
        "You are simulating {name}, a character in a social science experiment. "
        "Always use third-person limited perspective when describing {name}'s thoughts and actions. "
        "Your goal is to determine the single most appropriate action {name} would take next."
        "</s>"
    )
    scenario_name: str = "election"  # Default value, can be overridden
    # scenario_specific
    persona_type: str = "Reddit.Big5"
    use_news_agent: str = "with_images"
    use_server: bool = False


# ==================================================
# Abstract base classes for type checking only - NOT for Hydra registration


@dataclass
class SimRole:
    name: str
    module_path: str


@dataclass
class AgentParams:
    name: str
    context: (
        str  # holds all persona information (demographic+personality). Is used to create backstory
    )
    sim_role: SimRole
    style: str  # post writing style and topics
    goal: str | None


@dataclass
class AbstractAgentInputs:
    pass


@dataclass
class AbstractSettingDetails:
    pass


@dataclass
class SettingInfo:
    description: str
    details: Any


@dataclass
class InitializerParams:
    next_game_master_name: str
    shared_memories: list[str]
    player_specific_memories: dict[str, Any]
    player_specific_context: dict[str, Any]
    name: str = "initial setup rules"


@dataclass(kw_only=True)
class InitializerConfig(prefab_lib.InstanceConfig):
    prefab: str
    params: InitializerParams
    role: prefab_lib.Role = field(default=prefab_lib.Role.INITIALIZER)


@dataclass
class AbstractGameMasterParams:
    name: str
    calls_to_action: dict[str, str]
    sim_role: SimRole


@dataclass(kw_only=True)
class GameMasterConfig(prefab_lib.InstanceConfig):
    prefab: str
    params: Any  # Will be concrete type in implementation
    role: prefab_lib.Role = field(default=prefab_lib.Role.GAME_MASTER)


# ===================================================


@dataclass
class AbstractInteractionPremiseTemplate:
    pass


@dataclass
class QueryData:
    """Non-generic query data to avoid OmegaConf serialization issues."""

    query_type: str
    interaction_premise_template: Any = None


# ============================================================================
# Base Config Classes - Non-generic for Hydra/OmegaConf compatibility
# ============================================================================


@dataclass
class BaseAgentsConfig:
    """Base agents config - scenarios should define concrete versions."""

    inputs: Any = MISSING
    directory: list[prefab_lib.InstanceConfig] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)


@dataclass
class BaseSocSysConfig:
    """Base social system config - scenarios should define concrete versions."""

    exp_name: str = MISSING
    game_masters: list[Any] = field(default_factory=list)
    setting_info: Any = MISSING
    shared_agent_memories_template: list[str] = field(default_factory=list)
    scenario_name: str = MISSING
    social_media_usage_instructions: str = ""


@dataclass
class BaseProbesConfig:
    """Base probes config - scenarios should define concrete versions."""

    queries_data: dict[int, Any] = field(default_factory=dict)
    query_lib_module: str = "config_utils.agent_query_lib"


# ============================================================================
# Top-level Config Schema
# ============================================================================


@dataclass
class BaseScenarioConfig:
    """Base scenario config - scenarios should define concrete versions with proper types."""

    sim: SimConfig = MISSING
    agents: Any = MISSING  # Concrete AgentsConfig in scenario
    soc_sys: Any = MISSING  # Concrete SocSysConfig in scenario
    probes: Any = MISSING  # Concrete ProbesConfig in scenario
    defaults: list = field(default_factory=list)


ScenarioConfig = TypeVar("ScenarioConfig", bound=BaseScenarioConfig)


class AbstractScenario(ABC):
    """
    Base class defining the contract for every simulation scenario.
    """

    name: str = field(init=True)

    @abstractmethod
    def generate_config(self, name: str):
        pass

    @abstractmethod
    def generate_scenario_configs(self, sim_cfg: SimConfig):
        pass

    @abstractmethod
    def get_agent_classes(self) -> dict[str, type]:
        pass
