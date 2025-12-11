import importlib
from typing import Any

from concordia.typing import prefab as prefab_lib


def create_agent_instances_from_config(
    agent_config_list: list[Any],
    prefabs: dict[str, Any],
) -> tuple[
    list[prefab_lib.InstanceConfig],
    list[prefab_lib.InstanceConfig],
    dict[str, str],
    dict[str, list[str]],
    dict[str, str],
    dict[str, Any],
]:
    """
    Processes agent configs, creating InstanceConfig objects and loading prefabs.

    - Sorts agents into 'entity' and 'exogenous' lists based on 'role_dict.name'.
    - Dynamically imports agent classes and returns a map of prefab_string -> class_instance.
    """
    entity_agent_list = []
    exogenous_agent_list = []
    player_specific_memories_map = {}
    player_specific_context_map = {}
    roles: dict[str, Any] = {}
    entity_player_names = []
    prefab_agents_map: dict[str, Any] = prefabs

    for agent_data in agent_config_list:
        player_name = agent_data["name"]
        role_name = agent_data["role_dict"]["name"]
        roles[player_name] = role_name
        # --- a. Construct Prefab Name and Class Info ---
        if role_name != "exogenous":
            module_path_str = "sim_setting." + agent_data["role_dict"]["module_path"]
            class_name_str = "Entity"
            prefab_string = module_path_str.split(".")[-1] + "__" + class_name_str
            prefab_string = role_name + "__" + class_name_str

            # --- b. Load Prefab Class ---
            if prefab_string not in prefab_agents_map:
                print(f"[Loader] Loading prefab: {prefab_string}")
                try:
                    # e.g. importlib.import_module("sim_setting.agent_lib.voter")
                    buildagent_module = importlib.import_module(module_path_str)
                    # e.g., getattr(module, "AgentBuilder")
                    buildagent_class = getattr(buildagent_module, class_name_str)
                    # Store the *instantiated* class
                    prefab_agents_map[prefab_string] = buildagent_class()
                except ImportError:
                    print(f"Error: Could not import module: {module_path_str}")
                except AttributeError:
                    print(f"Error: Module {module_path_str} does not have class: {class_name_str}")
                except Exception as e:
                    print(f"An error occurred while loading prefab {prefab_string}: {e}")

            # --- c. Compress Context String ---
            context_parts = []
            if "context" in agent_data:
                context_parts.append(f"Biography: {agent_data['context']}")
            if "gender" in agent_data:
                context_parts.append(f"Gender: {agent_data['gender']}")
            if "style" in agent_data:
                context_parts.append(f"Communication Style: {agent_data['style']}")
            if "party" in agent_data:
                context_parts.append(f"Political Party: {agent_data['party']}")
            if "traits" in agent_data and isinstance(agent_data["traits"], dict):
                traits_str = ", ".join(f"{k}: {v}" for k, v in agent_data["traits"].items())
                context_parts.append(f"Traits: [{traits_str}]")
            compressed_context = "\n".join(context_parts)

            # --- d. Create the InstanceConfig ---
            agent_config = prefab_lib.InstanceConfig(
                prefab=prefab_string,
                role=prefab_lib.Role.ENTITY,
                params={
                    "name": player_name,
                    "goal": agent_data.get("goal", "Live a normal life."),
                    "context": compressed_context,
                },
            )

            # --- e. Sort agent and memory data based on role_dict.name ---

            entity_agent_list.append(agent_config)
            entity_player_names.append(player_name)
            original_memories = [agent_data.get("memories", "No specific memories.")]
            player_specific_memories_map[player_name] = original_memories
            player_specific_context_map[player_name] = compressed_context
        else:
            exogenous_agent_list.append(
                prefab_lib.InstanceConfig(
                    prefab="exogenous_agent__ExogenousAgent",
                    role=prefab_lib.Role.ENTITY,
                    params={"name": player_name},
                )
            )

    return (
        entity_agent_list,
        exogenous_agent_list,
        roles,
        player_specific_memories_map,
        player_specific_context_map,
        prefab_agents_map,
    )
