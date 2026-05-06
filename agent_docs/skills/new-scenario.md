# /new-scenario — Design a new silisocs scenario

A scenario is a **shared social world**: a setting, a cast of agents, and a platform
configuration. It lives in `scenarios/<name>/` and can be used as the substrate for
many different research studies. Think of it as community-owned common ground.

You are guiding the user through designing one. There are two paths — ask which they prefer before proceeding:

> "Would you like to **walk through the design step by step** (I'll ask about setting,
> agents, network, etc. one section at a time), or give me a **high-level description**
> and have me derive the design decisions for you to review and confirm?"

Then follow the appropriate path below.

---

## Fast path — derive from description

If the user chooses the high-level description path:

1. Ask for a single paragraph describing the scenario: what social world, who's in it,
   what dynamics they want to observe, and any study or research question it's for.

2. From that description, draft a complete `ScenarioSpec` yourself. Apply these defaults
   for anything not mentioned:
   - `network_type`: `barabasi_albert`, `base_followership_probability`: 0.5
   - `timeline_mode`: `follower_chronological`
   - All actions enabled
   - `num_steps`: 8–12 depending on agent count
   - `seed`: 42
   - Activity rates: 0.7 / 0.2 for active roles, 0.4 / 0.4 for passive roles

3. Present the full drafted spec as a readable summary (not raw JSON) — setting, event,
   agent roster with roles and counts, network topology, run defaults. Flag every design
   decision you had to guess at:
   > "Assumed: barabasi_albert topology — change if you need a hub agent."
   > "Assumed: 8 steps — increase if you want more interaction rounds."

4. If the description implies a research question, add a **signal check** (2–3 sentences):
   why this scenario would produce variation relevant to the question, and one concrete risk.

5. Ask: "Does this look right, or would you like to change anything before I write the files?"
   Iterate on feedback, then proceed to **Step 6 — Write files** once confirmed.

---

## Guided path — step by step

Work conversationally — one section at a time. Never ask for more than 2–3 things at once.

### Step 0 — Framing

Ask:
> "Are you designing this scenario for an existing study or research question, or
> building a standalone social world?"

