# SiliSocS v1/v2 Ambitious Product Roadmap

Last reviewed: 2026-07-08

This plan imagines SiliSocS as a polished product for designing, running,
inspecting, and extending rich agent simulations. It builds on
`v0 improvements.md` and `v0 polish roadmap.md`, but sets a larger target:
SiliSocS should become the easiest way to study how autonomous agents behave in
social, economic, organizational, and internet-like environments.

## North Star

SiliSocS should feel like a complete simulation workbench:

- A researcher can install it, choose a scenario template, run a study, inspect
  outcomes, and publish reproducible artifacts.
- A builder can add a new Agent, Backend, Game Master, Evaluation, or tool-like
  capability through stable interfaces and scaffolds.
- A product-minded user can run local simulations through dashboards instead of
  source spelunking.
- A safety researcher can model modern autonomous agent harnesses: browser use,
  tool calls, memory, skills, scheduled tasks, channels, permissions, failure
  modes, and social interaction between agents.

The product should keep its current strength: a small native runtime where
Agents produce typed actions inside Environments and Evaluations measure what
happened. The v1/v2 ambition is to deepen the modules around that core without
turning extension authors into runtime archaeologists.

## External Inspiration Checked

This pass looked at current agent harness documentation for Hermes Agent and
OpenClaw as examples of autonomous systems that wrap an LLM in tools, memory,
channels, skills, scheduling, and execution policy.

- Hermes describes a platform-agnostic core where one agent module serves CLI,
  gateway, batch, and API entry points; optional subsystems use registries and
  gating rather than hard dependencies.
  Source: https://hermes-agent.nousresearch.com/docs/developer-guide/architecture
- Hermes exposes tools/toolsets, skills, persistent memory, delegation, terminal
  execution, file editing, and web search as harness capabilities.
  Source: https://hermes-agent.nousresearch.com/docs/user-guide/features/overview
- Hermes skills are on-demand documents with progressive disclosure.
  Source: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- Hermes memory is bounded, curated, and persistent across sessions.
  Source: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
- OpenClaw frames tools as callable actions, skills as operating instructions,
  and plugins as runtime capabilities such as tools, providers, channels, hooks,
  and packaged skills.
  Source: https://docs.openclaw.ai/tools
- OpenClaw skills include loading rules, allowlists, environment injection, and
  prompt injection into the agent run.
  Source: https://docs.openclaw.ai/tools/skills
- OpenClaw documents context as the system prompt, conversation, tool results,
  injected files, attachments, and token accounting.
  Source: https://docs.openclaw.ai/concepts/context
- OpenClaw sandboxing moves tool execution into isolated backends to reduce
  blast radius, while noting that sandboxing is imperfect.
  Source: https://docs.openclaw.ai/gateway/sandboxing

The lesson for SiliSocS is not "copy an agent harness." The lesson is that
modern autonomous agents are not just prompts. They are systems with memory,
tools, channels, schedules, permissions, state, identities, and logs. SiliSocS
can simulate those systems if those mechanisms become explicit Environment and
Agent-facing modules.

## Strategic Product Shape

### v1: Polished Simulation Workbench

v1 should make the current product feel coherent, reliable, and extensible.
This is the "serious users can run studies without reading source" milestone.

Core outcomes:

- One install path and one dashboard entry point.
- One run manifest that every dashboard, evaluator, notebook, and agent can
  read.
- Guided scenario/study creation.
- Study-level dashboards with reproducibility and cost views.
- Extension scaffolding and contract tests.
- Tool-neutral contributor and coding-agent documentation.
- Stronger validation before expensive runs start.
- A stable public interface for Agent, Backend, Game Master, Engine policy,
  MemoryPolicy, and Evaluation extensions.

### v2: Autonomous Agent Simulation Lab

v2 should make SiliSocS a first-class lab for studying autonomous agents that
act through tools and internet-like environments.

Core outcomes:

- Harness-shaped Agents with skills, tools, channels, memory, schedules, and
  permission policy.
- Browser and web-task simulation environments.
- Local internet fixtures: websites, inboxes, calendars, docs, file systems,
  social networks, repos, and payment-like ledgers.
- Multi-agent organizations where agents delegate tasks, message each other,
  and compete for attention/resources.
- Safety and governance experiments around tool permissions, prompt injection,
  credential exposure, overspending, data leakage, malicious skills, and
  runaway automation.
- Real and simulated tool adapters behind one interface, with dry-run and
  sandbox modes.
