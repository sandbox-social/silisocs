# Echo Chambers Replication

This directory contains the EASE/SiliSocs reproduction and ablations for
`Decoding Echo Chambers: LLM-Powered Simulations Revealing Polarization in
Social Networks` (`EchoChamberSim`).

The replication is organized around two layers:

- **Exact reproduction**: rebuilds the original opinion-network simulator in
  the SiliSocs framework while preserving the paper's direct neighbor-opinion
  exposure, short/long memory, and daily belief update structure.
- **Loose social follow-up**: relaxes the strict opinion-update interface into
  a Twitter-like environment where agents can post, reply, repost, and like
  before reporting belief through a structured probe.

Generated run outputs are intentionally ignored by git. New runs write under
`replications/echo_chambers/generated/`, `runs/`, or `graph_experiments/`.

## Directory Layout

```text
replications/echo_chambers/
  components/      custom app, agent, GM, observe, resolve, and policy classes
  conf/            Hydra config fragments for exact and loose-social variants
  docs/            design notes for exact and loose-social setups
  evaluators/      paper-style metrics and plotting helpers
  input/           public static inputs: personas, initial beliefs, networks
  tools/           standalone reproduction scripts
  study.yaml       hypothesis-driven study specification
```

## Prerequisites

From the repository root:

```bash
cd /home/sneheel/mastodon-sim
uv sync
```

For OpenAI-backed runs, put `OPENAI_API_KEY` in `.env`.

The optional upstream-code comparison script expects the original repository at:

```text
/home/sneheel/EchoChamberSim
```

or pass `--original-root` if you keep it elsewhere.

## Exact In-Framework Graph Reproduction

This runs the main graph experiment over scale-free, random, and small-world
networks using the SiliSocs implementation.

```bash
uv run python replications/echo_chambers/tools/run_replication_graph_experiment.py \
  --runs 5 \
  --parallel 5 \
  --skip-existing
```

Useful lower-cost variants:

```bash
# Print commands without running.
uv run python replications/echo_chambers/tools/run_replication_graph_experiment.py --dry-run

# Run only the scale-free condition.
uv run python replications/echo_chambers/tools/run_replication_graph_experiment.py \
  --network-types scale_free \
  --runs 5 \
  --parallel 2 \
  --skip-existing
```

Outputs are written to:

```text
replications/echo_chambers/graph_experiments/replication_main/
```

## Optional Upstream-Code Comparison

This runs the original EchoChamberSim codebase and recomputes paper-style
metrics. This is useful for sanity checking, but it is not required for the
EASE replication.

```bash
uv run python replications/echo_chambers/tools/run_original_graph_experiment.py \
  --original-root /home/sneheel/EchoChamberSim \
  --runs 5 \
  --parallel 5 \
  --skip-existing
```

Outputs are written to:

```text
replications/echo_chambers/graph_experiments/original_main/
```

## Study Runs

The study file defines the hypothesis sequence used in the paper:

- `h1_opposite_exposure`: similarity vs opposite exposure on the exact
  scale-free reproduction.
- `h1_random_exposure`: similarity vs random exposure on the exact scale-free
  reproduction.
- `graph_structure_replication`: exact graph-type reproduction.
- `recommendation_policy_replication`: exact recommendation-policy comparison.
- `h3_loose_action_structure`: loose Twitter-like action interface.
- `h4_self_state_feedback`: with vs without explicit prior belief/opinion
  feedback.
- `h5_simple_social_agents`: Echo short/long-memory agent vs simpler social
  agent.
- `h6_qwen35_4b_self_state_feedback`: Qwen3.5-4B self-state feedback test.
- `h7_qwen35_4b_simple_agent_architecture`: Qwen3.5-4B memory architecture
  test.

Dry-run any condition before spending API budget:

```bash
uv run python experiments/run_study.py \
  --study replications/echo_chambers/study.yaml \
  run \
  --only-hypothesis h3_loose_action_structure \
  --only-condition loose_social_follower_chronological_5seed \
  --max-concurrent 2 \
  --dry-run
```

Run a condition:

```bash
uv run python experiments/run_study.py \
  --study replications/echo_chambers/study.yaml \
  run \
  --only-hypothesis h3_loose_action_structure \
  --only-condition loose_social_follower_chronological_5seed \
  --max-concurrent 2
```

Run all conditions for one hypothesis:

```bash
uv run python experiments/run_study.py \
  --study replications/echo_chambers/study.yaml \
  run \
  --only-hypothesis h5_simple_social_agents \
  --max-concurrent 2
```

Study outputs are written to:

```text
replications/echo_chambers/generated/runs/<hypothesis>/<condition>/seed_<seed>/run/
replications/echo_chambers/generated/analysis/<hypothesis>/
```

## Qwen / Local vLLM Runs

The Qwen hypotheses use an OpenAI-compatible local endpoint:

```text
http://127.0.0.1:30000/v1
```

On the Narval cluster, use the study array worker scripts from
`slurm_scripts/`. For the Qwen echo-chamber runs we used a 4-GPU vLLM server,
`VLLM_MAX_LEN=50000`, and `MAX_CONCURRENT=5` for the study runner.

## Analysis

Per-hypothesis comparison plots are generated by the study evaluator when at
least two conditions have metrics. To refresh plots after runs finish:

```bash
uv run python replications/echo_chambers/evaluators/plot_hypothesis_conditions.py \
  --run-dir replications/echo_chambers/generated/runs/h5_simple_social_agents/loose_social_simple_agent_5seed/seed_6201/run \
  --output replications/echo_chambers/generated/analysis/h5_simple_social_agents/refresh_eval.json
```

For exact exposure ablations:

```bash
uv run python -m replications.echo_chambers.evaluators.analyze_h1_opposite_exposure
uv run python -m replications.echo_chambers.evaluators.analyze_h1_random_exposure
```

## Notes

- Static input files in `input/` are small and intended to be committed.
- Generated databases, prompt logs, metrics, plots, and Slurm logs are ignored.
- See `docs/exact_reproduction.md` and `docs/loose_social.md` for design
  details on the two simulation variants.
