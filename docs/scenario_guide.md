# Creating a New Scenario

A **scenario** is a shared social world: a setting, a cast of agents, and a
backend configuration. It lives in `scenarios/<name>/` and can be reused across
many studies. Think of it as the stage: studies are the experiments you run on
it. The semantic world description for a scenario lives in
`conf/world/default.yaml`.

**Shortcut:** If you are using a repo-aware coding agent such as Codex, Claude
Code, Cursor, or another agent that can read `AGENTS.md`, ask it to follow
`agent_docs/skills/new-scenario.md` for an interactive `/new-scenario`-style
workflow. The workflow ends by calling `silisocs new-scenario
--from-spec-json '<spec>'`, which writes the validated config files — you can
also call it directly with a hand-written spec (`silisocs new-scenario --help`).

---

## What you are building

```
scenarios/<name>/
  conf/
    world/default.yaml      # Setting, event, run defaults (@package _global_)
    agents/default.yaml     # Agent classes, personas, data sources (@package agents)
    env.yaml                # Backend/GM overrides (optional)
    sim.yaml                # Simulation parameter overrides (optional)
    eval.yaml               # Probes and their deployment schedule (optional)
  README.md                 # Brief description for discoverability
```

You write the first two files. The rest are only needed when you want to change
something from the defaults (e.g. switch to a Reddit-like backend, adjust the
LLM, or ask agents survey questions).

!!! warning "Probes go in `conf/eval.yaml`, never in `world/default.yaml`"
    `world/default.yaml` carries `# @package _global_`, so every key in it lands
    at the **config root**. A `probes:` block written there lands at the root,
    where nothing reads it — the run would finish normally and emit zero probe
    events. The runtime therefore **rejects a root-level `probes:` block at build
    time** with an error telling you to move it.

    `conf/eval.yaml` is a *flat* scenario file (no `@package` header): its
    top-level keys are merged under `eval`, so a top-level `probes:` key there
    lands at `eval.probes`, which is what the probe deployer reads. See
    [Evaluation Probes](probes.md).

---

## Step 1: Define your world (`world/default.yaml`)

This file describes where the simulation takes place and what is happening.

```yaml
# @package _global_       ← required header, do not omit

scenario_name: my_world
jobname_format: "MyScenario_N${num_agents}_T${num_steps}_${run_name}"
num_agents: 3
num_steps: 10
seed: 42
run_name: my_world
output_dir: ""           # empty = Hydra's per-run output directory (recommended)

setting:
  name: "Riverside Heights Community Forum"
  background:
    - "A neighbourhood of 12,000 debating a mill-site redevelopment."
    - "Long-time residents worry about character change; newcomers want housing."

event:
  name: "Mill Site Vote"
  context: >-
    The city council is scheduled to vote on rezoning the old Hendricks Mill
    site for mixed-use development. The community forum has been active for
    three days with sharply divided opinions.
```

**Tips:**
- Keep `background` to 4 to 6 bullets. These become shared memories for all agents.
- The `context` sentence goes into every agent's initial awareness, make it
  the thing they are actually responding to.