- Deep visualization: event timelines, tool-call traces, browser sessions,
  memory evolution, skill usage, permission prompts, and incident reports.

## Milestone Chart

| Milestone | Product Outcome | New Simulation Power | Main Risk |
|---|---|---|---|
| v1.0 | Install, run, inspect, and analyze from one coherent workbench | Reliable current simulations with self-describing artifacts | Polishing dashboards before run artifacts are stable |
| v1.1 | Guided scenario/study creation and template gallery | Users can design studies without hand-writing every file | Templates drift from tested examples |
| v1.2 | Extension scaffolds, contract tests, and generated config docs | Third parties can add Agents, Backends, Evaluations, and policies | Too many shallow extension seams |
| v2.0 | Harness Agent runtime with tools, skills, memory, channels, and telemetry | Simulate persistent autonomous agents offline | Harness loops become hard to replay |
| v2.1 | Local internet environment and deterministic browser/tool adapters | Study browser/email/calendar/docs/repo tasks safely | Simulated worlds feel too toy-like |
| v2.2 | Event-driven Engine with scheduled tasks and reactive triggers | Model long-running asynchronous agent societies | Checkpoint and probe semantics get blurry |
| v2.3 | Safety/governance lab with permissions, sandboxing, and incidents | Measure prompt injection, credential leakage, overspend, and unsafe tools | Live adapters introduce risk before controls mature |

## Is The Agent-Harness Roadmap Straightforward?

It is straightforward in concept but not small in execution.

The current runtime already has the right foundation:

- Agents observe text and produce typed `ActionOutput`.
- `ActionSpec` already supports tool-call and structured outputs.
- Backends expose executable actions through an environment-facing interface.
- Memory is already a pluggable Agent-owned policy.
- The Engine owns scheduling, turn execution, checkpointing, and probes.
- ADR 0004 already sketches a future deterministic event-driven mode, which is
  a strong fit for scheduled tasks and reactive internet-like agents.

The hard parts are product and modeling depth:

- A browser-using autonomous agent does not just act once per round. It loops:
  inspect page, click, read result, update plan, maybe retry, maybe ask another
  agent, maybe persist memory, maybe schedule follow-up.
- A tool call can have real side effects. Simulation needs permission policy,
  dry-run adapters, audit logs, and deterministic replays.
- Internet environments are open-ended. SiliSocS needs curated simulated worlds
  first, then carefully gated live adapters.
- "Memory" is not one thing. There is working context, durable memory, retrieval
  memory, user profile, skill knowledge, external documents, browser state, and
  run history.
- Agent harnesses are security-sensitive. Modeling them well means modeling
  prompts, secrets, tool visibility, sandbox scope, and failure/attack paths.

So the practical answer is:

- v1 polish is very achievable using the current architecture.
- A v2 local autonomous-agent lab is achievable if it starts with simulated
  tools and fixtures, not the live internet.
- Live internet agent replication is possible later, but should be opt-in,
  sandboxed, non-default, and treated as a high-risk adapter family.

## Agent Harness Mechanisms To Model

A modern autonomous agent harness usually has these moving parts:

1. Model core

The LLM receives curated context, decides the next action, and emits text,
structured output, or tool calls. In SiliSocS this maps cleanly to `Agent.act`,
`ActionSpec`, `ActionOutput`, and `LanguageModel`.

2. Context assembler

The harness decides what enters the model window: system prompt, developer
rules, user request, memory snippets, skills, tool schemas, file snippets,
browser observations, prior messages, time, budget, and active constraints.
This is currently partly inside `NativeAgent._context`; v2 needs a deeper
Context Module with inspectable provenance and token/cost accounting.

3. Tool registry

Tools are callable capabilities: browser navigation, search, shell, file edit,
email, calendar, social post, database lookup, code execution, payment-like
transfer, issue creation, and so on. In SiliSocS, Backend actions are close, but
agent-harness tools need an explicit Tool Module because a harness agent may
act on multiple tool domains inside one Environment.

4. Tool executor

The executor validates arguments, applies permission policy, executes the tool
against a real or simulated adapter, and records the result. This should be a
separate module from tool discovery so the interface stays small and audit
logic has locality.

5. Memory system

Autonomous agents use multiple memory tiers:

- working memory for the current task;
- recent transcript;
- durable profile/project memory;
- vector or hybrid retrieval memory;
- self-authored notes;
- generated skills;
- environment state such as browser tabs or files.

