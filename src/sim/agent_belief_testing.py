import concurrent.futures
import datetime
import importlib
import logging
import os
import random
import sys
import time
import warnings
from functools import partial
from pathlib import Path

import hydra
from concordia import __file__ as concordia_location
from omegaconf import DictConfig, OmegaConf, open_dict

print(f"importing Concordia from: {concordia_location}")
warnings.filterwarnings(action="ignore", category=FutureWarning, module="concordia")

# concordia functions
from concordia.clocks import game_clock

# Go up two levels to set current working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
print("project root: " + str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sim.sim_utils.agent_speech_utils import (
    deploy_probes,
)
from sim.sim_utils.concordia_utils import (
    build_agent_with_memories,
    generate_concordia_memory_objects,
    make_profiles,
)

# sim functions
from sim.sim_utils.media_utils import select_large_language_model
from sim.sim_utils.misc_sim_utils import (
    ConfigStore,
    EventLogger,
    StdoutToLogger,
    get_sentance_encoder,
)


def run_sim(
    model,
    embedder,
    output_post_analysis=False,
    save_checkpoints=True,
    load_from_checkpoint_path="",
):
    cfg = ConfigStore.get_config()
    app_description = cfg.soc_sys.social_media_usage_instructions
    episode_call_to_action = cfg.soc_sys.episode_call_to_action
    setting_info = cfg.soc_sys.setting_info
    num_episodes = cfg.sim.num_episodes
    use_server = cfg.sim.use_server

    time_step = datetime.timedelta(minutes=30)
    today = datetime.date.today()
    SETUP_TIME = datetime.datetime(year=today.year, month=today.month, day=today.day, hour=8)  # noqa: DTZ001
    START_TIME = datetime.datetime(year=today.year, month=today.month, day=today.day, hour=8)  # noqa: DTZ001
    clock = game_clock.MultiIntervalClock(
        start=SETUP_TIME, step_sizes=[time_step, datetime.timedelta(seconds=10)]
    )

    # set probe settings
    probes = OmegaConf.to_container(cfg.probes, resolve=True)

    # build agent models
    agent_data = OmegaConf.to_container(cfg.agents.directory, resolve=True)
    get_idx = lambda name: [ait for ait, agent in enumerate(agent_data) if agent["name"] == name][0]

    profiles, roles = make_profiles(agent_data)  # profile format: (agent_config,role)
    role_parameters = setting_info["details"]["role_parameters"]

    shared_memories = (
        cfg.soc_sys.shared_agent_memories_template
        + [cfg.soc_sys.setting_info.description]
        + [cfg.soc_sys.social_media_usage_instructions]
    )
    (
        importance_model,
        importance_model_gm,
        blank_memory_factory,
        formative_memory_factory,
        gamemaster_memory,
    ) = generate_concordia_memory_objects(
        model,
        embedder,
        shared_memories,
        cfg.soc_sys.gamemaster_memories,
        clock,
    )

    action_event_logger = EventLogger(
        "action", os.path.join(cfg.sim.output_rootname, "action_events.jsonl")
    )
    action_event_logger.episode_idx = -1
    # build agents
    agents = []
    local_post_analyze_data = {}
    obj_args = (formative_memory_factory, model, clock, time_step, setting_info)
    build_agent_with_memories_part = partial(build_agent_with_memories, obj_args)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(profiles)) as pool:
        for agent_obj in pool.map(build_agent_with_memories_part, profiles.values()):
            agent, data = agent_obj
            agents.append(agent)
            local_post_analyze_data[agent._agent_name] = data
    # add agent-specific configuration
    for agent in agents:
        if roles[agent._agent_name] == "exogenous":
            # assign seed toots of exogenous agents with absolute path to images (if non empty)
            agent.seed_toot = agent_data[get_idx(agent._agent_name)]["seed_toot"]
            for post_text in agent.posts:
                agent.posts[post_text] = [
                    str(PROJECT_ROOT) + "/" + path for path in agent.posts[post_text]
                ]
        else:
            for observation in cfg.agents.initial_observations:
                agent.observe(observation.format(name=agent._agent_name))

    # initialize
    probe_event_logger = EventLogger(
        "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
    )

    # main loop
    start_time = time.time()  # Start timing
    model.agent_names = [
        agent._agent_name for agent in agents
    ]  # needed for tagging names to thoughts
    for i in range(num_episodes):
        model.meta_data["episode_idx"] = i
        probe_event_logger.episode_idx = i
        headlines = [
            "Bill Fredrickson Champions Proven Industry Leaders While Carter Picks Diplomat for Environmental Role",
            "Bill Fredrickson Champions Local Business Interests by Exiting Burdensome Climate Agreement, Opening New Opportunities for Growth",
            "Fredrickson Champions Industry Growth by Blocking Carter's Regulation Overreach",
            "Bill Fredrickson Championing Job Growth and Fiscal Stability, While Carter Seeks Expensive Government Programs for Storhampton",
            "Fredrickson Champions Economic Revitalization Through Pro-Industry Tax Incentives, Supporting Storhampton's Growth",
            "Fredrickson's Strategic Climate Decision Could Strengthen Local Economy More Than Ever",
            "Fredrickson Showcases Economic Growth with Record Production, Offers Promising Development Plans",
            "Fredrickson's Strategic Leadership: Fostering Job Growth Through Business Incentives",
            "Fredrickson Champions Economic Revival Through Strategic Local Industry Support",
            "Fredrickson's Economic Vision Promises Job Growth for Storhampton's Future",
            "Bill Fredrickson Champions Economic Growth While Carter Suggests Risky Environmental Regulations",
            "Fredrickson Champions Economic Growth with Strategic Tax Incentives for Storhampton's Industries",
            "Fredrickson's Plan: Sparking Economic Growth with Local Tax Incentives",
            "Bill Fredrickson Advocates for Balanced Growth: An Alternative to Carter's Environmental Restrictions",
            "Storhampton Industries Rally Behind Fredrickson's Economic Revival Plan",
            "Fredrickson Prioritizes Economic Growth as Carter Proposes Expanding Social Benefits",
            "Fredrickson Champions Growth with Tax Incentives to Revitalize Storhampton's Proud Textile Heritage",
            "Bill Fredrickson Champions Pro-Business Solutions as Carter Advocates for Bureaucratic Office Reallocation in Storhampton",
        ]
        for agent in agents:
            if roles[agent._agent_name] != "exogenous":
                agent.observe(headlines[i])
        print(f"Episode: {i}. Deploying survey...", end="")
        deploy_probes(
            [agent for agent in agents if roles[agent._agent_name] != "exogenous"],
            probes,
            probe_event_logger,
        )
        print("complete")


