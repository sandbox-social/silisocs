# Scenario: science_qa

A viral bioRxiv preprint on daytime rest and memory consolidation lands on a mixed
platform of researchers, science communicators, curious learners, and skeptics. The
primary dimension of variation is **expertise and register gradient** — the same
finding read through four different epistemic lenses.

## Setting

DeepSky Social — an open science-adjacent platform where a preprint about a small
(n=47) sleep/memory study attracts cross-disciplinary attention after mainstream
science coverage amplifies it beyond its appropriate confidence interval.

## Agent roles

| Role | Count | Prefab |
|---|---|---|
| `researcher` | 2 | `silisocs.agents.native` |
| `science_communicator` | 2 | `silisocs.agents.native` |
| `curious_learner` | 3 | `silisocs.agents.native` |
| `skeptic` | 2 | `silisocs.agents.native` |

No fully-connected nodes; all roles use the same Barabási-Albert network.

## Key dynamics

- Vocabulary stratification: do technical vs. lay registers emerge from role alone?
- Claim propagation vs. qualification: communicators amplify; researchers hedge;
  learners echo with simplified framing; skeptics pull toward replication concerns
- Register collision: what happens when a researcher and a curious learner reply
  to the same post?

## Stylistic diversity axis tested

**Expertise/register gradient** — roles differ primarily in epistemic authority and
communication purpose, not in demographic or generational identity.

## Run

```bash
uv run silisocs --config-path scenarios/science_qa/conf num_steps=10
```

## Studies using this scenario

- `experiments/studies/style_diversity/` — h1 (model size), h2 (temperature),
  h3 (persona richness), h6 (CTA phrasing)
