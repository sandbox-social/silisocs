import json
import logging
import threading
from typing import cast

from concordia.utils import html as html_lib
from hydra.core.hydra_config import HydraConfig
from IPython import display
from omegaconf import DictConfig

file_lock = threading.Lock()
import warnings

with warnings.catch_warnings():
    warnings.filterwarnings("ignore")
    import sentence_transformers


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


def read_token_data(file_path):
    try:
        with open(file_path) as file:
            data = json.load(file)
            return data
    except FileNotFoundError:
        return {"prompt_tokens": 0, "completion_tokens": 0}


def post_analysis(env, model, agents, roles, store_data, output_rootname):
    memories = {}
    for agent in agents:
        if roles[agent_agent_name]:
            memories[agent._agent_name] = store_data[agent._agent_name]

    all_gm_memories = env.memory.retrieve_recent(k=10000, add_time=True)

    detailed_story = "\n".join(all_gm_memories)
    print("len(detailed_story): ", len(detailed_story))
    # print(detailed_story)

    episode_summary = model.sample_text(
        f"Sequence of events:\n{detailed_story}"
        "\nNarratively summarize the above temporally ordered "
        "sequence of events. Write it as a news report. Summary:\n",
        max_tokens=3500,
        terminators=(),
    )
    print(episode_summary)

    # Summarise the perspective of each agent
    agent_logs = []
    agent_log_names = []
    for agent in agents:
        name = agent._agent_name
        detailed_story = "\n".join(
            memories[agent._agent_name].retrieve_recent(k=1000, add_time=True)
        )
        summary = ""
        summary = model.sample_text(
            f"Sequence of events that happened to {name}:\n{detailed_story}"
            "\nWrite a short story that summarises these events.\n",
            max_tokens=3500,
            terminators=(),
        )

        all_agent_mem = memories[agent._agent_name].retrieve_recent(k=1000, add_time=True)
        all_agent_mem = ["Summary:", summary, "Memories:", *all_agent_mem]
        agent_html = html_lib.PythonObjectToHTMLConverter(all_agent_mem).convert()
        agent_logs.append(agent_html)
        agent_log_names.append(f"{name}")

    # ## Build and display HTML log of the experiment
    gm_mem_html = html_lib.PythonObjectToHTMLConverter(all_gm_memories).convert()

    tabbed_html = html_lib.combine_html_pages(
        [gm_mem_html, *agent_logs],
        ["GM", *agent_log_names],
        summary=episode_summary,
        title="Mastodon experiment",
    )

    tabbed_html = html_lib.finalise_html(tabbed_html)
    with open(output_rootname + "_summary.html", "w", encoding="utf-8") as f:
        f.write(tabbed_html)

    display.HTML(tabbed_html)


class ConfigStore:
    _config: DictConfig | None = None

    @classmethod
    def set_config(cls, cfg: DictConfig) -> None:
        cls._config = cfg

    @classmethod
    def get_config(cls) -> DictConfig:
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
