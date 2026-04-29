# src/sim/config_utils/abstract_scenario.py

from dataclasses import dataclass, field
from typing import Any

from concordia.typing import prefab as prefab_lib
from omegaconf import MISSING

# from .social_media_constants import USE_SERVER
# from sim.config_utils.simulation_constants import (
#     APP_MODULE_PATH,
#     LLM_NAME,
#     NUM_AGENTS,
#     NUM_STEPS,
#     ROLEPLAYING_INSTRUCTIONS,
#     RUN_NAME,
#     SCENARIO_NAME,
#     SEED,
#     SENTENCE_ENCODER,
# )


# ==================================================
# @dataclass
# class SimConfig:
#     app_module_path: str = APP_MODULE_PATH
#     llm_name: str = LLM_NAME
#     num_agents: int = NUM_AGENTS
#     num_steps: int = NUM_STEPS
#     run_name: str = RUN_NAME
#     seed: int = SEED
#     sentence_encoder: str = SENTENCE_ENCODER
#     output_rootname: str = ""  # set from hydra fields at runtime
#     roleplaying_instructions: str = ROLEPLAYING_INSTRUCTIONS
#     scenario_name: str = SCENARIO_NAME


# ==================================================


# Abstract base classes for type checking only - NOT for Hydra registration


@dataclass(frozen=True)
class SimRole:
    """
    Custom entity (e.g. agent or gamemaster) loaded from module.
    Agent role also determines agent's social network properties and rate of activity
    """

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


@dataclass(kw_only=True)
class AgentConfig(prefab_lib.InstanceConfig):
    params: dict[str, Any] = field()  # AgentParams = field()
    prefab: str = ""
    role: prefab_lib.Role = field(default=prefab_lib.Role.ENTITY)


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
    module_path: str = ""


@dataclass(kw_only=True)
class InitializerConfig(prefab_lib.InstanceConfig):
    prefab: str
    params: dict[str, Any]  # InitializerParams
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
class QueryData:
    query_type: str
    interaction_premise_template: Any = None


@dataclass()
class AgentsConfig:
    inputs: Any = MISSING
    directory: list[prefab_lib.InstanceConfig] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)


# @dataclass()
# class SocSysConfig:
#     exp_name: str = "election_experiment"
#     game_masters: list[Any] = field(default_factory=list)  # Variable-length list
#     setting_info: Any = None
#     shared_agent_memories_template: list[str] = field(default_factory=list)
#     scenario_name: str = "election"
#     social_media_usage_instructions: str = ""


# @dataclass()
# class ProbesConfig:
#     queries_data: dict[int, Any] = field(default_factory=dict)
#     query_lib_module: str = "scenarios.election.config_utils.probe_lib"


# @dataclass
# class SimulationConfig:
#     """Concrete scenario config."""

#     sim: SimConfig = field(default_factory=lambda: SimConfig())
#     agents: AgentsConfig = field(default_factory=AgentsConfig)
#     soc_sys: SocSysConfig = field(default_factory=SocSysConfig)
#     probes: ProbesConfig = field(default_factory=ProbesConfig)
