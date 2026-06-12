## v0.2.0 (2026-06-12)

### ♻️  Refactorings

- **scenarios**: renamed scenarios to be object that holds configs together
- **config**: rename scenario→world throughout codebase

### fix

- **config**: fix missed evals->eval renames in evaluators and study artifacts

### refactor

- **config**: rename evals config group to eval

### 📝💡 Documentation

- **AGENTS**: add branching rules and gitmoji commit style

## v0.1.0 (2026-06-01)

### ⚡️ Performance

- **backends,-runtime**: removed agents having to pass their own names when executing funcitons, made agent names requires by the end of builder procedure

### docs

- **agents**: merge CLAUDE.md into AGENTS.md, generalize skills docs

### fix

- **docs**: restore silisocs package name stripped by case-change commit

### 📝💡 Documentation

- **election,-building_agents-docs**: update docs

## v0.1.0-alpha (2026-05-31)

### ✨ Features

- **backends**: made generic backends more ffeature complete for release
- **replications/**: added replciaiton of echo chamber sim study, made minor changes in src/ to improve extensibility
- **fixed_entity,-backends,-engine,-gm**: fixed logic across files, updated fixed_entity logic, multi-flow logic
- **social_media_backends**: added two local social media backends and generalized usage
- Incremental updates to version functioning - added different network initializations, new visualization dashboard, timing statistics
- **social-media-files**: added 2-state Markov process for user activity >>> ⏰ 1h
- **main,-simulation,-gm_social_act,-agent-models**: added ability to use multiple language models in sim
- **dashboard**: dashboard working >>> ⏰ 3h
- **social_media_engine.py;-gm_social_act.py**: added next_acting to select active agents and added next_game_master to cycle through game masters >>> ⏰ 2h
- moved to structured configs; removed example from sim code; mastodon_sim based on top-level apps.py >>> ⏰ 5h
- **all-main**: streamlined main.py by making a set_app_state function in social_media_game_master >>> ⏰ 2h
- **all-main**: streamlined main.py by making a set_app_state function in social_media_game_master >>> ⏰ 2h
- **main_new.py;social_media_game_master;-dashboard-file**: added model prompts and responses logging; cosmetic lift to main.py; fixed use_server typo >>> ⏰ 1h
- **social_media_engine.py**: added in the survey probe code directly while loop >>> ⏰ 1h
- **app_side_only_gamemaster_wo_thought**: added a game master without outer thought loop to improve performance
- **analysis.py,-replay_actions,-single_agent**: added a simple analysis app, changes to replay agent funcitonality to select agent and episode
- **replay_actions,-single_agent_testing**: added save,load. fixed minor bugs, created single agent setting
- **lots**: plans now in output/dashboard; call to actions/max steps in input >>> ⏰ 4h
- **main.py-gen_config.py**: combines existing work on image-augmented news_agent >>> ⏰ 1h
- **dashboard-basic.py**: Improved dashbaord presentation and changed graph
- **dashboard-basic.py**: updated dashboard structure and simplified usage
- **agent_pop_utils.py;main.py**: added N=100 agent config generation; also added exp as input arg to main.py >>> ⏰ 1h
- **scene.py-triggering.py-apps.py-Concordia+Mastodon_100.ipynb**: attempted to make triggering less dependent on the players, added boost feature and removed view_profile feature
- **Concordia+Mastodon_100.ipynb**: added code for parallelization and saving player states
- **Concordia+Mastodon_100.ipynb,-independent_configs.json**: added Gayatri's config file and edited to add fields, added policy proposal to context in agent building
- **Concordia+Mastodon_100.ipynb**: added config features and customization to notebook
- **Concordia+Mastodon_100.ipynb**: Added a config file and method to load personas into sim + Updated polling/voting feature
- add calendar example; update mastodon example
- add Mastodon API throttling modification scripts
- update concordia+mastodon example; update concordia to main
- add Mastodon phone app and notebook example
- add graph generation sample notebook
- add util to write all code to single markdown file
- add pyvis follow graph; fix get users from env; parallelize reset users
- add timeline filters; add mute/unmute operation
- add notifications operations
- **mastodon_ops**: add reply/media/poll/DM/sensitive/warning posts; add delete posts; add reset user
- add create_env_file.py and create_users.py; update readme and notebook example
- **mastodon_ops**: added update and read display_name and bio operations

### 🐛🚑️ Fixes

- **experiments/**: changed runner path to silisocs from mastodon-sim
- **resolve.py-backends,-other-minor-changes**: fixed tool calling, made general bug fixes
- **fix**: dashboard >>> ⏰ dashboard
- **simulation.py;-main.py**: fixed small bug; now runs >>> ⏰ 10m
- **configs**: fixed previous commit where I forgot to add scenario.yaml >>> ⏰ 15m
- **configs**: fixed call to action formatting >>> ⏰ 1h
- **config-files**: config naming/structure >>> ⏰ 1h
- **config-and-storing**: fixed and updated output storage >>> ⏰ 1h
- fixing merge conflicts
- **gm_social_act**: fixed username, parsing bug
- **new-files**: fixed some bugs. Code now terminates. call to action still buggy >>> ⏰ 3h
- **dashboard-withthoughts.py**: updated dashboard to work with new output structure
- **plotting/**: fixed plotting scripts to accept input arguments
- **base_agent.py**: fixed bugs with loading agent, improved efficiency of replay_actions
- **main.py;-dashboard;-others**: fixed bug in main.py in logging action plans; beautified dashboard; updated readme >>> ⏰ 5h
- **lots**: improved storing locations, names, and hydra options >>> ⏰ 3h
- **lots**: forgot to add in the last commit >>> ⏰ 5m
- **concordia_utils.py**: fixed agent config "role" attribute as the agent class >>> ⏰ 1h
- **gen_config.py**: aded root path to reddit json >>> ⏰ 5m
- **dashboard-basic.py**: fixed normalization and added episode -1 in dashboard plots. Also made small naming changes elsewhere >>> ⏰ 1h
- **main.py-gen_config.py**: aligned news agent post times to sim times >>> ⏰ 1h
- **dashboard-basic.py**: added check for first name only agent label error and toot_id type as int error. also fixed display to show episode=-1 >>> ⏰ 1h (#22)
- **main.py-concordia_utils.py**: fixed issues iwht Opinion component and election components
- **examples/election**: fixed candidate/opponent mislabel in PublicOpinion classes >>> ⏰ 1h
- **main.py**: merge conflict >>> ⏰ 5m
- **main.py**: fixed bug with previous commit
- **examples/election**: added to check vote prompt to promote correct spelling of candidate names >>> ⏰ 1h
- **Concordia+Mastodon_100ipynb,-scene.py-triggering.py-apps.py**: added capability to catch duplicates, and fixed minor bugs in updated architecture >>> ⏰ 3h
- **Concordia+Mastodon_100.ipynb**: minor issue with toot writing fixed >>> ⏰ 10m
- **notebook,-configs,-and-apps.py**: Added back previously deleted independent_configs.json an
- **Concordia+Mastodon_100.ipynb**: fixed simple bugs with getting info from config
- **Concordia+Mastodon_100.ipynb**: fixed logic bug in check_vote
- improve reset_user functionality

### ♻️  Refactorings

- **engine,-gm-components,-dashboards**: refactored engine policies, gm components, removed legacy code from dashboards
- **gm,-configs,-backend**: rewrote GM logic to include wiring in base classes, moved backend to be owned by gm in config
- **full-repo**: moving off concordia based implementations, into a more minimalist and less opinionated structure
- **-**: general pre-commit fix push to previous push that introduces multi-tool calling , improvements
- **configs**: moved configuration from functions to yaml files >>> ⏰ 3h
- **config**: split apart social media config and tidied format >>> ⏰ 3
- **lots**: created structured configs and scenario-generic sim code. agent models not finished >>> ⏰ 10h
- **main_new.py;-concordia_utils_new.py;-social_media_game_master.py**: streamlined main function in main_new.py; emptied concordia_utils_new.py >>> ⏰ 2h
- **analysis_utils/**: removed fixed paths from analysis files and added code to accept input args
- **main.py-and-others**: moved functions out of main.py and added hydra >>> ⏰ 3h
- **many-files**: fixed bugs, added conf folder, improved naming >>> ⏰ 5h
- **lots**: added checkpoint saving and started (not finished) implement loading >>> ⏰ 4h
- **many-files**: made a single stream of agent processing. roles are used for role-specific computation >>> ⏰ 4h
- **main.py;gen_config.py**: streamlined and organized configration structure, logic, and names >>> ⏰ will add hydra back later
- **all-election-example-code**: pulled out sim code into src/sim; leaving election specific code in examples/election >>> ⏰ 5h
- **gen_config.py-and-related**: brought all configuration into gen_config.py to make 4 config files; news agent treated like other agents >>> ⏰ 4h
- **dashboard-withthoughts.py**: pulled out election example info from bulk of dashboard code >>> ⏰ 1h
- **query-scripts**: pulled query types into a file and took out details from agent_speech_utils.py >>> ⏰ 2h
- **various**: setup file system for news_agent headlines and images >>> ⏰ 4h
- **concordia_utils.py**: removed duplicated class defs by adding name field and get_component_name >>> ⏰ 1h
- **main.py,-gen_config.py,-concordia_utils.py**: added print of Concordia location; removed explicit name of candidate in Opinion class instantiation >>> ⏰ 10m
- **examples/election/src/**: moved config generation into its own files; fixed small bugs >>> ⏰ 3h
- **examples/election**: refactored run_basic.py into a structured package >>> ⏰ 20h
- **Concordia+Mastodon_100.ipynb-and-apps.py**: added agent_config generation in notebook. Updated apps.py to use first+last name as Mastodon usernames >>> ⏰ 1h

### ⚡️ Performance

- **scene.py-triggering.py-basic_sim.ipynb**: created a simple version of the main game master to enable parallelization

### BREAKING CHANGE

- Default simulation preset changed from custom action_mode
to tool_calling, and from mastodon to twitter_like platform.
- Default simulation preset changed from custom action_mode
to tool_calling, and from mastodon to twitter_like platform.

### chore

- checkpoint before pre-release improvements
- checkpoint before concordia migration
- checkpoint before docs coverage execution
- checkpoint before backend boundary refactor
- add SESSION_STATE.md to .gitignore
- remove egg-info from tracking, add to .gitignore
- merge feature/tool-calling into v2_refactor
- ignore transient sqlite test artifacts
- ignore oversized local hf cache artifact
- finalize uv migration and include pending refactor updates

### ci

- add GitHub Pages docs deployment workflow
- fix Python version matrix and ruff lint errors in election entity lib

### config

- refactor defaults to use tool-calling and twitter_like

### docs

- add optional extras reference table to installation guide
- add user-facing scenario and study guides
- **style_diversity**: add inter-agent distinctiveness definition to metrics section
- document default checkpointing behaviour in AGENTS.md and study_schema.md
- **configuration**: rewrite for current config structure
- add study schema reference document
- reorganize for dual LLM/human audiences with clear entry points
- expand configuration guide with multi-flow/multi-gm, engine policies, scenarios, and seed posts
- cleanup scenarios and fix action names documentation
- comprehensive multi-flow/multi-gm and preset documentation
- comprehensive architecture documentation and config defaults

### feat

- **skills**: add fast-path option to /new-scenario skill
- **style_diversity**: replace NLI CDF with strip plot in paper figure panel (c)
- **study**: wire 4 new scenarios into style_diversity study
- **scenarios**: add style-diversity suite of 4 scenarios
- **scenario_gen**: add scenario and study generation backend
- **commands**: add /new-scenario and /new-study assisted generation skills
- **style_diversity**: H4 style-diversity eval and notebook rewrite
- **run_study**: enable checkpointing by default for all new runs
- **eval**: standardize eval.py interface and add builtin.study_eval preset
- **style_diversity**: add H4 CTA phrasing analysis to notebook
- add scenario generation skills and scenario READMEs
- **config**: Hydra SearchPathPlugin for scenario search path priority
- **experiments**: add study runner scripts and style_diversity study
- pre-study refactor and eval pipeline updates
- implement GM and engine multi-flow selection with enable flags
- implement multi-flow GM with component routing (Phase 1 & 2)
- add factory support for multi-component instances
- refactor backend recommendation system for multi-algorithm support
- clean multi-flow architecture improvements
- add ConcatAct wrapper for tool-calling and update GM/resolve; add smoke test
- add fixed-entity flow routing and checkpoint replay support
- finalize environment refactor and dashboard docs alignment
- improve docs, initialization flow, and dashboard UX

### fix

- **style_diversity**: relocate h6_cta_phrasing runs into study tree
- **run_study**: rename mastodon_sim→silisocs modules; migrate study.yaml to schema v1 with h4 results
- **run_study**: correct stale sim.* key references in CLI override generation
- **runner**: filter config group overrides from _merge_external_group_overrides re-apply
- **tests**: exclude llm_e2e tests from default pytest run via addopts
- pin concordia <2.2.0 to preserve concordia.utils.html API across Python versions
- update tests for refactored config keys and add poe ci task
- pandas>=2.2 for Python 3.12, fix test engine imports, add poe ci task
- **llm**: conditionally include extra_body only for qwen3.5 models
- track episodes in RecommendationComponent, clean conf structure
- wire timeline_strategy to observation component

### refactor

- **docs**: reorganize into user docs/ and agent agent_docs/
- **style_diversity**: rename h4_cta_phrasing to h6_cta_phrasing
- inline Hydra SearchPathPlugin into runner, remove hydra_plugins package
- rename package mastodon_sim→silisocs, restructure config groups, fix CLI entry point
- **config**: rename agent_situation→agents, introduce scenario group
- introduce simulator/ package — engines/ and simulation.py from runtime/
- **config**: dissolve sim group — move run params to root, setting/event to agent_situation
- **config**: split sim into llm/simulator/agent_situation groups
- harden timeline mode, flow routing, and recsys validation

### ⏪️ Reversions

- re-add cleaned files

### ✅🤡🧪 Tests

- **pyproject.toml;-poetry.lock**: added yaml library stubs >>> ⏰ 10m

### 🎨🏗️ Style & Architecture

- **social_media_engine**: adding typing
- **configs**: set up model config. structured config with version 2 of code now completes >>> ⏰ 2h
- **main,-engines,-game_master/game_master-components**: Updating code for switch to Concordia v2 backbone
- **concordia_utils.py,-gen_config.py**: created a map from agent type to agent file, separated game_runner to online_gamemaster
- **dashboard-basic.py**: polished style and added selected name highlight >>> ⏰ 2h
- **Concordia+Mastodon_100.ipynb-scene.py-triggering.py**: changed to entity agent and redid whole agent structure introducing new candidate opinion components >>> ⏰ 10h
- **configs.json**: cahnged toots merging
- **Mastodon.ipynb**: add markdown notes

### 💚👷 CI & Build

- add default workflows

### 📌➕⬇️ ➖⬆️  Dependencies

- **poetrylock**: adding hydra to pyproject >>> ⏰ 5m
- **src/sim/main.py**: hardcoded module path >>> ⏰ 5m
- **pyproject.toml,-poetry.lock**: added omegaconf and yaml packages >>> ⏰ 5m
- **Concordia+Mastodon_100.ipynb-scene.py-triggering.py-pyproject.toml**: updated concordia and made all relevant changes >>> ⏰ 2h
- update lock file for concordia mainline
- update dependencies
- add dependencies

### 📝💡 Documentation

- **README**: adding position paper
- **README**: updated references
- **docs,-mastodon**: updating docs, correcting mastodon backend issues and adding scenarios for backends
- fixed pre-commit hook failed tests, reverted backends folder to avoid conflict with app_action decorator
- **ReadMe.md**: update readme and also removed the old scneario configs >>> ⏰ 30m
- **README.md-and-others**: updated readme and cleaned up deprecated files >>> ⏰ 1h
- update api rate limit docs (#14)
- add note
- remove warning
- update contributing
- add components readme
- add poetry install note
- add comment
- hide readme sections
- add architecture diagram to infrastructure readme
- start adding Sphinx docs
- add ci badge to readme
- **README.md**: Add WIP warning to readme

### 📱💫 Design

- **graph.py**: update graph edge coloring

### 🔊🔇 Logs

- **apps.py**: logs username of target user being replied to

### 🔐🚧📈✏️ 💩👽️🍻💬🥚🌱🚩🥅🩺 Others

- resolving merge conflicts from the analysis merge to main
- **agent_lib**: update agent files fo v2, add simple agent
- **plotting-agent_belief_testing**: added internal validity tests and code to visualize opinion change
- **README.md,-main.py,-concordia_utils.py**: load sim_setting as a module; updated readme >>> ⏰ 2h

### 🔥⚰️  Clean up

- **infrastructure,-legacy**: cleaning up legacy code
- **find_lock**: removed unnecessary code

### 🔧🔨📦️ Configuration, Scripts, Packages

- **main.py-and-others**: pulled main input arguments into hydra-processed yaml; removed abs_path env var >>> ⏰ 2h
- **updated-config-file-with-malicious-agent**: update config file with the malicous agent and in format

### 🚚🍱 Resources & Assets

- rename reset_user.py to reset_users.py

### 🚨 Linting

- clear lint/type issues and finalize uv workflow cleanup

### 🧱 Infrastructure

- increase api limits
- add readme, package and scripts for AWS CloudFormation deployment

### 🧵 Threading

- **main.py-concordia_utils.py**: added code to improve parallelism
- **basic_sim.ipynb,-scene.py-apps.py**: made minor changes to improve performance, stop threading related bugs

### 🧹 chore

- **configs,-env**: cleaned up to more intuitive config set-up
- clear notebook outputs

### 🩹 fix-simple

- **scene.py-basic_sim.ipynb**: fixed minor bugs to run basic_sim.ipynb
- add issue templates
