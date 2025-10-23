import concurrent.futures
import importlib
import random

from concordia.associative_memory import (
    associative_memory,
    blank_memories,
    formative_memories,
    importance_function,
)

from mastodon_sim.concordia import apps
from mastodon_sim.mastodon_ops import update_bio


def generate_concordia_memory_objects(
    model, embedder, shared_agent_memories, gamemaster_memories, clock
):
    importance_model = importance_function.ConstantImportanceModel()
    importance_model_gm = importance_function.ConstantImportanceModel()

    blank_memory_factory = blank_memories.MemoryFactory(
        model=model,
        embedder=embedder,
        importance=importance_model.importance,
        clock_now=clock.now,
    )
    formative_memory_factory = formative_memories.FormativeMemoryFactory(
        model=model,
        shared_memories=shared_agent_memories,
        blank_memory_factory_call=blank_memory_factory.make_blank_memory,
    )

    game_master_memory = associative_memory.AssociativeMemory(
        embedder, importance_model_gm.importance, clock=clock.now
    )
    for memory in gamemaster_memories:
        game_master_memory.add(memory)

    return (
        importance_model,
        importance_model_gm,
        blank_memory_factory,
        formative_memory_factory,
        game_master_memory,
    )


def make_profiles(agent_data):
    # Create agents from JSON data and classify them
    roles = {}
    profiles = {}
    for agent_info in agent_data:
        roles[agent_info["name"]] = agent_info["role_dict"]["name"]
        profiles[agent_info["name"]] = {}
        agent_info["role_dict"]["agent_name"] = agent_info["name"]
        agent_info["role_dict"] = (
            agent_info["role_dict"] | {"style": agent_info["style"]}
            if agent_info["role_dict"]["name"] != "exogenous"
            else agent_info["role_dict"]
        )

        profiles[agent_info["name"]]["role_dict"] = agent_info["role_dict"] | {
            "agent_name": agent_info["name"],
        }
        profiles[agent_info["name"]]["cfg"] = (
            agent_info["posts"]
            if agent_info["role_dict"]["name"] == "exogenous"
            else formative_memories.AgentConfig(
                name=agent_info["name"],
                gender=agent_info["gender"],
                goal=agent_info["goal"],
                context=agent_info["context"],
                traits=agent_info["traits"],
            )
        )

    return profiles, roles


def build_agent_with_memories(obj_args, profile_item):
    profile_cfg = profile_item["cfg"]
    role_dict = profile_item["role_dict"]
    (formative_memory_factory, model, clock, time_step, setting_info) = obj_args

    # for non-exogenous agents
    if role_dict["name"] == "exogenous":
        module_path = "agent_utils.exogenous_agent"
        input_args = {"posts": profile_cfg, "name": role_dict["agent_name"]}
        store_for_local_post_analysis = None
    else:
        setting_data = {
            "setting_details": setting_info["details"],
            "setting_description": setting_info["description"],
        }
        module_path = "sim_setting." + role_dict["module_path"]
        mem = formative_memory_factory.make_memories(profile_cfg)
        input_args = {
            "config": profile_cfg,
            "input_data": role_dict | setting_data,
            "model": model,
            "clock": clock,
            "update_time_interval": time_step,
            "memory": mem,
        }
        store_for_local_post_analysis = mem

    agent_module = importlib.import_module(module_path)
    agent = agent_module.AgentBuilder.build(**input_args)
    return agent, store_for_local_post_analysis


def _follow_and_update_bio(roles, role_parameters, mastodon_apps, user_mapping):
    # initiailize initial social network. Pre-generate unique follow relationships
    follow_pairs = set()
    # Now, generate additional follow relationships between agents.
    role_prob_matrix = role_parameters["initial_follow_prob"]
    # agent_roles = [] # This variable was unused
    for agent_i, role_i in roles.items():
        for agent_j, role_j in roles.items():
            if agent_i == agent_j:  # Agents cannot follow themselves
                continue
            prob = role_prob_matrix[role_i][role_j]
            # The following block was originally if False, so it was never executed.
            # I'm keeping it commented out to preserve the original logic.
            # if False:
            #     if follower != followee: # follower and followee are not defined here
            #         # With a 20% chance, create mutual follow relationships.
            #         if random.random() < 0.2:
            #             follow_pairs.add((follower, followee))
            #             follow_pairs.add((followee, follower))
            # Otherwise, with a create a one-direction follow according to stored probability.
            if random.random() < prob:
                follow_pairs.add((agent_i, agent_j))

    # Execute the follow operations concurrently.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for follower, followee in follow_pairs:
            # Submit the follow operation from the appropriate mastodon app instance.
            futures.append(executor.submit(mastodon_apps[follower].follow_user, follower, followee))

    # Wait for all tasks to complete, handling exceptions as needed.
    for future in concurrent.futures.as_completed(futures):
        try:
            future.result()
        except Exception as e:
            # If a follow error occurs (e.g. already following), we simply log and ignore it.
            print(f"Ignoring error during follow operation: {e}")

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(
                update_bio, user_mapping[agent_name], display_name=agent_name, bio=""
            )  # update with generated bios?
            for agent_name in user_mapping
        ]
    # Optionally, wait for all tasks to complete
    for future in concurrent.futures.as_completed(futures):
        try:
            future.result()
        except Exception as e:
            print(f"Ignoring error during bio update: {e}")


def set_up_mastodon_app_usage(
    roles, role_parameters, action_logger, app_description, use_server, setup_base=True
):
    active_rates = {}
    for agent_name, role in roles.items():
        active_rates[agent_name] = role_parameters["active_rates_per_episode"][role]

    mastodon_apps = {
        agent_name: apps.MastodonSocialNetworkApp(
            action_logger=action_logger,
            perform_operations=use_server,
            app_description=app_description,
        )
        for agent_name in roles
    }
    user_mapping = {agent_name.split()[0]: f"user{i + 1:04d}" for i, agent_name in enumerate(roles)}
    for p in mastodon_apps:
        mastodon_apps[p].set_user_mapping(user_mapping)

    if setup_base:
        _follow_and_update_bio(roles, role_parameters, mastodon_apps, user_mapping)

    phones = {
        agent_name: apps.Phone(agent_name, apps=[mastodon_apps[agent_name]]) for agent_name in roles
    }

    return mastodon_apps, phones, active_rates
