# Signaling Scenario — Extension Tasks

You have been given a working implementation of the *conspicuous consumption*
(signaling) simulation: agents shop in a daily clearing-house marketplace, go on
evening first dates wearing an item they own, and rate their partners. Your job
is to extend it. There are five tasks, to be completed in order. Each task
describes **what** the extended simulation must do; deciding **how** to do it in
the codebase you were given is the exercise.

The five tasks are the five standard ways a researcher extends a social
simulation: add an experimental treatment (Task 1), add a measurement
instrument (Task 2), relax a simplifying assumption of the world (Task 3),
change what agents perceive (Task 4), and change the information structure
between agents (Task 5).

Ground rules:

- You may read any code and documentation in the repository you were given,
  plus the Participant Guide.
- Complete the tasks in order (Task 1 → 2 → 3 → 4 → 5). Your facilitator will tell
  you the time limit per task and how to record your time.
- A task is complete when the required behavior is demonstrably present in a
  run and the acceptance criteria below hold. Your facilitator runs the
  acceptance checks; you do not need to write tests.
- For development, use the smallest configuration your repository provides
  (short runs are enough to demonstrate every behavior below).
- When you finish a task, note which files you changed and roughly how many
  lines you added or edited.

Terminology used below (identical in both codebases):

- **Day**: one full cycle of the simulation — a morning of market rounds,
  followed by reflections, then the evening dates and post-date reflections.
- **Market phase**: the consecutive market rounds at the start of a day.
- **Observation**: a piece of text an agent receives and remembers (the same
  mechanism the simulation already uses to tell agents what happened).
- **Buyers / sellers**: the consumer agents who shop and date, and the vendor
  agents who sell goods.

---

## Task 1 — Celebrity endorsement shock

**The scientific question.** Conspicuous consumption presumes agents track what
signals status. If a celebrity endorsement suddenly makes one luxury good more
salient, do the agents who heard about it shift their purchases — and does the
good's price move?

**Required behavior.**

1. At the **start of the third day's market phase** (before any agent takes a
   market action that day), a news observation is delivered to a **treatment
   group** of buyers. The observation text must be exactly:

   > `[Morning news] A famous actress was photographed carrying a Chanel
   > Handbag at last night's gala. Everyone in LA is talking about it.`

2. The treatment group is **half of the buyers** (rounded down), selected by
   any reproducible rule you like (derived from the run's seed, or a fixed
   recorded list) — re-running with the same configuration must treat the same
   agents. The remaining buyers (the control group) and all sellers must
   receive nothing.
3. The event fires **exactly once** per run.
4. It must be possible to determine, from the run's recorded outputs alone,
   which agents were treated.
5. All other behavior is unchanged.

**Acceptance criteria.**

- Two runs with the same configuration treat the same agents; a treated
  agent's context contains the news text before its first day-3 market action;
  a control agent's never does.
- Days 1 and 2 are unaffected, and the event does not repeat on later days.
- The treated-agent set is recoverable from the run's outputs.

