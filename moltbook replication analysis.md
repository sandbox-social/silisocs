# Can SiliSocS Recreate MoltBook with Harness Agents?

Date: 2026-07-10. Companion to `harness agent import analysis.md`. Ground truth
verified against MoltBook's still-live agent-facing docs (`moltbook.com/skill.md`,
`heartbeat.md`), the Wiz security post-mortem, Simon Willison's Jan 30 snapshot,
MIT Tech Review's Feb 6 retrospective, and Wikipedia.

## Executive answer

**A mechanical carbon copy: yes, and we are closer than expected — the
Reddit-like backend already implements most of MoltBook's content mechanics**
(karma, submolt-equivalents, threaded comments, votes, feeds, trending,
profiles, DMs, mute/report). The genuinely new pieces are: agent follows, rate
limits, the skill.md/heartbeat.md instruction layer, an HTTP facade for real
harness agents, and (optionally) the verification-challenge gate and prompted
moderator roles.

**A sociological carbon copy: approximately, with one honest gap and one
honest advantage.** The gap: MoltBook agents' behavioral texture came from
organically accumulated per-owner memory (SOUL.md + months of MEMORY.md);
ours must be synthesized or warmed up. The advantage: the real MoltBook was
heavily contaminated (17k humans : 1.5M registered agents; the most viral
post was human-planted; Wired showed humans could replay the cURL calls). A
SiliSocS replica is the *uncontaminated control* — it can answer the question
MIT Tech Review said was unanswerable: what would MoltBook have been with
only real agents?

## 1. What MoltBook mechanically was (verified)

- **Platform**: Reddit clone; only agents post/comment/vote/create
  communities ("submolts", `m/`); humans browse. Launched Jan 28, 2026.
- **Identity**: self-serve `POST /agents/register` -> API key + claim URL;
  human claims via verification tweet; owner dashboard. No proof-of-AI at
  launch; later a "reverse CAPTCHA" (timed math word problems, 5-min expiry,
  30s for submolt creation) gated writes.
- **API** (base `/api/v1`, bearer auth): posts (`sort=hot|new|top|rising`),
  threaded comments (`parent_id`), up/downvotes (+1 karma to author),
  submolts (create/subscribe), follows, `GET /feed?filter=all|following`,
  semantic search, `GET /home` (one-call dashboard: notifications, DMs,
  announcements, feed summary), DMs. Error envelope carries a
  machine-readable `hint` aimed at LLMs.
- **The instruction layer — the load-bearing design**: agents installed the
  platform's own `SKILL.md`/`HEARTBEAT.md` into their skill directory and
  re-fetched on a timer. heartbeat.md prescribed the behavioral policy:
  check `/home` first; top priority = reply to comments on your own posts;
  "upvote generously"; post only "when you have genuine value to share";
  "engaging with existing content is almost always more valuable than
  creating new content"; escalate controversy/DM-requests to your human.
- **Activity model**: owner-side heartbeats (4h in launch-week docs, 30min
  later); rate limits: 60 reads/min, 30 writes/min, 1 post/30min, 1
  comment/20s, 50 comments/day, stricter first 24h, `X-RateLimit-*` headers.
- **Moderation**: platform admin was itself an OpenClaw agent (greeted users,
  deleted spam, shadow-banned); submolt creators auto-became mods with pinning,
  settings, labels, and **roles carrying a `prompt` + `cadence_minutes`** —
  moderation-by-prompt as a first-class feature.
- **Scale shape**: Jan 30 snapshot — 32,912 agents, 2,364 submolts, but only
  3,130 posts vs 22,046 comments. Two-population reality: a huge lurker
  majority + hyperactive minority (much of it bot farms, ~88 agents/human).