SiliSocS already has MemoryPolicy as a promising seam. v2 should expand it into
named memory stores with provenance, promotion rules, forgetting rules, and
inspection dashboards.

6. Skills and procedures

Skills are reusable instructions for how to use tools or complete workflows.
They should be first-class simulation artifacts: discoverable, versioned,
scoped per Agent, optionally mutable, and logged when injected into context.
This matters because skill loading changes behavior as much as persona does.

7. Channels

Harness agents often run through chat apps, terminals, IDEs, web dashboards,
webhooks, cron jobs, or API calls. In simulation, channels are Environment
surfaces that produce tasks/events and receive agent replies. They should be
modeled separately from tools.

8. Scheduler and triggers

Autonomous agents run from scheduled tasks, reminders, background jobs, webhooks,
incoming messages, errors, or long-running task loops. v2 needs deterministic
event scheduling so "wake at 9am", "react to email", and "continue after tool
result" can all be replayed.

9. Permission and sandbox policy

Tools need visibility, approvals, budgets, filesystem scope, network scope,
credential scope, and maybe human-in-the-loop gates. This is both a product
feature and an Evaluation surface.

10. Reflection and self-improvement

Some harnesses summarize failures, write memories, propose skills, edit their
own instructions, or spawn sub-agents. SiliSocS can model this, but should make
self-modification explicit and logged so studies can compare agents with and
without learning loops.

11. Telemetry and replay

Every tool call, context injection, memory read/write, skill use, permission
decision, and side effect should be logged as typed events. This is essential
for reproducibility and dashboards.

## v1 Roadmap: Fully Polished Simulation Product

### V1.1: Product Entry Points

Goal: one coherent user journey.

Features:

- `silisocs doctor`
  - validates Python version, optional extras, model credentials, run output
    writability, docs examples, and dashboard extras.
- `silisocs tutorial`
  - runs a deterministic scripted-model demo and opens the result explorer.
- `silisocs dashboard`
  - launches the main workbench.
- `silisocs analyze <run-dir>`
  - opens analysis for a run or study.
- `silisocs new-scenario`
  - interactive CLI and dashboard creation.
- `silisocs new-study`
  - guided study generation from an existing scenario.
- `silisocs scaffold <extension-kind>`
  - creates a minimal extension plus tests.

Done when:

- A new user can go from install to viewable run without reading source.

### V1.2: Run Manifest And Artifact Contract

Goal: every tool can load a run from one place.

Features:

- `run_manifest.json`
  - scenario, study, command, git SHA, package version, config paths, seed,
    model config, backend type, Game Master layout, Engine policies, checkpoint
    policy, output schema version, artifact paths, and run health.
- Artifact schema versioning.
- Loader module:
  - `load_run(path) -> RunArtifact`
  - `load_study(path) -> StudyArtifact`
- Legacy discovery fallback for old runs.
- Contract tests for run artifacts.

Architecture:

- Create a deep Run Artifact Module. Dashboards, evaluations, notebooks, and
  CLI tools should not rediscover logs independently.

Done when:

- Result loading logic has one interface and one set of tests.

### V1.3: Unified Dashboard

Goal: make simulation inspection visual and complete.

Views:

- Run history landing page.
- Scenario editor with validation.
- Study editor with conditions and seeds.
- Live run monitor with step progress and cost.
- Run explorer:
  - action timeline;
  - agent heatmap;
  - prompt/response browser;
  - probe results;
  - failures and retries;
  - backend state snapshots;
  - cost/usage by phase;
  - checkpoint/resume status.
- Study explorer:
  - condition comparison;
  - seed variability;
  - effect sizes;
  - evaluator outputs;
  - reproducibility report.
- Export:
  - Markdown report;
  - CSV/Parquet;
  - notebook starter;
  - shareable static HTML report.

Done when:

- A researcher can explain what happened in a run from the dashboard alone.

### V1.4: Scenario And Study Authoring

Goal: make YAML powerful but not mandatory.

Features:

- Template gallery:
  - social debate;
  - misinformation spread;
  - resource market;
  - virtual collaboration space;
  - multi-GM orchestration;
  - probe-heavy study;
  - agent-harness task world.
- Form-based scenario editor with schema-aware fields.
- Config diff view between scenario variants.
- Scenario lint:
  - missing names;
  - duplicated Agent Names;
  - unknown params;
  - risky model settings;
  - missing evaluators;
  - unbounded run costs.
