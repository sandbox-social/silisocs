# /new-scenario - Design a new Silisocs scenario

A scenario is a **shared social world**: a setting, a cast of agents, and a platform
configuration. It lives in `scenarios/<name>/` and can be used as the substrate for
many different research studies. Think of it as community-owned common ground.

You are guiding the user through designing one. There are two paths. Ask which
they prefer before proceeding:

> "Would you like to **walk through the design step by step** (I'll ask about
> setting, agents, platform, etc. one section at a time), or give me a
> **high-level description** and have me derive the design decisions for you to
> review and confirm?"

Then follow the appropriate path below.

---

## Fast path - derive from description

If the user chooses the high-level description path:

1. Ask for one paragraph describing the social world, who is in it, what dynamics
   they want to observe, and any study or research question it supports.

2. Draft a complete scenario spec yourself. Apply these defaults for anything not
   mentioned:
   - Agent: `silisocs.agents.native.NativeAgent`
   - Scripted/deterministic agent: `silisocs.agents.fixed.FixedAgent`
   - Backend: `twitter_like`
   - `timeline_mode`: `follower_chronological`
   - All standard social actions enabled
   - `network_type`: `barabasi_albert`, `base_followership_probability`: 0.5
   - `num_steps`: 8-12 depending on agent count
   - `seed`: 42
   - Activity rates: 0.7 / 0.2 for active roles, 0.4 / 0.4 for passive roles

3. Present the drafted spec as a readable summary, not raw YAML: setting, event,
   agent roster with roles and counts, platform behavior, network topology, probes,
   and run defaults. Flag every design decision you guessed:
   > "Assumed: barabasi_albert topology - change if you need a hub agent."
   > "Assumed: 8 steps - increase if you want more interaction rounds."

4. If the description implies a research question, add a **signal check**: why
   this scenario should produce variation relevant to the question, and one
   concrete risk.

5. Ask: "Does this look right, or would you like to change anything before I
   write the files?" Iterate on feedback, then proceed to **Step 6 - Write files**
   once confirmed.

---

## Guided path - step by step

Work conversationally, one section at a time. Never ask for more than 2-3 things
at once.

### Step 0 - Framing

Ask:
> "Are you designing this scenario for an existing study or research question, or
> building a standalone social world?"

If study-first, ask them to state the research question in one sentence. Store it
as `research_question`. Throughout the rest of the design, use it as a lens:

- When drafting the setting and event, check that the social dynamics would
  plausibly produce variation in whatever the study measures.
- When designing agent roles, prioritize roles that maximize variation along the
  dimension the study cares about.
- After confirming the event, add a brief signal check:
  > "Signal check: here's why this scenario should produce meaningful variation
  > for your research question - and one risk to watch for."

If standalone, skip the signal-check notes and proceed directly to Step 1.

### Step 1 - Phenomenon

Ask:
> "What social phenomenon does this scenario capture? Describe it in a sentence or
> two - what's happening, who's involved, and what dynamics you want to observe."

From their answer, draft:

- A **setting**: a named place or context plus 4-6 background bullet points
  grounding the world.
- An **event**: a named triggering event plus a 3-5 sentence narrative context
  paragraph describing what is happening now and what dynamics to observe.

Show both to the user and ask for confirmation. Offer to revise any part.

### Step 2 - Agent roles and behavior

Ask:
> "What do you want your agents to do on this platform? Describe the different
> kinds of participants - what motivates them, how they behave, and whether any
> are scripted or automated."

Map roles to the current native agent classes:

| Need | Class path | Best for |
| --- | --- | --- |
| Persona-driven participant | `silisocs.agents.native.NativeAgent` | Agents that think, form opinions, post, react, follow, and use memory |
| Deterministic account | `silisocs.agents.fixed.FixedAgent` | News bots, scheduled accounts, and fixed action plans |
| Legacy Concordia prefab | `silisocs.agents.concordia.ConcordiaAgent` with `compat: concordia` | Compatibility-only scenarios that intentionally use Concordia components |

For custom native behavior, scaffold a subclass of
`silisocs.agents.base_agent.Agent` that accepts `model`, calls
`super().__init__(model)`, builds context in `act()`, and calls
`self._call_model(context, action_spec)`.

### Step 3 - Agent details

For each role, collect:

- `count`: how many agents of this type
- `sim_role_name`: short snake_case label, such as `conference_attendee`
- For each individual agent, or representative archetypes if count > 5:
  - `name`, `username`
  - `context`: 2-4 sentence personality/backstory paragraph
  - `style`: one-line description of their posting voice
  - `goal`: one sentence stating what they are trying to achieve
  - optional seed post text

If the user provides high-level descriptions rather than per-agent details,
expand them into concrete agents yourself and ask for confirmation.

### Step 4 - Platform and connections

Ask:
> "What actions can agents take, and how should they encounter each other?"

Collect or confirm:

- `enabled_actions`: choose from backend action names such as `create_tweet`,
  `reply_to_tweet`, `like_tweet`, `repost_tweet`, `follow_user`, and `FINISHED`.
- `timeline_mode`: `follower_chronological` by default, or a recommendation mode
  when the study needs it.
- Network topology:
  - Any roles that should be followed by everyone?
  - `base_followership_probability` (default 0.5)
  - `network_type`: `barabasi_albert` by default, or another supported topology
  - Per-role `inactive_to_active` and `active_to_inactive` transition rates

### Step 5 - Scenario slug and run defaults

Ask:
> "What should this scenario be called? (short, snake_case slug - e.g.
> `vaccine_debate`)"

Then confirm or suggest:

- `num_agents`: total agents
- `num_steps`: default episode length (suggest 8-12 for medium scenarios)
- `seed`: default random seed (suggest 42)
- `jobname_format`: suggest `"<ScenarioSlug>_N${num_agents}_T${num_steps}_${run_name}"`

### Step 6 - Write files

Once all sections are confirmed, assemble a spec and write the files with the
scenario generator:

```bash
uv run silisocs new-scenario --from-spec-json '<JSON>'
```

This writes:

```text
scenarios/<name>/
  conf/
    scenario/default.yaml   # @package _global_
    agents/default.yaml     # @package agents
    env.yaml
    evals.yaml
    sim.yaml
```

Then validate with a scripted dry run:

```bash
uv run silisocs --config-path scenarios/<name>/conf \
  num_steps=1 sim.llm.provider=scripted sim.llm.name=scripted
```

Report the result. If validation fails, diagnose and fix before finishing.

---

## Current config reminders

- Runtime objects use direct `class_path` + `params`, not prefabs.
- Native configs do not import Concordia. Use `compat: concordia` only for
  explicit legacy Concordia agents.
- Engine config uses `sim.engine.loop`, `sim.engine.step`, and
  `sim.engine.turn_policy`.
- Probe config uses `probe_type`, `probe_data`, and `probe_return`.
- Checkpoint restore uses `sim.checkpoint.source_run` plus
  `sim.checkpoint.restore`, not simulation initialization.
