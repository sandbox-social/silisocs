# EchoChamberSim Exact Reproduction

This directory indexes the tight, paper-facing reproduction of EchoChamberSim.
The code remains in the shared replication package so existing run artifacts and
imports stay stable, but the conceptual boundary is:

| Layer | Exact reproduction path |
|---|---|
| Scenario config | `replications/echo_chambers/conf/world/default.yaml` |
| Agent config | `replications/echo_chambers/conf/agents/default.yaml` |
| Environment config | `replications/echo_chambers/conf/env.yaml` |
| Engine config | `replications/echo_chambers/conf/sim.yaml` |
| App/state owner | `replications.echo_chambers.components.app.EchoChamberApp` |
| Observation | `replications.echo_chambers.components.observe.EchoChamberObservation` |
| Resolve | `replications.echo_chambers.components.resolve.EchoChamberResolve` |
| Exact agent | `replications.echo_chambers.components.agent.EchoChamberAgentRuntime` |
| Componentized exact prototype | `replications.echo_chambers.components.agent_concordia.Entity` |
| Main five-run in-framework result | `replications/echo_chambers/graph_experiments/replication_main` |
| Main original-code rerun | `replications/echo_chambers/graph_experiments/original_main` |
| H1 opposite-exposure result | `replications/echo_chambers/generated/runs/h1_opposite_exposure` |
| H1 analysis plots | `replications/echo_chambers/generated/analysis/h1_opposite_exposure` |

Exact reproduction assumptions:

- Agents do not choose social-media actions.
- Each active agent receives a precomputed set of neighbor opinions once per day.
- Each active agent emits one structured belief/opinion update.
- Belief state is owned by the app environment and committed synchronously after all agents stage their updates.
- Recommendation is a belief-distance filter over graph neighbors, not a platform feed algorithm.

The loose social-media follow-up starts from this baseline and changes the
action and observation boundary while keeping the app-owned measurement state.