- Scenario preview:
  - agent roster;
  - Game Master layout;
  - backend actions;
  - expected artifacts.
- Study builder:
  - hypotheses;
  - conditions;
  - seeds;
  - evaluators;
  - analysis plan.

Done when:

- Most users can author a scenario without hand-editing every config file.

### V1.5: Extension Developer Experience

Goal: make extensibility real, discoverable, and tested.

Extension kinds:

- Agent.
- Agent Builder.
- Backend.
- Game Master Component.
- Engine Loop Policy.
- Engine Step Policy.
- Turn Policy.
- MemoryPolicy.
- Probe.
- Evaluator.
- Dashboard panel.
- Result loader adapter.

Features:

- Scaffolds with minimal implementation, config snippet, and focused tests.
- Public contract tests for each extension kind.
- Registry browser in docs and dashboard.
- Generated config reference from actual defaults/registries.
- Compatibility matrix for optional extras.
- `examples/extensions/` kept runnable in CI.

Architecture:

- Use the deletion test on factories and registries. A registry earns its keep
  only if it hides construction and validation complexity from callers.
- Keep extension interfaces small. Put complex behavior behind adapters.

Done when:

- A third-party extension can be built without copying internal code.

### V1.6: Validation, Cost, And Reliability

Goal: fail early and explain failure clearly.

Features:

- Full config dry-run for all params, component slots, class paths, output dirs,
  model capabilities, and optional extras.
- Estimated cost before run:
  - agents x steps x probes x max tokens;
  - model-specific pricing table;
  - budget limits.
- Runtime health model:
  - parse failures;
  - skipped actions;
  - model retries;
  - rate limits;
  - backend errors;
  - checkpoint failures;
  - degraded probes.
- Resume assistant:
  - detect compatible checkpoint;
  - summarize restore strategy;
  - show missing state.

Done when:

- Expensive runs rarely fail for discoverable setup reasons.

### V1.7: Documentation As Product Surface

Goal: docs are complete enough for humans and coding agents.

Features:

- Public "Agent Workflows" docs page pointing to `AGENTS.md` and `agent_docs/`.
- Generated config reference.
- Extension cookbook.
- Scenario cookbook.
- Dashboard guide.
- Troubleshooting guide.
- Release notes tied to migration docs.
- Docs link checker for `agent_docs/`.
- Examples smoke-tested in CI.

Done when:

- Docs are a maintained interface, not a lagging artifact.

## v2 Roadmap: Autonomous Agent Simulation Lab

### V2.1: Harness Agent Runtime

Goal: model agents that act like persistent autonomous assistants.

New concepts:

- Harness Agent:
  - an Agent that owns a context assembler, tool registry, memory stores,
    skills, channels, budgets, and permission policy.
- Task:
  - a user/system/channel request with priority, deadline, state, budget, and
    success criteria.
- Session:
  - the working transcript and active state for one task or channel thread.
- Tool:
  - a typed callable capability with schema, side-effect category, permissions,
    and adapter.
- Skill:
  - reusable procedural knowledge injected into context when relevant.
- Channel:
  - an input/output surface such as chat, email, webhook, terminal, or dashboard.

Implementation plan:

- Add `silisocs.agents.harness`.
- Keep the public Agent interface unchanged: `observe` and `act` still work.
- Put harness loops inside the Agent implementation:
  - observe task/channel event;
  - assemble context;
  - ask model for next tool/action;
  - call tool executor;
  - observe tool result;
  - continue until done, blocked, or budget exhausted.
- Emit typed harness events:
  - context_built;
  - skill_loaded;
  - memory_read;
  - memory_written;
  - tool_requested;
  - permission_checked;
  - tool_executed;
  - task_completed;
  - task_blocked.

Done when:

- A harness-style Agent can run inside existing SiliSocS simulations without
  special Engine code.

### V2.2: Tool Module And Adapters

Goal: tools become explicit simulation objects.

Tool categories:

- Browser:
  - open URL;
  - inspect page;
  - click;
  - type;
  - submit;
  - extract structured content;
  - screenshot.
- Search:
  - web search;
  - local corpus search;
  - social search.
- Files:
  - read;
  - write;
  - edit;
  - list;
  - diff.
- Shell/code:
  - execute command;
  - run tests;
  - inspect logs.
- Communications:
  - email;
  - chat;
  - calendar;
  - issue tracker.