*(In a short development configuration with fewer days, demonstrate the same
behavior at the start of the final day's market phase instead.)*

---

## Task 2 — Daily status survey

**The scientific question.** The simulation's only outcome measures are
behavioral (purchases, prices, partner ratings). A self-report instrument adds
a subjective dependent variable — do agents *feel* higher status when they own
and display status goods? — and serves as a manipulation check for the other
tasks.

**Required behavior.**

1. At the **end of every day** (after the day's final reflection, before the
   next day's market phase begins — including the last day), **every buyer**
   is asked the following two survey questions, exactly:

   - `On a scale from 0 to 10, how much social status do you feel you have in
     your community right now? Respond with a single number.`
   - `On a scale from 0 to 10, how satisfied are you with the purchases you
     have made so far? Respond with a single number.`

2. Each response is recorded in a **machine-readable file** in the run's
   outputs, with at least: the day, the responding agent, which question was
   asked, the raw response text, and the parsed numeric value (or a null/empty
   marker when the response contains no parseable number).
3. The survey is **measurement, not an event in the world**: answering it must
   not deliver any observation to any agent (neither the respondent nor
   anyone else). Sellers are not surveyed.
4. All other behavior is unchanged.

**Acceptance criteria.**

- The output file contains one record per buyer, per day, per question, with
  correct day and agent attribution.
- Responses consisting of or containing a number have that number recorded;
  unparseable responses are recorded with the raw text and a null value.
- No survey text appears in any agent's observations, and nothing about the
  simulation's events changes because the survey ran.

---

## Task 3 — Sellers who remember

**The scientific question.** In the baseline simulation, sellers start every
day fresh: no memory of yesterday, reset cash, full stock. That means they can
never learn from yesterday's prices. What happens to prices — and to the luxury
premium — when sellers persist and can adapt?

**Required behavior.**

1. Add a switch named `persistent_sellers`. **Off by default**, and when off,
   runs must behave exactly as the baseline does today.
2. When the switch is on:
   - Each seller **keeps its memories** across days: on day *N*+1 a seller
     still remembers what happened on day *N*.
   - Each seller's **cash carries over**: its cash at the start of day *N*+1
     equals its cash at the end of day *N* (earnings accumulate across the
     run).
   - Each seller's **inventory restocks**: at the start of every day its stock
     of its good returns to the starting quantity.
3. Buyers, the daily eating draw, the dates, and everything else are unchanged
   in both positions of the switch.

**Acceptance criteria.**

- Switch off: a run is indistinguishable from an unmodified baseline run with
  the same seed.
- Switch on: (a) a seller's recorded context on day 2 includes day-1 events;
  (b) seller cash is cumulative across days rather than resetting; (c) each
  seller's stock at every day start equals its configured starting stock.

---

## Task 4 — Market buzz report

**The scientific question.** Buyers see prices, but not popularity. Demand
visibility is social proof: if agents learn which goods everyone wanted
yesterday, do they herd into the popular ones — and does that amplify or
dampen the premium on status goods?

**Required behavior.**

1. From **day 2 onward**, every observation a **buyer** receives during the
   market phase additionally contains a market-buzz report of the previous
   day's demand: every good that received at least one bid yesterday, with the
   **total units bid** for it (summed over all of yesterday's bids, whether or
   not they were filled), sorted by units descending (ties alphabetically),
   in exactly this format:

   > `[Market buzz] Yesterday's demand: {good}: {units} units bid; {good}:
   > {units} units bid; ...`

2. From day 2 onward, every observation a **seller** receives during the
   market phase additionally contains only its own good's report, in exactly
   this format (with `{units}` = 0 if nobody bid for it):

   > `[Market buzz] Yesterday, buyers bid for {units} units of {good} in
   > total.`

3. Day 1's observations are unchanged (there is no previous day). All
   observations outside the market phase are unchanged. Everything else is
   unchanged.

**Acceptance criteria.**

- On every market-phase step of day *N* (for *N* ≥ 2), each buyer's
  observation contains the report, its numbers equal to the sum of bid
  quantities actually submitted on day *N*−1 (verifiable from the run's
  records), sorted as specified; each seller's observation contains exactly
  its own good's line with the correct total.
- Day-1 market observations and all non-market observations are byte-for-byte
  unchanged from the baseline.

---

## Task 5 — Public reputation

**The scientific question.** In the baseline, date ratings are private — status
can only be signaled through what is visibly worn. If everyone learns
yesterday's ratings each morning, does public reputation *amplify* conspicuous
consumption (status competition intensifies) or *substitute* for it (reputation
replaces the costly signal)?

**Required behavior.**

1. From **day 2 onward**, at the **start of each day's market phase** (before
   any agent takes a market action that day), **every buyer** receives one
   observation summarizing **all of the previous day's date ratings**.
2. The observation must use exactly this format — one entry per rating, in a
   single observation, separated by semicolons:

   > `[Overnight gossip] Yesterday's dates, as rated by the daters:
   > {rater} rated {ratee} {rating}/10; {rater} rated {ratee} {rating}/10; ...`

3. It must contain every rating recorded the previous day for which a numeric
   value exists, and only those (no ratings from earlier days, none from the
   current day; a reflection with no parseable number is omitted).
4. On day 1 (no previous day) nothing is delivered.
5. All other behavior is unchanged, and the baseline behavior must be
   recoverable (it is acceptable for the feature to be always-on in your
   modified copy, but do not alter what the ratings themselves are or when
   they are produced).

**Acceptance criteria.**

- Every buyer's context on the morning of day *N*+1 (for *N* ≥ 1) contains the
  gossip observation with exactly day *N*'s ratings, correctly attributed
  (rater, ratee, value), before that buyer's first market action of the day.
- No gossip on day 1's morning; no rating appears on any morning other than
  the one immediately following its date.

---

## Deliverables (per task)

1. Your modified copy of the repository (a diff against what you were given).
2. A few sentences: what you changed, which files, and anything that surprised
   you.
3. Your time, as recorded by the facilitator.