- `num_agents` must equal the total `count` across all your agent classes
  (Step 2 below builds `1 + 2 = 3`). It is a *declared total* used for the
  output directory name and run metadata — the class counts are what actually
  build agents, and the runner warns when the two disagree. See
  [`num_agents` vs. per-class `count`](configuration.md#num_agents-vs-per-class-count).
- Every scenario world file must re-declare all of these keys, `output_dir`
  included: a scenario `world/default.yaml` **replaces** the packaged one rather
  than merging into it.

---

## Step 2: Define your agents (`agents/default.yaml`)

This file describes who is in the simulation, what they know, and how active they are.

```yaml
# @package agents          ← required header, do not omit

persona_pipeline:

  defaults:
    params:
      world_context: ${event.context}
    shared_memories:
      - ${event.context}
      - ${setting.background}

  classes:
    council_member:
      count: 1
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: council_member
      data:
        source: inline
        records:
          - name: Margaret Osei
            username: margaret_osei
            context: >-
              Margaret Osei is the district council member. She supports the
              development but wants community buy-in before the vote.
            style: "Measured, civic register. Full sentences. No slang."
            goal: "Build consensus around the development proposal."
            seed_post: ""
      field_map:
        name: name
        username: username
        context: context
        style: style
        goal: goal
        seed_post: seed_post

    resident:
      count: 2
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: resident
      data:
        source: inline
        records:
          - name: Ruth Callahan
            username: ruth_callahan
            context: "Ruth has lived in Riverside Heights for 40 years. ..."
            style: "Nostalgic, detailed, occasionally sharp."
            goal: "Preserve the neighbourhood's existing character."
            seed_post: ""
          - name: Dennis Kowalski
            username: dennis_kowalski
            context: "Dennis runs the local hardware store. ..."
            style: "Direct, practical, focused on economics."
            goal: "Understand what the development means for foot traffic."
            seed_post: ""
      field_map:
        name: name
        username: username
        context: context
        style: style
        goal: goal
        seed_post: seed_post

shared_memories:
  - ${event.context}
  - ${setting.background}

initial_observations:
  - "{name} opens their social media feed and starts scrolling."
  - "{name} sees new posts about the mill site vote."
```

**Key choices:**

| Field | What it controls |
|---|---|
| `context` | The agent's backstory, fed into their memory at init |
| `style` | One-line posting voice description, shapes how they write |
| `goal` | What they are trying to achieve in this simulation |
| `seed_post` | Their first post (leave blank `''` to let the LLM decide) |
| `inactive_to_active` | How readily an agent becomes active. Configured in `conf/sim.yaml` under `engine.participation.params.activity_transition_rates`, keyed by `sim_role_name` (or agent name) — **not** in `env.yaml` |
| `active_to_inactive` | The mirror rate, same block. Only the `activity_markov` policy uses it as a real transition rate; under `activity_probability` it is inert |
| `fully_connected_targets` | Roles that everyone follows (configured in `env.yaml`, under `gm.components.initialize.params.graph`) |

!!! note "What `inactive_to_active` means depends on the participation policy"
    Under `sim.engine.participation.built_in: activity_probability`,
    `inactive_to_active` **is** the independent per-step probability that the
    agent acts at all (there is no chain to transition), and `active_to_inactive`
    is read only as a fallback when `inactive_to_active` is absent. Under
    `activity_markov`, the two values are genuine active/inactive Markov
    **transition rates**, so the long-run active share is
    `inactive_to_active / (inactive_to_active + active_to_inactive)`, not
    `inactive_to_active` itself. Participation is opt-in: the packaged default is
    `built_in: all` (everyone acts every step), and an agent matched by no rate
    entry is a config error rather than a default.

**Data sources:** The examples above use `source: inline` (records embedded directly
in the YAML). For larger casts you can use `source: local_json` (a JSON file) or
`source: hf_dataset` (a HuggingFace dataset). See `agent_docs/scenario_design.md`
for the full reference.

---

## Step 3: Run it

```bash
uv run silisocs --config-path scenarios/my_world/conf
```

With overrides:
```bash
uv run silisocs --config-path scenarios/my_world/conf num_steps=20 seed=99
```

Or from Studio:
```bash
uv run silisocs-studio --output-root outputs --port 8765
```

---

## Step 4: Validate and add a README

Validate the scenario without spending tokens — this builds the real runtime for
zero steps, so composition errors, unknown component params, and bad slots all
surface here:
```bash
uv run silisocs-config-dry-run --config-path scenarios/my_world
```

`--config-path` accepts either the scenario root (`scenarios/my_world`) or its
config directory (`scenarios/my_world/conf`), and checks every `world/*.yaml`
variant it finds (plus each `env/*.yaml` variant, if the scenario has an `env/`
group directory). Omit it to sweep every scenario and replication in the
checkout. See [Usage](usage.md) for the full reference.

To also exercise the engine loop with a deterministic offline model:
```bash
uv run silisocs --config-path scenarios/my_world/conf num_steps=1 sim.llm.provider=scripted
```

Add a `scenarios/my_world/README.md` so the scenario is discoverable (see
existing scenarios like `scenarios/neighborhood_forum/README.md` for the format).

---

## Common patterns

### Multiple agent classes with different activity levels

Set `inactive_to_active` high (0.8 to 0.9) for active roles and low (0.3 to 0.5) for
lurkers. Activity rates go in the scenario's `conf/sim.yaml` under
`engine.participation.params.activity_transition_rates` keyed by
`sim_role_name`.

### A broadcast/authority agent everyone follows

Add the role's `sim_role_name` to `fully_connected_targets`. Useful for news bots,
moderators, or officials.

```yaml
# scenarios/my_world/conf/env.yaml
gm:
  components:
    initialize:
      params:
        graph:
          fully_connected_targets:
            - council_member
          base_followership_probability: 0.5
          network_type: barabasi_albert
          barabasi_albert_m: 2
```

```yaml
# scenarios/my_world/conf/sim.yaml
engine:
  participation:
    built_in: activity_probability
    params:
      activity_transition_rates:
        council_member:
          inactive_to_active: 0.9
          active_to_inactive: 0.1
        resident:
          inactive_to_active: 0.6
          active_to_inactive: 0.3
```

### Scripted/deterministic agents

Use `class_path: silisocs.agents.fixed.FixedAgent` with a `fixed_action` block for
agents that post from a predefined schedule (news feeds, announcements). See
`agent_docs/scenario_design.md` for the fixed-action schema.

### Switching backends

Create `scenarios/my_world/conf/env.yaml` with a single line:
```yaml
gm:
  backend:
    type: reddit_like   # or mastodon
    class_path: null
    params: {}
```

---

## Where to look next

- **Existing scenarios:** `scenarios/election/`, `scenarios/ai_conference/`: working examples
- **Full config reference:** [Configuration Reference](configuration.md)
- **Study design:** [Study Guide](study_guide.md): how to run multi-condition experiments on your scenario
- **Probes (survey questions):** [Evaluation Probes](probes.md)
- **Advanced config:** `agent_docs/scenario_design.md`: complete field reference