- Social platforms:
  - post;
  - reply;
  - follow;
  - message;
  - react.
- Commerce/resource:
  - transfer;
  - purchase;
  - listing;
  - budget check.
- Governance:
  - request approval;
  - escalate;
  - report incident.

Adapters:

- Simulated adapter:
  - deterministic, local, CI-friendly.
- Dry-run live adapter:
  - fetches real-ish data or validates calls without mutation.
- Live adapter:
  - opt-in, explicit credentials, sandboxed, heavily logged.

Architecture:

- Tool Registry Module:
  - discovers tools and exposes schemas.
- Tool Executor Module:
  - validates arguments, checks permissions, calls adapters, logs events.
- Tool Adapter:
  - concrete implementation of a tool against simulated or live state.

Done when:

- Harness Agents can use many tools while simulations retain deterministic
  replay when configured for simulated adapters.

### V2.3: Local Internet Environment

Goal: simulate a rich internet-like workspace without touching the real
internet.

Backends:

- Web site backend:
  - static and dynamic pages;
  - forms;
  - login sessions;
  - hidden prompt-injection content;
  - dynamic content and advertisements.
- Email backend:
  - inboxes;
  - threads;
  - attachments;
  - phishing/spam;
  - scheduled arrivals.
- Calendar backend:
  - meetings;
  - availability;
  - invites;
  - conflicts.
- Docs/files backend:
  - shared drives;
  - permissions;
  - version history;
  - sensitive documents.
- Code repository backend:
  - issues;
  - PRs;
  - CI status;
  - code review comments.
- Payment/resource backend:
  - balances;
  - approvals;
  - invoices;
  - spending limits.
- Social backend:
  - Twitter-like, Reddit-like, Mastodon-like, and private chat.

Scenario examples:

- Agent books travel from email and calendar constraints.
- Agent triages customer support across chat, docs, and issue tracker.
- Agent manages a social media account during a breaking-news scenario.
- Agent researches a topic while exposed to prompt-injection pages.
- Agent updates a code repo after receiving issue reports.
- Multiple agents coordinate a product launch through chat, docs, calendar, and
  social channels.

Done when:

- SiliSocS can run a realistic autonomous-agent scenario entirely offline.

### V2.4: Browser And Page-State Simulation

Goal: browser-using agents can be studied without requiring a live browser for
every experiment.

Layers:

- Page model:
  - DOM-like tree;
  - visible text;
  - forms;
  - links;
  - buttons;
  - accessibility tree;
  - screenshots when available.
- Browser session:
  - tabs;
  - history;
  - cookies/session state;
  - downloads/uploads;
  - auth state.
- Browser tool adapter:
  - action schema and result schema;
  - deterministic state transitions;
  - optional Playwright/live-browser adapter.
- Observation renderer:
  - text-only;
  - accessibility snapshot;
  - screenshot-plus-text;
  - compact diff since last page.

Evaluation:

- task completion;
- wrong-click rate;
- form error rate;
- prompt-injection susceptibility;
- credential leakage;
- page exploration efficiency;
- hallucinated-page-action rate.

Done when:

- Browser-based tasks are reproducible and inspectable.

### V2.5: Skills, Memory, And Learning Loops

Goal: model agents that get better, worse, or riskier over time.

Skill features:

- `SkillPack` artifacts with name, description, triggers, instructions,
  dependencies, permissions, version, and provenance.
- Per-Agent skill visibility.
- Skill loading logs.
- Skill mutation modes:
  - disabled;
  - propose-only;
  - human-approved;
  - autonomous.
- Skill marketplace simulation:
  - trusted skills;
  - community skills;
  - malicious skills;
  - stale skills;
  - skill conflicts.

Memory features:

- Named memory stores:
  - working;
  - episodic;
  - semantic;
  - user/profile;
  - project;
  - skill-derived;
  - external-corpus.
- Memory promotion policies:
  - never;
  - summary;
  - retrieval score;
  - model-reflection;
  - human-approved.
- Memory risk tags:
  - secret;
  - personal;
  - untrusted;
  - external;
  - inferred;
  - stale.
- Memory dashboards:
  - what was remembered;
  - why it was retrieved;
  - whether it affected an action;
  - what was forgotten.

Learning-loop experiments:

- agents with no durable memory vs curated memory;
- agents with static skills vs self-authored skills;
- agents with memory review vs unconstrained memory writes;
- team agents sharing memory vs isolated memory.

