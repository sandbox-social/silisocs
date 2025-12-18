# src/sim/config_utils/abstract_scenario.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

from concordia.typing import prefab as prefab_lib
from omegaconf import MISSING

# ========= SimConfig constants =====================
APP_MODULE_PATH = "mastodon_sim"
LLM_NAME = "gpt-4o-mini"
NUM_AGENTS = 20
NUM_STEPS = 1
RUN_NAME = "run1"
SEED = 1
SENTENCE_ENCODER = "sentence-transformers/all-mpnet-base-v2"
ROLEPLAYING_INSTRUCTIONS = (
    "<general_instructions> \n"
    "You are simulating {agent_name}, a character in a social science experiment. \n"
    "Always use third-person limited perspective when describing {agent_name}'s thoughts and actions. \n"
    "Your goal is to determine the single most appropriate action {agent_name} would take next. \n"
    "</general_instructions> \n"
)
SCENARIO_NAME = ""  # set in scenario class
# scenario_specific
PERSONA_TYPE = "Reddit.Big5"
USE_NEWS_AGENT = "with_images"
USE_SERVER = False


# ==================================================
@dataclass
class SimConfig:
    app_module_path: str = APP_MODULE_PATH
    llm_name: str = LLM_NAME
    num_agents: int = NUM_AGENTS
    num_steps: int = NUM_STEPS
    run_name: str = RUN_NAME
    seed: int = SEED
    sentence_encoder: str = SENTENCE_ENCODER
    output_rootname: str = ""  # set from hydra fields at runtime
    roleplaying_instructions: str = ROLEPLAYING_INSTRUCTIONS
    scenario_name: str = SCENARIO_NAME
    # scenario_specific
    persona_type: str = PERSONA_TYPE
    use_news_agent: str = USE_NEWS_AGENT
    use_server: bool = USE_SERVER


# ==================================================


# Abstract base classes for type checking only - NOT for Hydra registration


@dataclass(frozen=True)
class SimRole:
    name: str
    module_path: str


@dataclass(frozen=True)
class AgentParams:
    name: str
    context: (
        str  # holds all persona information (demographic+personality). Is used to create backstory
    )
    sim_role: SimRole
    style: str  # post writing style and topics
    goal: str | None


@dataclass(frozen=True)
class AbstractAgentInputs:
    pass


@dataclass(frozen=True)
class AbstractSettingDetails:
    pass


@dataclass(frozen=True)
class SettingInfo:
    description: str
    details: Any


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class AbstractInteractionPremiseTemplate:
    pass


@dataclass(frozen=True)
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

    def generate_sim_config(self):
        sim_cfg = SimConfig()
        return sim_cfg

    @abstractmethod
    def generate_config(self):
        pass

    @abstractmethod
    def generate_scenario_configs(self, sim_cfg: SimConfig):
        pass

    @abstractmethod
    def get_agent_classes(self) -> dict[str, type]:
        pass
