# def create_agent_instances_from_config(
#     agent_config_list: list[Any],
#     prefabs: dict[str, Any],
# ) -> tuple[
#     list[prefab_lib.InstanceConfig],
#     list[prefab_lib.InstanceConfig],
#     dict[str, str],
#     list[str],
#     dict[str, list[str]],
#     dict[str, str],
#     dict[str, Any],
# ]:
#     """
#     Processes agent configs, creating InstanceConfig objects and loading prefabs.

#     - Sorts agents into 'entity' and 'exogenous' lists based on 'role_dict.name'.
#     - Dynamically imports agent classes and returns a map of prefab_string -> class_instance.
#     """
#     entity_agent_list = []
#     exogenous_agent_list = []
#     player_specific_memories_map = {}
#     player_specific_context_map = {}
#     roles = {}
#     entity_player_names = []
#     prefab_agents_map: dict[str, Any] = prefabs

#     for agent_data in agent_config_list:
#         player_name = agent_data["name"]
#         role_name = agent_data["role_dict"]["name"]
#         roles[player_name] = role_name
#         # --- a. Construct Prefab Name and Class Info ---
#         if role_name != "exogenous":
#             module_path_str = "sim_setting." + agent_data["role_dict"]["module_path"]
#             # class_name_str = "AgentBuilder"
#             # prefab_string = (
#             #     f"{module_path_str.split('sim_setting.')[1].replace('.', '__')}__{class_name_str}"
#             # )
#             class_name_str = "Entity"
#             prefab_string = "basic__Entity"

#             # --- b. Load Prefab Class ---
#             if prefab_string not in prefab_agents_map:
#                 print(f"[Loader] Loading prefab: {prefab_string}")
#                 try:
#                     # e.g. importlib.import_module("sim_setting.agent_lib.voter")
#                     buildagent_module = importlib.import_module(module_path_str)
#                     # e.g., getattr(module, "AgentBuilder")
#                     buildagent_class = getattr(buildagent_module, class_name_str)
#                     # Store the *instantiated* class
#                     prefab_agents_map[prefab_string] = buildagent_class()
#                 except ImportError:
#                     print(f"Error: Could not import module: {module_path_str}")
#                 except AttributeError:
#                     print(f"Error: Module {module_path_str} does not have class: {class_name_str}")
#                 except Exception as e:
#                     print(f"An error occurred while loading prefab {prefab_string}: {e}")

#             # --- c. Compress Context String ---
#             context_parts = []
#             if "context" in agent_data:
#                 context_parts.append(f"Biography: {agent_data['context']}")
#             if "gender" in agent_data:
#                 context_parts.append(f"Gender: {agent_data['gender']}")
#             if "style" in agent_data:
#                 context_parts.append(f"Communication Style: {agent_data['style']}")
#             if "party" in agent_data:
#                 context_parts.append(f"Political Party: {agent_data['party']}")
#             if "traits" in agent_data and isinstance(agent_data["traits"], dict):
#                 traits_str = ", ".join(f"{k}: {v}" for k, v in agent_data["traits"].items())
#                 context_parts.append(f"Traits: [{traits_str}]")
#             compressed_context = "\n".join(context_parts)

#             # --- d. Create the InstanceConfig ---
#             agent_config = prefab_lib.InstanceConfig(
#                 prefab=prefab_string,
#                 role=prefab_lib.Role.ENTITY,
#                 params={
#                     "name": player_name,
#                     "goal": agent_data.get("goal", "Live a normal life."),
#                     "context": compressed_context,
#                 },
#             )

#             # --- e. Sort agent and memory data based on role_dict.name ---

#             entity_agent_list.append(agent_config)
#             entity_player_names.append(player_name)
#             original_memories = [agent_data.get("memories", "No specific memories.")]
#             player_specific_memories_map[player_name] = original_memories
#             player_specific_context_map[player_name] = compressed_context
#         else:
#             exogenous_agent_list.append(
#                 prefab_lib.InstanceConfig(
#                     prefab="exogenous_agent__ExogenousAgent",
#                     role=prefab_lib.Role.ENTITY,
#                     params={"name": player_name},
#                 )
#             )

#     return (
#         entity_agent_list,
#         exogenous_agent_list,
#         roles,
#         player_specific_memories_map,
#         player_specific_context_map,
#         prefab_agents_map,
#     )


# def _follow_and_update_bio(follow_pairs, mastodon_app, user_mapping):

#     # Execute the follow operations concurrently.
#     with concurrent.futures.ThreadPoolExecutor() as executor:
#         futures = []
#         for follower, followee in follow_pairs:
#             # Submit the follow operation from the appropriate mastodon app instance.
#             futures.append(executor.submit(mastodon_app.follow_user, follower, followee))

#     # Wait for all tasks to complete, handling exceptions as needed.
#     for future in concurrent.futures.as_completed(futures):
#         try:
#             future.result()
#         except Exception as e:
#             # If a follow error occurs (e.g. already following), we simply log and ignore it.
#             print(f"Ignoring error during follow operation: {e}")

#     with concurrent.futures.ThreadPoolExecutor() as executor:
#         futures = [
#             executor.submit(
#                 update_bio, user_mapping[agent_name], display_name=agent_name, bio=""
#             )  # update with generated bios?
#             for agent_name in user_mapping
#         ]
#     # Optionally, wait for all tasks to complete
#     for future in concurrent.futures.as_completed(futures):
#         try:
#             future.result()
#         except Exception as e:
#             print(f"Ignoring error during bio update: {e}")


# def set_up_mastodon_app_usage(
#     roles, role_parameters, action_logger, app_description, use_server, setup_base=True
# ):
#     active_rates = {}
#     for agent_name, role in roles.items():
#         active_rates[agent_name] = role_parameters["active_rates_per_episode"][role]

#     mastodon_app = apps.MastodonSocialNetworkApp(
#         action_logger=action_logger,
#         perform_operations=use_server,
#         app_description=app_description,
#     )

#     user_mapping = {agent_name.split()[0]: f"user{i + 1:04d}" for i, agent_name in enumerate(roles)}
#     mastodon_app.set_user_mapping(user_mapping)

#     if setup_base:
#         _follow_and_update_bio(roles, role_parameters, mastodon_app, user_mapping)

#     return mastodon_app, active_rates, user_mapping