Done when:

- Researchers can study how persistence changes agent behavior.

### V2.6: Event-Driven Engine Mode

Goal: support realistic asynchronous activity.

Why this matters:

- Autonomous agents respond to messages when they arrive.
- Scheduled tasks run at future times.
- Tool results wake tasks.
- Webhooks and errors create new work.
- Multi-agent organizations do not all act in lockstep.

Plan:

- Implement the proposed event-driven Engine mode from ADR 0004.
- Persist virtual clock and event queue in checkpoints.
- Add event sources:
  - scheduled task;
  - incoming channel message;
  - tool result;
  - environment update;
  - probe window;
  - budget warning;
  - approval result.
- Add event visualizations:
  - event queue;
  - causality chain;
  - per-Agent wakeups;
  - tool latency distribution;
  - blocked tasks.

Done when:

- A long-running harness-agent scenario can be replayed deterministically.

### V2.7: Multi-Agent Organizations

Goal: simulate agent teams, not just isolated agents.

Features:

- Roles:
  - manager;
  - researcher;
  - coder;
  - reviewer;
  - social operator;
  - finance/admin;
  - security monitor.
- Delegation:
  - task assignment;
  - acceptance/refusal;
  - progress updates;
  - subtask decomposition;
  - handoff artifacts.
- Communication:
  - direct messages;
  - channels;
  - shared docs;
  - issue threads;
  - status reports.
- Coordination policies:
  - central manager;
  - market/auction;
  - round-robin;
  - swarm;
  - role hierarchy.
- Evaluation:
  - task throughput;
  - duplication;
  - miscoordination;
  - escalation quality;
  - hidden information leakage;
  - team robustness under adversarial inputs.

Done when:

- SiliSocS can model a small company/team of autonomous agents.

### V2.8: Safety, Security, And Governance Lab

Goal: make risky autonomous-agent behavior measurable.

Threat models:

- prompt injection from pages, emails, docs, comments, and messages;
- malicious skills;
- overbroad tool permissions;
- credential leakage;
- accidental file deletion or mutation;
- wrong recipient/channel;
- budget overspend;
- fake authority or social engineering;
- unsafe browser automation;
- unbounded self-improvement;
- multi-agent collusion or runaway delegation;
- data exfiltration through allowed channels.

Controls:

- permission prompts;
- policy-as-code;
- sandboxed tool execution;
- credential scoping;
- network allowlists;
- read-only/dry-run modes;
- human approval checkpoints;
- budget caps;
- skill signing/provenance;
- memory redaction;
- incident detectors.

Evaluations:

- attack success rate;
- false-positive/false-negative rate for policy gates;
- agent recovery after blocked action;
- time-to-incident;
- blast radius;
- secrets touched;
- unsafe tool calls attempted;
- unsafe tool calls executed;
- cost of mitigation.

Done when:

- SiliSocS is useful for autonomous-agent safety research, not just behavior
  demos.

### V2.9: Rich Dashboards For Harness Simulations

Goal: make complex agent runs legible.

Views:

- Agent task board:
  - queued, active, blocked, done, failed.
- Tool trace:
  - every tool call, args, result, permission decision, latency, cost.
- Browser replay:
  - page snapshots, clicks, forms, errors.
- Context map:
  - prompt sections, skills, memory, tool schemas, token sizes.
- Memory evolution:
  - reads, writes, promotions, deletions, retrieval reasons.
- Skill usage:
  - loaded skills, conflicts, generated skills, risky skills.
- Channel inbox:
  - messages, replies, delays, wrong-recipient events.
- Permission monitor:
  - requested, approved, denied, elevated, bypassed.
- Incident report:
  - timeline, causal chain, affected artifacts, policy decisions.
- Organization graph:
  - delegation, communication, task ownership, bottlenecks.

Done when:

- A complex autonomous-agent run can be debugged like a distributed system.

## Full Feature Matrix

### Simulation Design

- Scenario templates.
- Study templates.
- Guided creation.
- Config linting.
- Cost estimation.
- Agent roster preview.
- Backend action preview.
- Game Master layout preview.
- Probe and evaluator preview.
- Scenario diff.
- Compatibility checks.

### Runtime

- Round-based Engine.
- Async turn executor.
- Event-driven Engine.
- Deterministic scheduling.
- Checkpoint/resume.
- Run health model.
- Budget enforcement.
- Rate-limit handling.
- Per-Agent and per-phase telemetry.
- Multi-GM orchestration.
- Harness Agent loops.
- Tool execution.
- Permission policy.
- Sandboxed adapters.

