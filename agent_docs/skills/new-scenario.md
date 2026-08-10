# Scenario Design Workflow

A scenario is a **shared social world**: a setting, a cast of agents, and a
backend configuration. It lives in `scenarios/<name>/` and can be used as the
substrate for many different research studies. Its semantic world description
lives in `conf/world/default.yaml`. Think of the scenario as community-owned
common ground.

Guide the user through designing one. Work conversationally — one section
at a time. Never ask for more than 2–3 things at once. After collecting and confirming
all sections, write the files by calling the CLI.

---

## Step 0 — Framing

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

---

## Step 1 — Phenomenon (free-form)

Ask:
> "What social phenomenon does this scenario capture? Describe it in a sentence or two —
> what's happening, who's involved, and what dynamics you want to observe."

From their answer, use your own reasoning to draft:
- A **setting**: a named place or context + 4–6 background bullet points grounding
  the scenario's world (history, demographics, tensions, platform culture)
- An **event**: a named triggering event + a 3–5 sentence narrative context paragraph
  describing what's happening now and what dynamics to observe

Show both to the user and ask for confirmation. Offer to revise any part.

If in study-first mode, follow the confirmation with a **signal check** (2–3
sentences): why this scenario would produce variation relevant to the research
question, and one concrete risk (e.g. a dynamic that might suppress the signal).

---

## Step 2 — Agent roles and behavior

Ask:
> "What do you want your agents to DO on this platform? Describe the different kinds
> of participants — what motivates them, how they behave, whether any are scripted
> or automated."

Assess each role against the supported agent classes:

| Agent class | Module | Best for |
|---|---|---|
| **NativeAgent** | `silisocs.agents.native.NativeAgent` | Any native agent that thinks, forms opinions, posts, reacts, follows — driven by an LLM using persona + goal + memory |
| **FixedAgent** | `silisocs.agents.fixed.FixedAgent` | Scripted/deterministic agents: news bots, automated accounts, agents with a fixed posting schedule |
| **ConcordiaAgent** | `silisocs.agents.concordia.ConcordiaAgent` with `compat: concordia` | Explicit compatibility for older Concordia-style scenario agents |

