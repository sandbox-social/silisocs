# Scenario: election_recsys_engagement

The Storhampton election world extended with a recommendation system layer.
Studies how timeline algorithm choice (chronological vs. recsys) shifts voter
engagement, interaction rates, and partisan content amplification.

## Setting

Same as `election` (Storhampton mayoral race) — see `scenarios/election/README.md`.
Adds a configurable recommendation system controlling what each voter sees.

## Agent roles

Same role structure as `election`. Recommendation system is a GM component —
agents are unaware of it; it only controls timeline composition.

## Key dynamics

- Does recsys amplify partisan content relative to chronological timelines?
- How does algorithm choice affect interaction rates (likes, replies, reposts)?
- Are engagement effects stable across random seeds and news bias conditions?

## Run

```bash
uv run silisocs --config-path scenarios/election_recsys_engagement/conf num_steps=15
```

## Studies using this world

- `experiments/studies/election_opinion_program_v1/` — opinion trajectory under news bias
- `experiments/studies/recsys_behavior_sweep/` — timeline mode × engagement rate
