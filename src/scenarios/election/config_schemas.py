# import json

# from sim.entities.agent_instance_config import get_auxillary_agent_data_from_config

# from .config_constants import (
#     BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY,
#     CALL_TO_ACTION,
#     CANDIDATE_INFO,
#     PARTISAN_TYPES,
#     SCENARIO_NAME,
#     SHARED_MEMORIES_TEMPLATE,
#     SOCIAL_MEDIA_GAMEMASTER_FILENAME,
#     SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
#     USE_SERVER,
# )
# from .config_dataclasses import (
#     ActiveRatesPerStep,
#     AgentInputs,
#     AgentsConfig,
#     Candidate,
#     CandidateInfo,
#     CandidatesInfo,
#     GameMasterConfig,
#     InitialFollowProb,
#     InitializerConfig,
#     InitializerParams,
#     InteractionPremiseTemplate,
#     NewsAccount,
#     ProbesConfig,
#     QueryData,
#     SettingDetails,
#     SettingInfo,
#     SimRole,
#     SimRoleParameters,
#     SocialMediaParams,
#     SocSysConfig,
#     UserData,
#     Voter,
# )

# # ============================================================================
# # Config Generation Functions
# # ============================================================================


# def get_news_agent_configs(n_agents, news=None, include_images=True):
#     """Generate news agent configurations."""
#     news_types = ["local", "national", "international"]

#     # Limit the news types to the first n_agent elements
#     news_types = news_types[:n_agents]

#     # Create news agent config settings
#     news_info = {
#         "local": {
#             "name": "Storhampton Gazette",
#             "type": "local",
#             "coverage": "local news",
#             "schedule": "hourly",
#             "mastodon_username": "storhampton_gazette",
#             "seed_toot": "Good morning, Storhampton! Tune in for the latest local news updates.",
#         },
#         "national": {
#             "name": "National News Network",
#             "type": "national",
#             "coverage": "national news",
#             "schedule": "hourly",
#             "mastodon_username": "national_news_network",
#             "seed_toot": "Good morning, Storhampton! Tune in for the latest national news updates.",
#         },
#         "international": {
#             "name": "Global News Network",
#             "type": "international",
#             "coverage": "international news",
#             "schedule": "hourly",
#             "mastodon_username": "global_news_network",
#             "seed_toot": "Good morning, Storhampton! Tune in for the latest international news updates.",
#         },
#     }

#     news_agent_configs = []
#     for news_type in news_types:
#         news_data = news_info[news_type]

#         # Create AgentConfig for news agent using dataclass
#         agent_config = NewsAccount(
#             name=news_data["name"],
#             simrole_dict=SimRole(name="news", model_module_path="agent_lib.exogenous"),
#             seed_toot=news_data.get("seed_toot", ""),
#             bio=f"Providing {news_data['coverage']} to the users of Storhampton.social.",
#             # mastodon_username=news_data["mastodon_username"],
#             posts=(
#                 {k: [img for img in v] if include_images else [] for k, v in news.items()}
#                 if news is not None
#                 else None
#             ),
#         )
#         news_agent_configs.append(agent_config)

#     return news_agent_configs, {k: news_info[k] for k in news_types}


# def get_followership_connection_stats(roles):
#     """Generate followership statistics."""
#     fully_connected_targets = ["candidate", "exogenous"]
#     p_from_to = {}
#     for role_i in roles:
#         p_from_to[role_i] = {}
#         for role_j in roles:
#             if role_j in fully_connected_targets:
#                 p_from_to[role_i][role_j] = 1.0
#             else:
#                 p_from_to[role_i][role_j] = BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY
#     return p_from_to


# def get_agents_config(sim):
#     """Generate agents configuration from sim config."""
#     use_news_agent = sim.use_news_agent
#     num_agents = sim.num_agents

#     agents_dict = {
#         "inputs": {"persona_file": "reddit_agents.json", "news_file": "v1_news_bill_bias"},
#         "directory": [],
#     }

#     # Add candidates
#     candidate_configs = []
#     for partisan_type in PARTISAN_TYPES:
#         candidate = CANDIDATE_INFO[partisan_type].copy()
#         policy_text = (
#             f"{candidate['name']} campaigns on {' and '.join(candidate['policy_proposals'])}"
#         )
#         agent_config = Candidate(
#             name=candidate["name"],
#             gender=candidate["gender"],
#             policy_proposals=policy_text,
#             goal=f"{candidate['name']}'s goal is to win the election and become the mayor of Storhampton.",
#             simrole_dict=SimRole(name="candidate", model_model_module_path="agent_lib.simple"),
#             seed_toot="",
#         )
#         candidate_configs.append(agent_config)

#     # Add news agents if enabled
#     news_agent_configs = []
#     news_info = {}
#     if use_news_agent:
#         with open(
#             f"scenarios/election/input/news_data/{agents_dict['inputs']['news_file']}.json"
#         ) as f:
#             news = json.load(f)

