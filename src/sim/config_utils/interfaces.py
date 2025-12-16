# from typing import Any, Protocol

# SCENARIO_NAME = "election"

# if SCENARIO_NAME == "election":
#     from scenarios.election.config_schemas import (  # Import the static definitions
#         AgentsConfig,
#         ProbesConfig,
#         SocSysConfig,
#     )
# else:
#     AgentsConfig = dict[str, Any]
#     SocSysConfig = dict[str, Any]
#     ProbesConfig = dict[str, Any]


# class DynamicModuleInterface(Protocol):
#     """
#     Defines the required attributes (dataclasses) that any
#     dynamically loaded module must expose.
#     """

#     # Use Type[...] to signify that the attribute is the class itself.
#     AgentsConfig: type[AgentsConfig]
#     SocSysConfig: type[SocSysConfig]
#     ProbesConfig: type[ProbesConfig]
