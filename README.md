[![CI](https://github.com/social-sandbox/mastodon-sim/actions/workflows/test.yml/badge.svg)](https://github.com/social-sandbox/mastodon-sim/actions/workflows/test.yml)
[![Open in Dev Containers](https://img.shields.io/static/v1?label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/social-sandbox/mastodon-sim)

# Social Simulation Sandbox for Social Media Scenarios

Configurable generative agent simulation of social media using the [Concordia framework](https://github.com/google-deepmind/concordia).
- 2024 NeurIPS Workshop Paper: [http://arxiv.org/abs/2410.13915](http://arxiv.org/abs/2410.13915).
- 2025 Version 1 demo paper: [https://www.ijcai.org/proceedings/2025/1271][https://www.ijcai.org/proceedings/2025/1271].
- Version 2 complete (structured scenario configuration compatible with Concordia v.2)

## Code Overview

### Files
simulation code is in  `mastodon-sim/src/` within which
- `conf/` holds all the yaml configuration files
- `sim/` is the multi-LLM-agent+environment simulation library (contains a social media environment as an example)
- `scenarios/` has scenario-specific configuration logic (e.g. scenario data classes and agent factories)
- `mastodon_sim/` is the library of tools for running and deploying the (in this case Mastodon) social media server.

`mastodon-sim/src/sim/main.py` runs the simulation orchestrated in `conf/config.yaml`

### Configuration
We provide an example that simulates an election simulation as a specific scenario.
There are 4 yaml files listed in `config.yaml` that configure the simulation. Here they are, along with what they consist of in the case of the election scenario:

| config file | description | election scenario |
| ----------- | ----------- | ---------------- |
| `sim.yaml` | This contains multi-LLM agent simulator attributes like the language model and how long to run the simulation for and. It has no scenario or environment information. | N/A |

| `social_media.yaml` | all detailed structured information about the environment. | N/A |
| `scenario.yaml` | All the configuration associated with the specific scenario (agents and setting). This includes detailed structured information about the social system: the shared knowledge and social context of the agents. We even have custom query types (with formatting from dynamic values). We currently use this function to deploy longitudinal surveys on the agent population. | We made 3 custom agents: `voter.py`, `candidate.py`, and `malicious.py`. We made 3 query types: `Favorability`, `VotePreference`, `IntentToVote`. We formed two versions of the first two types, one for each candidate. |

All config generation is orchestrated in `mastodon-sim/src/sim/config_utils/config_schema.py`.
We use the Hydra package to manage config files, run simulations, and log output. See the [hydra documentation](https://hydra.cc/docs/intro/) for more details. One simple use of hydra is to override default parameter values by including them in the command line. More structured experiments can be made with customized yaml subconfig files.

### Output
Simulation output is a set of files located in the respective scenarios folder, here `mastodon-sim/scenarios/election/outputs/run_name/` where `run_name` is a parameter in `sim.yaml`.

| Output file | Description |
| ----------- | ----------- |
| `events.json` | stores agent actions|
| `probes.json` | stores probe results |
| `prompts_and_responses.jsonl` | stores language model prompts and responses |
| `.hydra/config.yaml` | stores all config information into a single yaml file |

`mastodon-sim/src/sim/analysis_utils/dashboard/` holds a set of scripts whose main element `main.py` loads a browser-based dashboard from which output data files are loaded in and automated analytics including dynamic social networks are presented for the user to analyze the results. Here is a snapshot:

![alt text](https://github.com/social-sandbox/mastodon-sim/blob/main/docs/img/dashboard_screenshot.png?raw=true)

## Detailed file structure:
<pre>
mastodon-sim/
└── src/
    ├── conf/
    │   ├── config.yaml
    │   ├── sim/
    |   |   └── base.yaml
    │   ├── social_media/
    |   |   └── mastodon.yaml
    │   └── scenario/
    |       └── election.yaml
    ├── mastodon_sim/
    │   ├── apps.py
    │   ├── logging.py
    │   ├── mastodon_ops/
    │   │   ├── block.py
    │   │   ├── boost.py
    │   │   ├── create_app.py
    │   │   ├── create_env_file.py
    │   │   ├── delete_posts.py
    │   │   ├── env_utils.py
    │   │   ├── follow.py
    │   │   ├── get_account_id.py
    │   │   ├── get_client.py
    │   │   ├── like.py
    │   │   ├── login.py
    │   │   ├── mute.py
    │   │   ├── new_app.py
    │   │   ├── notifications.py
    │   │   ├── post_status.py
    │   │   ├── read_bio.py
    │   │   ├── reset_users.py
    │   │   ├── timeline.py
    │   │   ├── toot.py
    │   │   ├── unblock.py
    │   │   ├── unfollow.py
    │   │   ├── unmute.py
    │   │   └── update_bio.py
    │   └── mastodon_utils/
    │       ├── account_ids.py
    │       ├── create_app.py
    │       ├── get_users_from_env.py
    │       └── graphs.py
    ├── sim/
    |   ├── main.py
    |   ├── config_utils/
    |   |   ├── agent_builders.py
    |   |   ├── scenario_schema.py
    |   |   ├── simulation_dataclasses.py
    |   |   ├── social_media_dataclasses.py
    |   |   └── social_media_functions.py
    |   ├── sim_utils/
    |   |   ├── agent_speech_utils.py
    |   |   ├── media_utils.py
    |   |   └── misc_sim_utils.py
    |   ├── analysis_utils/
    |   |   └── dashboard/
    |   |       └──main.py
    |   ├── entities/
    |   |   ├──social_media.py
    |   |   ├──simple.py
    |   |   └── components/
    |   |       └── gm_social_act.py
    |   |       └── social_make_observations.py
    |   └── engines/
    |       └── social_media_engine.py
    └── scenarios/
        └── election/
            ├── builders.py
            ├── scenario_dataclasses.py
            ├── config_utils/
            |    └── probe_lib.py
            ├── entity_lib/
            |    └── role_1.py
            ├── input/
            |    └── source_1.py
            └── output/
                └── run_type_1/
                    └── run_type_1_time/
                        └── run_type_1_time_outputtype_A.json
</pre>
<!--
## Hidden Section

## Installing

To install this package, run:

```sh
pip install mastodon-sim
```

-->

## Development Installation

1. Clone the repository:

    ```sh
    git clone https://github.com/social-sandbox/mastodon-sim.git
    cd mastodon-sim
    ```

2. Install Poetry (for managing dependencies):

    ```sh
    curl -sSL https://install.python-poetry.org | python3 -
    ```

    Note that poetry offers several [alternative installation methods](<https://python-poetry.org/docs/#installation}>).

3. Configure Poetry to create virtual environments within the project directory:

    ```sh
    poetry config virtualenvs.in-project true
    ```

4. Install the dependencies:

    ```sh
    poetry install
    ```

## Environment Variables

The application relies on a `.env` file to manage sensitive information and configuration settings. This file should be placed in the root directory of your project (`mastodon-sim/`) and contain key-value pairs for required environment variables. The `dotenv` library is used to load these variables into the environment.

### Example `.env` File

Below is an scenario of what your `.env` file might look like. Make sure to replace the placeholder values with your actual configuration. Remember not to add this file to the project so as not to expose this sensitive information like client IDs, secrets, and passwords.

```dotenv
# Mastodon API base URL
API_BASE_URL=https://<domain_name>

# Mastodon client credentials
MASTODON_CLIENT_ID=*************************0
MASTODON_CLIENT_SECRET=*********************************o

# Email prefix for user accounts
EMAIL_PREFIX=<email_prefix>

# Bot user passwords
USER001_PASSWORD=***************************5
USER002_PASSWORD=***************************8
```

<!--
## Hidden Section

## Using

To view the CLI help information, run:

```sh
mastodon-sim --help
```

-->