#         print("headlines:")
#         for headline in news.keys():
#             print(headline)

#         include_images = use_news_agent == "with_images"
#         print(
#             "Including images with the above headlines"
#             if include_images
#             else "NOT including images"
#         )
#         n_agents = 1
#         news_agent_configs, news_info = get_news_agent_configs(
#             n_agents=n_agents, news=news, include_images=include_images
#         )

#     # Add voters
#     with open(f"scenarios/election/input/personas/{agents_dict['inputs']['persona_file']}") as f:
#         persona_rows = json.load(f)

#     voter_configs = []
#     for row in persona_rows[: num_agents - len(candidate_configs)]:
#         agent_config = Voter(
#             name=row["Name"],
#             goal=row["Context"] + " Their goal is have a good day and vote in the election.",
#             simrole_dict=SimRole(name="voter", model_module_path="agent_lib.simple"),
#             seed_toot="",
#             bio="",
#         )
#         voter_configs.append(agent_config)

#     # Combine all agents (voters + candidates first, then news agents added later in generate_output_configs)
#     all_agents = voter_configs + candidate_configs + news_agent_configs

#     simroles = {agent.name: agent.simrole_dict.name for agent in all_agents}

#     agents_config = AgentsConfig(
#         directory=all_agents,  # News agents added in generate_output_configs
#         initial_observations=[
#             "{name} is at home, they have just woken up.",
#             "{name} remembers they want to update their Mastodon bio.",
#             "{name} remembers they want to read their Mastodon feed to catch up on news",
#         ],
#         inputs=AgentInputs(
#             news_file=agents_dict["inputs"]["news_file"],
#             persona_file=agents_dict["inputs"]["persona_file"],
#         ),
#     )

#     return agents_config, news_info, simroles


# def get_soc_sys_config(sim, news_info, simroles):
#     """Generate social system configuration."""
#     experiment_name = "independent"

#     # Build candidate info dataclasses
#     candidate_info_dict = {}
#     for partisan_type in PARTISAN_TYPES:
#         candidate = CANDIDATE_INFO[partisan_type]
#         policy_text = (
#             f"{candidate['name']} campaigns on {' and '.join(candidate['policy_proposals'])}"
#         )
#         candidate_info_dict[partisan_type] = CandidateInfo(
#             name=candidate["name"], gender=candidate["gender"], policy_proposals=policy_text
#         )

#     candidates_info = CandidatesInfo(
#         conservative=candidate_info_dict["conservative"],
#         progressive=candidate_info_dict["progressive"],
#     )

#     # Build role parameters
#     active_rates = ActiveRatesPerStep(candidate=0.7, voter=0.8, exogenous=1.0)

#     initial_follow_prob_dict = get_followership_connection_stats(roles)
#     initial_follow_prob = InitialFollowProb(
#         candidate=initial_follow_prob_dict.get("candidate", {}),
#         exogenous=initial_follow_prob_dict.get("exogenous", {}),
#         voter=initial_follow_prob_dict.get("voter", {}),
#     )

#     simrole_params = SimRoleParameters(
#         active_rates_per_episode=active_rates, initial_follow_prob=initial_follow_prob
#     )

#     sm_user_data = UserData(simrole_parameters=simrole_params, simroles=simroles)
#     setting_details = SettingDetails(
#         candidate_info=candidates_info, simrole_parameters=simrole_params
#     )

#     description = "\n".join([candidate_info_dict[p].policy_proposals for p in PARTISAN_TYPES])

#     setting_info = SettingInfo(description=description, details=setting_details)

#     # Add news info to shared memories if applicable
#     shared_memories = SHARED_MEMORIES_TEMPLATE.copy()
#     if sim.use_news_agent and news_info:
#         shared_memories.append(
#             f"Voters in Storhampton are actively getting the latest local news from "
#             f"{news_info['local']['name']} social media account."
#         )

#     # Add Configurator Game Master
#     shared_memories = (
#         shared_memories + [setting_info.description] + [SOCIAL_MEDIA_USAGE_INSTRUCTIONS]
#     )
#     (roles, player_specific_memories_map, player_specific_context_map) = (
#         get_auxillary_agent_data_from_config(agent_data)
#     )
#     InitializerGM = InitializerConfig(
#         prefab="formative_memories_initializer__GameMaster",
#         params=InitializerParams(
#             name="initial setup rules",
#             next_game_master_name=SOCIAL_MEDIA_GAMEMASTER_FILENAME + "__GameMaster",
#             shared_memories=shared_memories,
#             player_specific_memories=player_specific_memories_map,
#             player_specific_context=player_specific_context_map,
#         ),
#     )

#     SocialMediaGM = GameMasterConfig(
#         prefab=SOCIAL_MEDIA_GAMEMASTER_FILENAME + "__GameMaster",
#         params=SocialMediaParams(
#             name="mastodon",
#             app_module=sim.app_module,
#             call_to_action_str=CALL_TO_ACTION,
#             sm_user_data=sm_user_data,
#             use_server=USE_SERVER,
#             app_description=SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
#         ),
#     )

