APP_MODULE_PATH = "mastodon_sim"
LLM_NAME = "gpt-4o-mini"
NUM_AGENTS = 20
NUM_STEPS = 1
RUN_NAME = "run1"
SEED = 1
SENTENCE_ENCODER = "sentence-transformers/all-mpnet-base-v2"
ROLEPLAYING_INSTRUCTIONS = (
    "<general_instructions> \n"
    "You are simulating {name}, a character in a social science experiment. \n"
    "Always use third-person limited perspective when describing {name}'s thoughts and actions. \n"
    "Your goal is to determine the single most appropriate action {name} would take next. \n"
    "</general_instructions> \n"
)
SCENARIO_NAME = ""  # set in scenario class
