import importlib
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def generate_sim_config(example_name):
    # Required:
    default_sim_config = {}
    default_sim_config["seed"] = 1  # seed used for python's random module"
    default_sim_config["num_agents"] = 20  # number of agents
    default_sim_config["num_episodes"] = 1  # number of episodes
    default_sim_config["sentence_encoder"] = (
        "sentence-transformers/all-mpnet-base-v2"  # select sentence embedding model
    )
    default_sim_config["model"] = "gpt-4o-mini"  # select language model to run sim
    default_sim_config["run_name"] = "run1"  # experiment label
    default_sim_config["app_module"] = "mastodon_sim"
    default_sim_config["social_media_gamemaster_filename"] = "social_media_game_master"
    default_sim_config["load_path"] = ""
    default_sim_config["roleplaying_instructions"] = (
        "<system>"
        "You are simulating {name}, a character in a social science experiment. Always use third-person limited perspective when describing {name}'s thoughts and actions. Your goal is to determine the single most appropriate action {name} would take next on a social media platform."
        "</system>"
    )

    # Example specific:
    default_sim_config["example_name"] = example_name
    default_sim_config["use_server"] = (
        False  # server (e.g. www.social-sandbox.com, www.socialsandbox2.com)
    )
    default_sim_config["use_news_agent"] = (
        "with_images"  # use news agent in the simulation 'with_images', else without
    )
    default_sim_config["persona_type"] = "Reddit.Big5"  # persona

    return default_sim_config


def generate_remaining_and_write_configs(sim: dict):
    sys.path.insert(0, str(PROJECT_ROOT / "examples" / sim["example_name"]))

    # example functions
    example_module = importlib.import_module("gen_config_new")
    soc_sys, probes, agents = example_module.generate_output_configs(sim)

    # generate name for run
    config_label = "_".join(
        [
            "N${sim.num_agents}",
            "T${sim.num_episodes}",
            "${sim.persona_type}",
            "${soc_sys.exp_name}",
            "${agents.inputs.news_file}",
            "${sim.use_news_agent}",
            "${sim.run_name}",
        ]
    )

    # write heirarchy of configs to conf
    data_config = {"soc_sys": soc_sys, "probes": probes, "agents": agents, "sim": sim}

    # configure hydra
    ccfg: dict[str, Any] = {}
    ccfg["defaults"] = []
    for name, cfgg in data_config.items():
        ccfg["defaults"].append({name: sim["example_name"] + "_" + name})
        output_file = Path("conf/" + name + "/" + sim["example_name"] + "_" + name + ".yaml")
        output_file.parent.mkdir(exist_ok=True, parents=True)
        with open(output_file, "w") as outfile:
            print("writing " + str(output_file))
            yaml.dump(cfgg, outfile, default_flow_style=False)

    ccfg["hydra"] = {}
    ccfg["hydra"]["job"] = {}
    ccfg["hydra"]["job"]["name"] = config_label + "_" + "${now:%Y-%m-%d_%H-%M-%S}"
    ccfg["hydra"]["run"] = {}
    ccfg["hydra"]["run"]["dir"] = "examples/" + "${sim.example_name}/" + "outputs/" + config_label
    ccfg["hydra"]["output_subdir"] = "configs/" + config_label

    with open("conf/config.yaml", "w") as outfile:
        yaml.dump(ccfg, outfile, default_flow_style=False)


if __name__ == "__main__":
    example_name = "election"
    sim_cfg = generate_sim_config(example_name)
    generate_remaining_and_write_configs(sim_cfg)