#     soc_sys = SocSysConfig(
#         call_to_action=CALL_TO_ACTION,
#         exp_name=experiment_name,
#         game_masters=[SocialMediaGM, InitializerGM],
#         setting_info=setting_info,
#         shared_agent_memories_template=shared_memories,
#         scenario_name=SCENARIO_NAME,
#         social_media_usage_instructions=SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
#     )

#     return soc_sys

#     # # Get Game Master Entities list
#     # entity_game_master_instance_list = []

#     # entity_game_master_instance_list.append(
#     #     prefab_lib.InstanceConfig(
#     #         prefab="formative_memories_initializer__GameMaster",
#     #         role=prefab_lib.Role.INITIALIZER,
#     #         params={
#     #             "name": "initial setup rules",
#     #             "next_game_master_name": social_media_gm_name,
#     #             "shared_memories": shared_memories,
#     #             "player_specific_memories": player_specific_memories_map,
#     #             "player_specific_context": player_specific_context_map,
#     #         },
#     #     )
#     # )

#     # # Add Social Media Game Master
#     # gm_module = importlib.import_module(
#     #     "sim.agent_utils." + cfg.sim.social_media_gamemaster_filename
#     # )
#     # entity_map[cfg.sim.social_media_gamemaster_filename + "__GameMaster"] = gm_module.GameMaster()
#     # entity_game_master_instance_list.append(
#     #     prefab_lib.InstanceConfig(
#     #         prefab=cfg.sim.social_media_gamemaster_filename + "__GameMaster",
#     #         role=prefab_lib.Role.GAME_MASTER,
#     #         params={
#     #             "name": social_media_gm_name,
#     #             "app_module": cfg.sim.app_module,
#     #             "call_to_action_str": cfg.soc_sys.call_to_action,
#     #             "sm_user_data": sm_user_data,
#     #             "use_server": cfg.sim.use_server,
#     #             "app_description": cfg.soc_sys.social_media_usage_instructions,
#     #             "output_path": cfg.sim.output_rootname,
#     #         },
#     #     )
#     # )

#     # # Add Survey Game Master
#     # # Convert to questionnaires
#     # # probe_event_logger = EventLogger(
#     # #     "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
#     # # )
#     # # probes_config = OmegaConf.to_container(cfg.probes, resolve=True)
#     # # questionnaires, query_questionnaire = create_interviewer_gm_with_queries(
#     # #     probes_config=probes_config,
#     # #     player_names=entity_player_names
#     # # )
#     # # entity_map["survey_probe__GameMaster"] = game_master_prefabs.interviewer.GameMaster()
#     # # entity_game_master_list.append(
#     # #     prefab_lib.InstanceConfig(
#     # #         prefab="interviewer__GameMaster",
#     # #         role=prefab_lib.Role.GAME_MASTER,
#     # #         params={
#     # #             "name": "InterviewerGM",
#     # #             "player_names": entity_player_names,
#     # #             "questionnaires": questionnaires,  # Your converted queries
#     # #             "verbose": False,
#     # #         },
#     # #     )
#     # # )

#     # exogenous_agent_instance_list = []  # remove once exogeneous agents set up


# def generate_scenario_configs(sim):
#     """
#     Generate all scenario-specific configs from sim config.

#     Args:
#         sim: SimConfig instance

#     Returns
#     -------
#         Tuple of (SocSysConfig, ProbesConfig, AgentsConfig)
#     """
#     # Generate agents config (returns agents WITHOUT news agents in directory)
#     agents, news_agent_configs, news_info, roles = get_agents_config(sim)

#     # # Generate gamemaster memories
#     # gamemaster_memories = [
#     #     f"{agent.name} is at their private home." for agent in agents.directory
#     # ] + [f"The workday begins for the {agent.name}" for agent in news_agent_configs]

#     # # Join non-news and news agents (matching old behavior)
#     # agents.directory = agents.directory + news_agent_configs

#     # Generate social system config
#     soc_sys = get_soc_sys_config(sim, news_info, roles)

#     # Generate probes config - get candidate names from CANDIDATE_INFO
#     candidates = [CANDIDATE_INFO[p]["name"] for p in PARTISAN_TYPES]

#     probes = ProbesConfig(
#         queries_data={
#             0: QueryData(
#                 query_type="VotePref",
#                 interaction_premise_template=InteractionPremiseTemplate(
#                     candidate1=candidates[0], candidate2=candidates[1]
#                 ),
#             ),
#             1: QueryData(
#                 query_type="Favorability",
#                 interaction_premise_template=InteractionPremiseTemplate(candidate=candidates[0]),
#             ),
#             2: QueryData(
#                 query_type="Favorability",
#                 interaction_premise_template=InteractionPremiseTemplate(candidate=candidates[1]),
#             ),
#             3: QueryData(query_type="VoteIntent"),
#         }
#     )

#     return soc_sys, probes, agents