**If study-first**: ask them to state the research question in one sentence.
Store it as `research_question`. Then, throughout the rest of the steps, use it
as a lens:
- When drafting the setting and event (Step 1), check that the social dynamics
  you propose would plausibly produce variation in whatever the study measures.
  Flag any mismatches explicitly (e.g. "this setting might suppress the signal
  you need because...").
- When designing agent roles (Step 2), prioritise roles that maximise variation
  along the dimension the study cares about.
- After confirming the event (Step 1), add a brief note:
  > "Signal check: here's why this scenario should produce meaningful variation
  > for your research question — and one risk to watch for."

**If standalone**: skip the signal-check notes and proceed directly to Step 1.

### Step 1 — Phenomenon (free-form)

Ask:
> "What social phenomenon does this scenario capture? Describe it in a sentence or two —
> what's happening, who's involved, and what dynamics you want to observe."

From their answer, use your own reasoning to draft:
- A **setting**: a named place or context + 4–6 background bullet points grounding
  the world (history, demographics, tensions, platform culture)
- An **event**: a named triggering event + a 3–5 sentence narrative context paragraph
  describing what's happening now and what dynamics to observe

Show both to the user and ask for confirmation. Offer to revise any part.

If in study-first mode, follow the confirmation with a **signal check** (2–3
sentences): why this scenario would produce variation relevant to the research
question, and one concrete risk (e.g. a dynamic that might suppress the signal).

---

### Step 2 — Agent roles and behavior

Ask:
> "What do you want your agents to DO on this platform? Describe the different kinds
> of participants — what motivates them, how they behave, whether any are scripted
> or automated."

Assess each role against the two existing agent prefabs:

| Prefab | Module | Best for |
|---|---|---|
| **Entity** | `silisocs.agents.entity` | Any agent that thinks, forms opinions, posts, reacts, follows — driven by an LLM using persona + goal + memory |
| **Fixed entity** | `silisocs.agents.fixed_entity` | Scripted/deterministic agents: news bots, automated accounts, agents with a fixed posting schedule |

For each role the user describes:
- If it maps cleanly to an existing prefab: confirm which one and why.
- If it requires capabilities neither prefab has (e.g. multi-agent coordination,
  custom tools, access to external data at runtime, components that carry scenario-specific semantics): say so clearly.
  Ask: "This role would need a custom agent model. Do you want to build one?
  I can scaffold the `input/entity_lib/` stubs and explain what to implement."
  Only proceed with custom scaffolding if the user confirms. Prioritize simpler implementations, such as adding a component to the entity agent, rather than starting from scratch.

---

### Step 3 — Agent details

For each role, collect (can be done as a group if roles are similar):
- `count`: how many agents of this type
- `sim_role_name`: short snake_case label (e.g. `conference_attendee`, `protester`)
- For each individual agent (or use representative archetypes if count > 5):
  - `name`, `username`
  - `context`: 2–4 sentence personality/backstory paragraph
  - `style`: one-line description of their posting voice
  - `goal`: one sentence stating what they're trying to achieve
  - `seed_post`: their opening post (can be blank `''`)

If the user provides high-level descriptions rather than per-agent details, expand
them into concrete agents yourself and ask for confirmation.

---

### Step 4 — Network and platform

Ask:
> "What actions can agents take, and how should they be connected to each other?"

Collect or confirm:
- `enabled_actions`: choose from `[create_tweet, reply_to_tweet, like_tweet, repost_tweet, FINISHED]`
  (all enabled by default; FINISHED signals end of turn)
- `timeline_mode`: `follower_chronological` (default) or `pure_recsys`
- Network topology:
  - Any roles that should follow *everyone* (bridge/journalist roles)?
  - `base_followership_probability` (default 0.5)
  - `network_type`: `barabasi_albert` (scale-free, default) or `fully_connected`
  - Per-role `inactive_to_active` and `active_to_inactive` transition rates
    (higher = more active; suggest 0.6–0.9 for key roles, 0.1–0.3 for passive ones)

---

### Step 5 — Scenario slug and run defaults

Ask:
> "What should this scenario be called? (short, snake_case slug — e.g. `vaccine_debate`)"

Then confirm or suggest:
- `num_agents`: total agents (sum of role counts)
- `num_steps`: default episode length (suggest 8–12 for medium scenarios)
- `seed`: default random seed (suggest 42)
- `jobname_format`: suggest `"<ScenarioSlug>_N${num_agents}_T${num_steps}_${run_name}"`

---

### Step 6 — Write files

Once all sections are confirmed, assemble the full spec as a JSON object matching
the `ScenarioSpec` schema (see below) and write the files by running:

```bash
uv run silisocs new-scenario --from-spec-json '<JSON>'
```

This writes:
```
scenarios/<name>/
  conf/
    scenario/default.yaml   # @package _global_
    agents/default.yaml     # @package agents
    env.yaml
    evals.yaml
  input/entity_lib/         # only if custom agent stubs were requested
    __init__.py
    <role>.py
```

Then validate the config loads correctly:
```bash
uv run silisocs --config-path scenarios/<name>/conf num_steps=1 llm.disabled=true
```

Report the result to the user. If validation fails, diagnose and fix before finishing.

---

## ScenarioSpec JSON schema

```json
{
  "name": "snake_case_slug",
  "scenario_name": "snake_case_slug",
  "jobname_format": "Slug_N${num_agents}_T${num_steps}_${run_name}",
  "num_agents": 9,
  "num_steps": 8,
  "seed": 42,
  "run_name": "snake_case_slug",
  "setting": {
    "name": "Human-readable name",
    "background": ["bullet 1", "bullet 2", "..."]
  },
  "event": {
    "name": "Event name",
    "context": "Multi-sentence narrative paragraph..."
  },
  "data": {},
  "agent_classes": [
    {
      "role": "role_name",
      "sim_role_name": "role_name",
      "prefab_module": "silisocs.agents.entity",
      "count": 4,
      "agents": [
        {
          "name": "Full Name",
          "username": "username",
          "context": "Personality paragraph...",
          "style": "One-line posting style.",
          "goal": "One sentence goal.",
          "seed_post": ""
        }
      ],
      "activity": {
        "inactive_to_active": 0.7,
        "active_to_inactive": 0.2
      }
    }
  ],
  "network": {
    "fully_connected_targets": [],
    "base_followership_probability": 0.5,
    "network_type": "barabasi_albert",
    "barabasi_albert_m": 2
  },
  "platform": {
    "platform_type": "twitter_like",
    "timeline_mode": "follower_chronological",
    "enabled_actions": ["create_tweet", "reply_to_tweet", "like_tweet", "repost_tweet", "FINISHED"]
  },
  "custom_entity_stubs": []
}
```
