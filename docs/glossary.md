# Glossary

The handful of terms the docs and config use, defined once. When two words
appear in the wild for the same thing, the **bold** one is the preferred term.

**Scenario**
: A directory of YAML config (`scenarios/<name>/conf/`) describing one
  simulated world: its setting, agents, environment, engine, and evaluation.
  The launchable unit. (Studio sometimes says "world" in older copy — same
  thing.)

**World (config group)**
: The `world` Hydra config group inside a scenario. Despite the name it holds
  the *run parameters* (`num_agents`, `num_steps`, `seed`, ...) plus the
  setting/event text. You will edit it often; you rarely need the word "world"
  outside this group.

**Run**
: One execution of a scenario. Everything it produces lives in one output
  directory under `outputs/`, indexed by its `run_manifest.json`. The
  `output_dir` run parameter names that directory (it was called
  `output_rootname` before 0.4.0); `run_simulation(cfg, output_dir=...)` is the
  same thing programmatically. Studio's job queue manages runs; "job" in API
  paths is the queue's internal word for the same thing.

**Step**
: One tick of the simulation clock (`num_steps` of them per run). Each step,
  every participating agent gets a turn. Artifact rows record the step index
  in a field named `episode` — step and episode are the same number.

**Agent**
: One simulated actor: it receives observations and produces actions, usually
  via an LLM. Built from the persona pipeline, or from your own `Agent`
  subclass via `class_path`.

**Persona**
: The character sheet an agent is built from — name, background, memories.
  Personas describe; agents act.

**Backend**
: The platform the agents act on — the Twitter-like app, the Reddit-like app,
  a real Mastodon server, a public-goods game. Selected by the `env` config
  group; implemented as an `*App` class. "Platform" in Studio's viewer tab
  refers to the same thing.

**Game master (GM)**
: The referee between agents and a backend. Each turn it shows the agent its
  view of the world (observe), asks for an action (action prompt), and turns
  the response into backend calls (resolve). Built from pluggable components;
  multi-GM setups route different agent flows through different GMs.

**Probe**
: An in-simulation survey question put to agents on a schedule (e.g. "Do you
  believe the claim?"). Results land in `probe_events.jsonl` and feed the
  analysis panels. Older docs sometimes say "survey" or "questionnaire".

**Evaluation** (`eval` vs `evaluations`)
: Three similarly-named things. The **`eval` config group** (`eval.probes.*`)
  configures *in-run* measurement — the probes an engine fires during a
  simulation. The **`evaluations:` study key** configures *post-run* analysis —
  evaluators a study runs over finished run directories. The
  **`silisocs.evaluations` package** is the code implementing both, plus the
  `load_run`/`load_study` artifact loaders. A scenario config never has an
  `evaluations:` key; a `study.yaml` never has an `eval:` group.

**Participation**
: The sim-level filter deciding which agents are in a step's roster at all
  (`sim.engine.participation`), applied before any GM's `next_acting`. The
  default `all` puts everyone in every step. The activity models
  (`activity_probability`, `activity_markov`) read `activity_transition_rates`,
  keyed by agent name or sim role, whose two rates are always **named**
  (`inactive_to_active`, `active_to_inactive`) rather than positional — and mean
  different things per policy (see
  [Configuration](configuration.md#social-setup-and-participation)).

**Flow**
: A named group of agents that the engine schedules together (`flow_tag` on a
  persona class). Flows let different populations act in a fixed order, take
  different actions, or route through different GMs.

**Sim role**
: A per-agent label (e.g. `user`, `moderator`) that rate tables and probe
  filters key on. Coarser than an agent name, finer than a flow.

**Slot**
: The `{built_in: <preset> | class_path: <your.Class>, params: {...}}` shape
  every pluggable config knob uses. `built_in` picks a shipped implementation;
  `class_path` loads yours; `params` are passed to its constructor and
  validated strictly. Full rules and the list of slots:
  [Slots](configuration.md#slots).

**Study**
: A grid of runs (hypotheses × conditions × seeds) declared in a
  `study.yaml`, executed by `silisocs-study`, with evaluators that aggregate
  metrics across runs.

**EASE**
: The four-part decomposition the config mirrors: Environment, Agents,
  Simulation engine, Evaluation — the `env`, `agents`, `sim`, and `eval`
  config groups.

**Concordia**
: DeepMind's agent-simulation framework, an inspiration for this project.
  Agents written for it can be reused via the optional `concordia` extra.
  You do not need it.

**Hydra**
: The configuration engine underneath: it composes the config groups above
  and accepts `key=value` overrides on the command line
  (`num_agents=25 sim.llm.name=gpt-4o`).
