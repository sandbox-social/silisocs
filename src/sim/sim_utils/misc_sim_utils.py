import importlib
import json
import logging
import os
import sys
import threading
from typing import Any, cast

from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig

file_lock = threading.Lock()
import warnings

with warnings.catch_warnings():
    warnings.filterwarnings("ignore")
    import sentence_transformers


def write_concordia_logs(results_log, output_rootname):
    file_path = os.path.join(output_rootname, "logs.html")
    try:
        with open(file_path, "w", encoding="utf-8") as html_file:
            html_file.write(results_log)
        print(f"HTML content successfully saved to {file_path}")
    except OSError as e:
        print(f"Error saving HTML content: {e}")


def get_prefab_instance(entity_prefab, module_path):
    print(f"[Loader] Loading prefab: {entity_prefab} from {module_path}")
    entity_name, entity_type = entity_prefab.split("__")
    try:
        # e.g. importlib.import_module("scenarios.election.entity_lib.voter")
        build_entity_module = importlib.import_module(module_path)
        # e.g., getattr(module, "Entity")
        build_entity_class = getattr(build_entity_module, entity_type)
        print(type(build_entity_class))
    except ImportError:
        print(f"Error: Could not import module: {entity_name}")
    except AttributeError:
        print(f"Error: Module {entity_name} does not have class: {entity_type}")
    except Exception as e:
        print(f"An error occurred while loading prefab {entity_prefab}: {e}")
    # return the *instantiated* class
    return build_entity_class()


# Create a custom StreamHandler that redirects stdout to the logger
class StdoutToLogger:
    def __init__(self, logger, log_level=logging.INFO):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ""

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line.rstrip())

    def flush(self):
        pass


def get_sentence_encoder(model_name):
    # Setup sentence encoder
    st_model = sentence_transformers.SentenceTransformer(model_name)
    embedder = lambda x: st_model.encode(x, show_progress_bar=False)
    return embedder


def write_item(out_item, output_filename):
    try:
        with file_lock, open(output_filename, "a") as f:
            json_str = json.dumps(out_item)  # Separate this step for debugging
            print(json_str, file=f)
            print(f"Successfully wrote item with type: {out_item.get('label')}")  # Debug print
    except Exception as e:
        print(f"Error in write_item: {e}")
        print(
            f"Problem item: {type(out_item)}, keys: {out_item.keys() if isinstance(out_item, dict) else 'not a dict'}"
        )


class EventLogger:
    def __init__(self, event_type, output_filename):
        self.episode_idx = None
        self.output_filename = output_filename
        self.type = event_type
        self.dummy = None

    def log(self, log_data):
        if isinstance(log_data, list):
            for log_item in log_data:
                log_item["episode"] = self.episode_idx
                log_item["event_type"] = self.type
                write_item(log_item, self.output_filename)
        else:
            log_data["episode"] = self.episode_idx
            log_data["event_type"] = self.type
            if self.type == "action":
                log_data["data"]["suggested_action"] = self.dummy

            write_item(log_data, self.output_filename)


class ConfigStore:
    _config: Any = None  # DictConfig | None = None

    @classmethod
    def set_config(cls, cfg: DictConfig | Any) -> None:
        cls._config = cfg

    @classmethod
    def get_config(cls) -> Any:
        # Try to get from local store first
        if cls._config is not None:
            return cls._config

        # Try to get from Hydra
        try:
            # Access the config using getattr to avoid mypy error
            hydra_conf = HydraConfig.get()
            config = getattr(hydra_conf, "config", None)
            if config is not None:
                return cast(DictConfig, config)
            raise ValueError("Config not found in HydraConfig")
        except ValueError:
            raise RuntimeError("Configuration not initialized. Run main script first.")


def configure_logging(logger):
    # supress verbose printing of hydra's api logging so only warnings (or greater issues) are printed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Redirect stdout to the logger
    sys.stdout = StdoutToLogger(logger)


# def read_token_data(file_path):
#     try:
#         with open(file_path) as file:
#             data = json.load(file)
#             return data
#     except FileNotFoundError:
#         return {"prompt_tokens": 0, "completion_tokens": 0}


# def post_analysis(env, model, agents, roles, store_data, output_rootname):
#     memories = {}
#     for agent in agents:
#         if roles[agent._agent_name]:
#             memories[agent._agent_name] = store_data[agent._agent_name]

#     all_gm_memories = env.memory.retrieve_recent(k=10000, add_time=True)

#     detailed_story = "\n".join(all_gm_memories)
#     print("len(detailed_story): ", len(detailed_story))
#     # print(detailed_story)

#     episode_summary = model.sample_text(
#         f"Sequence of events:\n{detailed_story}"
#         "\nNarratively summarize the above temporally ordered "
#         "sequence of events. Write it as a news report. Summary:\n",
#         max_tokens=3500,
#         terminators=(),
#     )
#     print(episode_summary)

#     # Summarise the perspective of each agent
#     agent_logs = []
#     agent_log_names = []
#     for agent in agents:
#         name = agent._agent_name
#         detailed_story = "\n".join(
#             memories[agent._agent_name].retrieve_recent(k=1000, add_time=True)
#         )
#         summary = ""
#         summary = model.sample_text(
#             f"Sequence of events that happened to {name}:\n{detailed_story}"
#             "\nWrite a short story that summarises these events.\n",
#             max_tokens=3500,
#             terminators=(),
#         )

#         all_agent_mem = memories[agent._agent_name].retrieve_recent(k=1000, add_time=True)
#         all_agent_mem = ["Summary:", summary, "Memories:", *all_agent_mem]
#         agent_html = html_lib.PythonObjectToHTMLConverter(all_agent_mem).convert()
#         agent_logs.append(agent_html)
#         agent_log_names.append(f"{name}")

#     # ## Build and display HTML log of the experiment
#     gm_mem_html = html_lib.PythonObjectToHTMLConverter(all_gm_memories).convert()

#     tabbed_html = html_lib.combine_html_pages(
#         [gm_mem_html, *agent_logs],
#         ["GM", *agent_log_names],
#         summary=episode_summary,
#         title="Social media experiment",
#     )

#     tabbed_html = html_lib.finalise_html(tabbed_html)
#     with open(output_rootname + "_summary.html", "w", encoding="utf-8") as f:
#         f.write(tabbed_html)

#     display.HTML(tabbed_html)