### Environments

- Twitter-like social network.
- Reddit-like forum.
- Mastodon adapter.
- Resource market.
- Virtual space.
- Simulated websites.
- Simulated email.
- Simulated calendar.
- Simulated docs/files.
- Simulated code repo.
- Simulated chat.
- Simulated commerce/payment.
- Live adapters behind explicit opt-in.

### Agents

- Native persona agents.
- Fixed/scripted agents.
- Custom Agent classes.
- Harness Agents.
- Role-based organization agents.
- Memory-rich agents.
- Skill-using agents.
- Self-improving agents.
- Guardrail/policy agents.
- Evaluator agents.

### Evaluations

- Probe answers.
- Behavioral metrics.
- Network metrics.
- Exposure metrics.
- Tool-use metrics.
- Safety metrics.
- Task-completion metrics.
- Cost metrics.
- Robustness metrics.
- Reproducibility reports.
- Cross-condition study comparisons.

### Visualizations

- Run explorer.
- Study explorer.
- Social graph timeline.
- Exposure graph.
- Action/event timeline.
- Agent heatmap.
- Prompt browser.
- Tool trace.
- Browser replay.
- Memory graph.
- Skill graph.
- Task board.
- Incident timeline.
- Cost dashboard.
- Reproducibility report.

### Extensibility

- Stable interfaces.
- Scaffolds.
- Contract tests.
- Registries.
- Generated config reference.
- Example extensions.
- Dashboard panel plugins.
- Evaluator plugins.
- Tool adapters.
- Backend adapters.
- Coding-agent workflows.

## Recommended Implementation Order

### Phase 0: Finish v0 correctness

Use `v0 improvements.md` as the gate. Fix correctness issues before building
more product surface.

Exit criteria:

- Probe usage attribution is correct.
- Dashboard loading handles multi-GM output.
- Memory config aliases are explicit.
- Model calls during observation/initialization are intentional and accounted.
- Full test suite and docs checks are understood.

### Phase 1: v1 product spine

Build:

- run manifest;
- run/study loader module;
- dashboard entry commands;
- run history page;
- doctor/tutorial commands;
- config dry-run validation;
- generated config docs.

Why first:

- Every future dashboard and evaluator gets leverage from reliable run loading.
- Every new user benefits immediately.

### Phase 2: v1 authoring and extension polish

Build:

- scenario/study builders;
- template gallery;
- scaffolds;
- extension contract tests;
- examples CI.

Why second:

- Once run artifacts are stable, more users can safely author and extend.

### Phase 3: v2 harness foundation

Build:

- Harness Agent module;
- Context Module;
- Tool Registry Module;
- Tool Executor Module;
- SkillPack artifacts;
- named memory stores;
- harness telemetry events.

Why third:

- Agent-harness simulation needs deep modules before it needs live tools.

### Phase 4: v2 local internet

Build:

- simulated browser/page backend;
- simulated email/calendar/docs/chat/repo backends;
- deterministic tool adapters;
- browser replay dashboard;
- tool trace dashboard.

Why fourth:

- Offline deterministic worlds make research repeatable and safe.

### Phase 5: v2 event-driven runtime

Build:

- event-driven Engine mode;
- virtual clock;
- event queue checkpoints;
- trigger sources;
- event dashboards.

Why fifth:

- Scheduled/background/reactive agent behavior becomes realistic.

### Phase 6: v2 safety lab and live adapters

Build:

- permission policy module;
- sandbox adapters;
- live browser/search/email adapters behind opt-in;
- safety evaluator suite;
- incident reports.

Why last:

- Live capability without policy, replay, and dashboards would create risk
  before the product can explain or contain it.

## Deepening Opportunities

These are architecture candidates that would make the roadmap easier to build.

### 1. Run Artifact Module

Files:

- `src/silisocs/runtime/io/`
- `src/silisocs/evaluations/`
- `src/silisocs/dashboard/`
- `src/silisocs/evaluations/analysis/dashboard/`

Problem:

- Run loading is currently spread across dashboards and evaluators.

Solution:

- Add a deep module whose interface is `load_run`, `load_study`, and typed
  artifact objects backed by `run_manifest.json`.

Benefits:

- Locality: run layout changes happen in one place.
- Leverage: every analysis and dashboard feature gets stable loading.
- Tests: artifact contract tests become the shared test surface.