def configure_logging(logger):
    # supress verbose printing of hydra's api logging so only warnings (or greater issues) are printed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Redirect stdout to the logger
    sys.stdout = StdoutToLogger(logger)


@hydra.main(version_base=None, config_path="../../conf", config_name="config")
def main(cfg: DictConfig):
    OmegaConf.set_struct(cfg, True)
    with open_dict(cfg):
        # Construct output_rootname using os.path.join for platform independence
        cfg.sim.output_rootname = os.path.join(
            hydra.core.hydra_config.HydraConfig.get().runtime.output_dir,
            hydra.core.hydra_config.HydraConfig.get().job.name,
        )
    ConfigStore.set_config(cfg)

    logger = logging.getLogger(__name__)
    configure_logging(logger)

    package = importlib.import_module(cfg.sim.example_name)
    sys.modules["sim_setting"] = package
    SEED = cfg.sim.seed
    random.seed(SEED)

    # load language models
    model = select_large_language_model(
        cfg.sim.model, os.path.join(cfg.sim.output_rootname, "prompts_and_responses.jsonl"), True
    )
    embedder = get_sentance_encoder(cfg.sim.sentence_encoder)

    # run sim
    run_sim(
        model,
        embedder,
        load_from_checkpoint_path=cfg.sim.load_path,
    )


if __name__ == "__main__":
    # # parse input arguments
    # parser = argparse.ArgumentParser(description="input arguments")
    # # parser.add_argument("--load_path", type=str, default="", help="path to saved checkpoint folder")
    # parser.add_argument(
    #     "--example_name", type=str, default="election", help="path to saved checkpoint folder"
    # )
    # args = parser.parse_args()
    sys.path.insert(0, str(PROJECT_ROOT / "examples"))
    main()  # config)