For each role the user describes:
- If it maps cleanly to an existing class: confirm which one and why.
- If it requires capabilities none of these classes has (e.g. multi-agent coordination,
  custom tools, access to external data at runtime, components that carry scenario-specific semantics): say so clearly.
  Ask: "This role would need a custom agent model. Do you want to build one?
  I can write the `Agent` subclass and explain what to implement."
  Only proceed with custom agent work if the user confirms. Prioritize simple native
  `Agent` subclasses before introducing compatibility code. Note that
  `silisocs new-scenario` writes **config only** — it generates no agent module
  stubs (the spec's `custom_agent_stubs` field is inert), so you write those
  files yourself and point the class's `class_path` at them.

---

## Step 3 — Agent details

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

## Step 4 — Network and backend

Ask:
> "What actions can agents take, and how should they be connected to each other?"

Collect or confirm:
- `env.gm.backend.enabled_actions`: leave as `null` for the full backend action
  surface, or list the exact actions this scenario should allow. For a small
  Twitter-like social run, use `[create_tweet, reply_to_tweet, like_tweet,
  repost_tweet, FINISHED]`. `FINISHED` signals the end of an open-ended turn.
  (`enabled_actions: []` is an EMPTY allow-list, not "no filter" — it fails at
  build.)
- `env.gm.backend.excluded_actions`: optional deny-list for removing actions
  while keeping the rest of the backend surface available. **Not a
  `ScenarioSpec` field** — the scaffolder cannot emit it, so add it to the
  generated `conf/env.yaml` by hand afterwards if you need it.
- `env.gm.components.observe.params.timeline_mode`: `follower_chronological`
  (default) or `pure_recsys`
- Network topology:
  - Any roles that should follow *everyone* (bridge/journalist roles)?
  - `base_followership_probability` (the packaged `env/twitter_like.yaml`
    default is 0.3; the `ScenarioSpec` field defaults to 0.5, and whatever you
    put in the spec is what gets written)
  - `network_type`: `barabasi_albert` (scale-free, default) or `fully_connected`
  - Per-role activity rates under
    `sim.engine.participation.params.activity_transition_rates` (with
    `sim.engine.participation.built_in: activity_probability`;
    `inactive_to_active` / `active_to_inactive`; higher = more active; suggest
    0.6–0.9 for key roles, 0.1–0.3 for passive ones). The GM `next_acting`
    slot no longer accepts activity models — it is env-derived selection only.

---

## Step 5 — Measurement (probes)

Ask:
> "What do you want to *measure* in this world? A probe asks every agent the same
> question on a schedule and records the typed answer — that's how a run becomes
> data rather than a transcript. What would you want to ask them, and when?"

A scenario with no probes still runs; it just produces no survey panel, so
anything you want to chart later has to be derived from the action log alone.
Push back gently if the user has a research question (Step 0) but no probe that
would move when that question's answer moves.

For each measurement, collect:

- `id`: snake_case key (e.g. `vote_pref`, `trust_in_council`). Also used as the
  probe's name if you do not give one.
- `probe_type`: one of the built-ins —

  | `probe_type` | Answer shape | Use when |
  |---|---|---|
  | `NumericRatingProbe` | integer between `lo` and `hi` | attitude strength, favorability, trust |
  | `BinaryProbe` | yes / no | intent, membership, agreement |
  | `ChoiceProbe` | one of N options | candidate/side/topic preference |
  | `FreeTextProbe` | open string | reasoning, framing, open-ended concerns |

- `question`: the exact prompt. Keep it to the measurement question plus any
  answer-format constraint — persona and recent context come from the agent
  runtime, so do not restate them. For `NumericRatingProbe`, write the bounds as
  the `{lo}` / `{hi}` template slots (e.g. `"Return one number from {lo} to {hi}:
  how much do you trust the council?"`) and set `lo` / `hi`.
- `deployment` (optional): per-probe schedule/targeting overrides. Only the
  fields you set are emitted; everything else inherits the scenario's global
  `eval.probes.deployment` block (`enabled: true`, `start_step: 1`,
  `every_n_steps: 1`, no agent filters), which the scaffolder always writes.

Schedule rules to apply while advising:

- Steps are **0-indexed**, and the default `start_step: 1` therefore means
  **step 0 is never probed**. If the user wants a pre-simulation baseline, set
  `start_step: 0` on that probe.
- For a *terminal* measurement ("what do they think at the end?"), do not try to
  compute the last step index — anchor it instead. The `ScenarioSpec`
  `deployment` block does **not** support the `at` field, so scaffold the probe
  without a deployment and then add `deployment: {at: run_end}` to its entry in
  the generated `conf/eval.yaml` by hand.
- Probes cost an LLM call per agent per due step (batched into one call per agent
  across probes due together). On a long run with many agents, `every_n_steps: 1`
  on five probes is a real bill — suggest a coarser cadence for anything that is
  not the primary outcome.

`ChoiceProbe` needs a `choices` list, which is also **not** a `ScenarioSpec`
field. Scaffold it with `"probe_type": "ChoiceProbe"` anyway — it builds fine
with an empty choice list — then add `choices:` under its `probe_data` in the
generated `conf/eval.yaml`, and re-run the dry run.

Confirm the probe list with the user before moving on.

---

## Step 6 — Scenario slug and run defaults

Ask:
> "What should this scenario be called? (short, snake_case slug — e.g. `vaccine_debate`)"

Then confirm or suggest:
- `num_agents`: total agents (sum of role counts)
- `num_steps`: default episode length (suggest 8–12 for medium worlds)
- `seed`: default random seed (suggest 42)
- `jobname_format`: suggest `"<ScenarioSlug>_N${num_agents}_T${num_steps}_${run_name}"`

---

## Step 7 — Write files

Once all sections are confirmed, assemble the full spec as a JSON object matching
the `ScenarioSpec` schema (see below) and write the files by running:

```bash
uv run silisocs new-scenario --from-spec-json '<JSON>'
```

`--output-dir <dir>` (default `scenarios`) chooses the root the scenario
directory is created under; the scenario always lands in
`<output-dir>/<spec.name>/`. Use it to scaffold outside the repo, or into a
throwaway directory while iterating:

```bash
uv run silisocs new-scenario --from-spec-json '<JSON>' --output-dir /tmp/scratch
```

The command refuses to overwrite: if `<output-dir>/<name>/` already exists it
exits with an error, so remove it or pick a new name before re-running.

This writes exactly five files:
```
<output-dir>/<name>/
  conf/
    world/default.yaml      # @package _global_ — run params, setting, event, data
    agents/default.yaml     # @package agents  — persona pipeline
    env.yaml                # flat, merged under `env`  — backend + GM components
    eval.yaml               # flat, merged under `eval` — probe deployment + probes
    sim.yaml                # flat, merged under `sim`  — action mode, init, engine
```

Nothing else is generated — there is no `input/agent_lib/` scaffolding, and
`custom_agent_stubs` is accepted by the spec but writes no files. If the user
agreed to a custom agent in Step 2, write those modules yourself and point the
class's `class_path` at them.

Notable defaults the generated files carry (do not "fix" these — they are
deliberate, and the alternatives fail at build):

- `sim.yaml` seed posts use provider `type: agent` (each agent writes its own
  opening post). Valid types are `agent | csv | json | fallback | none`.
- `env.yaml`'s observe component is `timeline_every_turn` with
  `episode_observation_flows: [fixed_pre]` — a **plural list**. The singular
  `episode_observation_flow` belongs to the `episode_only` built-in and fails at
  build here, because component params are strict.
- `sim.yaml` sets `action_mode: generic` with `tool_calling.mode: multi`, and
  `env.yaml` pairs it with the `tool_calling` resolve component.

Then validate the generated config — this builds the real runtime for zero steps
and spends no tokens:
```bash
uv run silisocs-config-dry-run --config-path scenarios/<name>
```

Re-run it after every hand-edit (adding `choices:`, `at: run_end`,
`excluded_actions`, …).

Note that `new-scenario` does **not** stop at writing files: it finishes by
running the scenario for one step with `sim.llm.provider=scripted` and prints
`[PASS]` / `[FAIL]` with the captured output, exiting non-zero on failure. That
is a real run, so it creates an `outputs/<scenario>/...` directory. To repeat it
yourself:
```bash
uv run silisocs --config-path scenarios/<name>/conf num_steps=1 sim.llm.provider=scripted
```

A one-step run only executes step **0**, and the scaffolded probe deployment
starts at `start_step: 1`, so that check writes no `probe_events.jsonl` — that
is expected, not a broken probe config. Use `num_steps=3` if you want to see
probe rows.

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
      "class_path": "silisocs.agents.native.NativeAgent",
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
  "backend": {
    "type": "twitter_like",
    "timeline_mode": "follower_chronological",
    "enabled_actions": ["create_tweet", "reply_to_tweet", "like_tweet", "repost_tweet", "FINISHED"]
  },
  "custom_agent_stubs": [],
  "probes": [
    {
      "id": "favorability",
      "probe_type": "NumericRatingProbe",
      "name": "Favorability",
      "question": "Return one number from {lo} to {hi}: how favorable is your view of the proposal?",
      "context": "",
      "lo": 1,
      "hi": 10
    },
    {
      "id": "will_vote",
      "probe_type": "BinaryProbe",
      "question": "Will you cast a vote? Reply yes or no.",
      "deployment": {
        "start_step": 2,
        "every_n_steps": 2
      }
    }
  ]
}
```

Unknown keys are **silently ignored** by the spec parser, so a misspelled or
unsupported field (e.g. `backend.excluded_actions`, or `at` inside a probe's
`deployment`) does not error — it simply never reaches the generated YAML.
Check the written files against what you intended.

### `probes` (optional)

Omit it, or pass `[]`, and the generated `conf/eval.yaml` still gets its
`probes.deployment` block with an empty `probes: {}` map. Each entry becomes one
`eval.probes.probes.<id>` entry:

| Field | Required | Default | Notes |
|---|---|---|---|
| `id` | yes | — | The YAML key **and** the emitted `probe_name` |
| `probe_type` | no | `"FreeTextProbe"` | `NumericRatingProbe` / `BinaryProbe` / `ChoiceProbe` / `FreeTextProbe`, or a custom class name |
| `name` | no | `""` → falls back to `id` | Emitted as `probe_data.name` |
| `question` | yes | — | Emitted as `probe_data.question` |
| `context` | no | `""` | Emitted as `probe_data.context` only when non-empty |
| `lo` | no | unset | Emitted only when set; `NumericRatingProbe` bound and `{lo}` template slot |
| `hi` | no | unset | Emitted only when set; `NumericRatingProbe` bound and `{hi}` template slot |
| `deployment` | no | unset | Per-probe schedule/targeting override; only the fields you set are emitted |

`deployment` accepts exactly these keys, all optional — an unset key inherits the
global `eval.probes.deployment` value:

`enabled` (bool), `start_step` (int), `every_n_steps` (int), `include_agents`
(list of names), `exclude_agents` (list of names), `include_flows` (list of flow
tags), `exclude_flows` (list of flow tags).

It does **not** accept `at`, `sample_k`, `sample_fraction`, `include_classes`, or
`exclude_classes`, even though the runtime's deployment block does. Add those to
the generated `conf/eval.yaml` by hand.

Rendered shape for one entry:

```yaml
# conf/eval.yaml  (flat file — its top-level keys are merged under `eval`)
probes:
  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
    include_agents: []
    exclude_agents: []
  probes:
    <id>:
      probe_name: <id>
      probe_type: <probe_type>
      probe_data:
        name: <name or id>
        question: <question>
        # context / lo / hi appear only when set
      # deployment: appears only when you set at least one field
```