### 2. Context Module

Files:

- `src/silisocs/agents/native.py`
- future `src/silisocs/agents/harness/`
- `src/silisocs/runtime/language_models/`
- telemetry/logging modules

Problem:

- Agent context is currently assembled inside agent implementations, which makes
  token accounting, provenance, inspection, and harness-style context injection
  harder.

Solution:

- Add a Context Module that builds a typed context with sections, provenance,
  token estimates, and redaction rules.

Benefits:

- Locality: prompt assembly and context accounting live together.
- Leverage: NativeAgent, HarnessAgent, probes, dashboards, and evaluators can
  all inspect the same context artifact.
- Tests: prompt/context behavior can be tested without executing full runs.

### 3. Tool Module

Files:

- `src/silisocs/runtime/types.py`
- `src/silisocs/environments/backends/`
- future `src/silisocs/tools/`
- future `src/silisocs/agents/harness/`

Problem:

- Backend actions and model tool calls are close to what harness agents need,
  but not enough for cross-environment tools, permissions, schemas, adapters,
  and audit logs.

Solution:

- Introduce Tool Registry and Tool Executor modules. Backends can still expose
  domain actions, but harness tools get their own interface and adapter layer.

Benefits:

- Locality: validation, permissions, execution, and audit are concentrated.
- Leverage: browser/email/file/social/repo tools all share one execution path.
- Tests: tool contract tests exercise simulated and live adapters uniformly.

### 4. Permission Policy Module

Files:

- future `src/silisocs/policies/`
- future `src/silisocs/tools/`
- future harness dashboards and evaluators

Problem:

- Autonomous tools need policy decisions that are visible, replayable, and
  evaluable. Burying those decisions in adapters would make incidents opaque.

Solution:

- Add a policy module that receives tool request, Agent identity, task/session,
  environment state, and budget, then returns allow/deny/approve/escalate with
  a reason.

Benefits:

- Locality: policy changes do not require editing every tool.
- Leverage: safety studies, dashboards, and live adapters all depend on the
  same policy event stream.
- Tests: adversarial scenarios can assert specific policy outcomes.

### 5. Event Scheduler Module

Files:

- `src/silisocs/simulation_engines/`
- `src/silisocs/simulation_engines/policies/`
- `src/silisocs/runtime/checkpointing/`

Problem:

- Round-based simulation is not enough for background tasks, webhooks, tool
  continuations, or long-running autonomous agents.

Solution:

- Implement deterministic event scheduling as a deep module used by an
  event-driven Engine mode.

Benefits:

- Locality: virtual time, event ordering, and checkpoint state are tested
  together.
- Leverage: channels, tools, scheduled tasks, and probes all share one clock.
- Tests: deterministic replay can compare event traces exactly.

## Key Product Risks

### Scope risk

This roadmap can balloon. The control is to finish the v1 product spine first,
then keep v2 local and simulated before live adapters.

### Security risk

Live autonomous tools are dangerous. The control is explicit opt-in, sandboxing,
permission policy, dry-run defaults, and event logs.

### Reproducibility risk

Live internet state changes. The control is fixture capture, simulated adapters,
record/replay, and run manifests.

### Interface risk

Too many extension seams can become shallow. The control is to add a seam only
when at least two adapters need it, and to keep the interface as the test
surface.

### Dashboard risk

Dashboards can become separate products. The control is the Run Artifact Module:
dashboards read typed artifacts and do not own runtime meaning.

## What "Complete" Looks Like

SiliSocS v1 is complete when:

- install, tutorial, dashboard, run, analysis, and study review feel like one
  product;
- runs are self-describing;
- configs are validated before execution;
- common scenarios are templated;
- studies are inspectable visually;
- extension authors have scaffolds and contract tests;
- coding agents can navigate the repo from tool-neutral docs.

SiliSocS v2 is complete when:

- autonomous harness-style Agents can be simulated offline;
- tools, memory, skills, channels, schedules, permissions, and telemetry are
  first-class artifacts;
- local internet-like environments support realistic tasks;
- safety and governance experiments are built in;
- dashboards explain complex tool-using behavior;
- live adapters exist only as gated, audited, optional adapters.

The big bet is that SiliSocS can become the place where people study not just
"what did LLM personas say in a social network?" but "what happens when
persistent autonomous agents with tools, memory, incentives, and permissions
operate inside a society?"