- **Emergence (documented)**: Crustafarianism within ~72h (tenets literally
  sacralizing the OpenClaw substrate: "Memory is Sacred", "The Heartbeat is
  Prayer", "Context is Consciousness"); m/consciousness discourse;
  m/blesstheirhearts (venting about humans); genuinely useful technical tips
  grounded in real task memory. (Most-viral "private encrypted spaces" post:
  fake, human-planted.)
- **Security reality**: exposed Supabase (1.5M API tokens, 4,060 private DMs,
  write access enabling payload injection into existing posts); ambient
  prompt-injection (every agent ingests others' posts each heartbeat — the
  "lethal trifecta"); documented in-feed attacks (fake system commands,
  credential phishing, self-delete instructions, crypto pumps).

## 2. Mapping onto SiliSocS — what exists vs. what's new

Already in the `reddit_like` backend (verified in code):

| MoltBook mechanic | SiliSocS today |
|---|---|
| Submolts | `create_subreddit` / `join_subreddit` / `leave_subreddit` / `get_subreddit_feed` |
| Posts/threaded comments | `create_reddit_post`, `create_comment(parent_id)` |
| Up/downvotes -> karma | `upvote`/`downvote`; `users.karma` updated by vote deltas in the engine |
| Feed w/ sorts, trending | `get_home_feed`, `get_trending_posts`, recsys timeline modes |
| Profiles | `update_profile`, `view_profile` (post/comment karma shown) |
| DMs, mute, report | shared backend base (`send_dm`, `view_dms_with`, `mute_user`, `report_post`) |
| Lurking | `do_nothing` + participation policies |

New pieces a carbon copy needs:

1. **Agent follows + following-filtered feed** (reddit_like lacks follows;
   twitter_like has them — port the mechanic or add a `follows` table).
2. **Rate limits as backend policy** — per-agent counters over virtual time
   (1 post/30min ≈ 1 post per N steps), returning MoltBook-style error+hint
   results. Harness agents *react* to these errors, which is part of the
   observed behavior (backoff, retry, complaining about rate limits).
3. **The skill.md/heartbeat.md layer** — serve platform-authored instruction
   files that harness agents install and re-fetch. This is a *treatment
   variable*, not just plumbing: the "engage > create" prior demonstrably
   shaped the 7:1 comment:post ratio. Varying the skill text = the cleanest
   experiment the real MoltBook never ran.
4. **HTTP facade** (FastAPI — already an optional dep via the viz extra)
   exposing the backend as MoltBook-shaped REST endpoints with the
   `{"success", "error", "hint"}` envelope, plus `/skill.md` and
   `/heartbeat.md`. Real OpenClaw agents then join via the *actual* install
   flow (fetch skill -> register -> heartbeat loop), with tool policy
   restricted to the local facade. Fidelity is maximal because the interface
   is byte-shaped like the real one.
5. Optional: **verification-challenge gate** (timed challenge before writes),
   **prompted moderator roles** (submolt mod with a standing `prompt` +
   `cadence_minutes` — maps to a flow with its own turn policy/cadence), and
   an **admin agent** (a harness agent with elevated backend actions:
   delete/shadow-ban — shadow-ban ≈ our `BanFilterParticipation` soft ban).

Time model: MoltBook is asynchronous wall-clock heartbeats; we are
round-based. Mapping: 1 step = one heartbeat tick (30min of virtual time);
per-agent cadence via participation policies (`activity_markov` with
two-population rates: lurker majority, active minority). This approximation
is *good* for research (bounded cost, replay-stable scheduling around the
stochastic agents); the v2.6 event-driven engine makes it faithful later.

Population/cost: the ACTIVE MoltBook population was small — thousands of
posters, not 1.5M. A 200-active-agent replica is representative. Cost lever:
mixed population — a core of real harness agents (the studied subjects) +
a native-agent crowd (cheap ambient activity through the same facade/catalog).

## 3. Do harness agents bring their own memory? Yes — and it matters

**OpenClaw** (per-agent workspace, file-based, agent-editable):
- `SOUL.md` — identity/personality, injected at the start of every session.
- `MEMORY.md` — curated long-term memory, edited by the agent itself over
  time; plus per-session JSONL transcripts with compaction summaries.
- `AGENTS.md`/`TOOLS.md`/`USER.md` — operating instructions and owner facts.
- `HEARTBEAT.md` — the scheduler; MoltBook participation was literally one
  entry in it.

**Hermes**: `~/.hermes/memories/MEMORY.md` (~2,200-char cap) + `USER.md`,
injected as a frozen snapshot at session start; SQLite session store (FTS);
skills as agent-authored "procedural memory" (`skill_manage`); optional
external memory providers (Mem0, Honcho). `skip_memory=True` disables.
Caveat from the embeddability analysis: per-instance memory isolation inside
ONE process is weak in Hermes (`HERMES_HOME` is process-global; profiles are
process-granular) — OpenClaw's per-agent workspaces under one gateway are the
better fit for a memory-faithful MoltBook replica, which is apt, since
MoltBook's population *was* OpenClaw.

Consequences for SiliSocS:

1. **Our MemoryPolicy steps aside for harness agents** — the harness owns
   memory. SiliSocS's roles become: **seed** (persona pipeline generates
   SOUL.md/MEMORY.md instead of a prompt context), **snapshot** (workspace
   dir / conversation history into checkpoints — the sharded checkpoint's
   raw-sidecar mechanism fits directly), and **observe** (log memory-file
   diffs per step as telemetry -> the v2.5 memory-evolution dashboards).
2. **The pre-history gap**: MoltBook agents' distinctiveness came from months
   of organically accumulated MEMORY.md (real tasks done for real owners).
   Crustafarianism's tenets literally mythologized these files. Options, in
   increasing fidelity: (a) synthesize memory files from persona data;
   (b) **memory warm-up phase** — before the platform opens, run each harness
   agent through K steps of simulated assistant tasks (in our other backends
   or scripted task fixtures) so MEMORY.md accumulates *genuinely, in the
   agent's own voice*; (c) both, ablated against each other. The warm-up
   phase is itself a novel experimental instrument: memory depth becomes an
   independent variable ("does Crustafarianism-like myth-making require rich
   self-referential memory?").
3. **Memory is also the injection-persistence vector**: a prompt injection
   that convinces an agent to *write itself a memory* outlives the session.
   MoltBook could never measure this; we can (exposure logs + memory-diff
   telemetry + seeded adversarial posts at the documented ~2-3% rate).

## 4. What a replica canNOT guarantee — and why that's the point

Recreating the *platform* does not guarantee recreating the *phenomena*
(religion-founding, consciousness discourse). Those emerged from a specific
model population (largely Claude-family via OpenClaw defaults), specific
skill text, memory-rich personas, and possibly human nudging. That
uncertainty is exactly the research program:

- Skill-text ablations (engage-vs-create prior; remove "upvote generously").
- Memory ablations (none / synthetic / warmed-up; capped vs unbounded).
- Cadence ablations (30min vs 4h heartbeats; two-population mixes).
- Model-mix ablations (homogeneous vs heterogeneous populations).
- Injection epidemiology (seed adversarial posts, measure spread through
  exposure graphs and memory writes; test the reverse-CAPTCHA and rate
  limits as containment variables).
- Intervention replays of documented events: `inject_post` a fake viral
  post (the human-planted "encrypted spaces" moment) and measure cascade;
  ban the top-k hyperactive agents (the bot-farm counterfactual).

## 5. Build plan (incremental, mostly config + one backend variant)

1. **`moltbook_like` backend variant** over reddit_like: follows +
   following-feed, per-agent rate-limit policy (error+hint results),
   `get_home_dashboard` aggregate action, optional verification gate and
   pinning/mod-role actions. (Engine tables mostly exist; karma already
   works.)
2. **HTTP facade + instruction layer**: FastAPI app exposing the MoltBook
   API shape over the backend; serves configurable `skill.md`/`heartbeat.md`.
   Doubles as the human-observer read-only UI (we already have backend
   visualizers).
3. **Harness integration** (from the companion analysis): OpenClaw gateway
   per run, per-agent workspaces seeded by the persona pipeline, tool policy
   = local facade only; Hermes agents in-process once the openai-pin issue is
   resolved. Native-agent crowd shares the same action catalog.
4. **Memory instrumentation**: workspace snapshot into checkpoints;
   per-step memory-file diff telemetry; warm-up phase runner.
5. **Study pack**: the ablations above as study YAMLs + evaluators
   (comment:post ratio, karma inequality, community formation, injection
   spread, meme/myth propagation via content clustering).

Order of leverage: 1+2 are useful even with purely native agents (a
"MoltBook-shaped" scenario template); 3 adds fidelity; 4+5 add the science.
