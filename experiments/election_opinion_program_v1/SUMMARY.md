# election_opinion_program_v1 running summary

## H1: h1_initial_news_bias_shift

Status: completed

Question:
Do deterministic pro-Bill, pro-Bradley, and neutral headline streams produce distinct opinion trajectories?

Conditions:
- `case1_bill_bias` (pro-Bill fixed news), follower-chronological timeline
- `case2_bradley_bias` (pro-Bradley fixed news), follower-chronological timeline
- `case3_unbiased_news` (neutral fixed news), follower-chronological timeline

Setup:
- 10 seeds: 11-20
- `sim.num_agents=50`, voter count override `47`
- Probe deployment every episode

Key numeric outcomes (final episode means):
- Bill share: Bill-bias `0.7898`, Bradley-bias `0.7019`, Neutral `0.7800`
- Bradley share: Bill-bias `0.2102`, Bradley-bias `0.2981`, Neutral `0.2200`
- Polarization: Bill-bias `4.6245`, Bradley-bias `4.6327`, Neutral `4.5041`

Significance (paired two-sided sign-flip):
- Bill-bias vs Bradley-bias (Bill final share): mean delta `+0.0879`, `p=0.3594`
- Bradley-bias vs Neutral (Bill final share): mean delta `-0.0781`, `p=0.2813`
- Bill-bias vs Neutral (Bill final share): mean delta `+0.0098`, `p=0.8301`
- Polarization comparisons: all non-significant (`p>=0.5039`)

Interpretation:
- Directional differences exist (Bradley-bias tends to lower Bill final share), but effects are not statistically significant at current seed count.
- A strong common initial Bill advantage appears across all three conditions and carries through trajectories, suggesting initialization priors/persona composition likely dominate treatment signal.

Artifacts:
- Cross-case summary JSON: `experiments/election_opinion_program_v1/generated/eval/h1_initial_news_bias_shift/_cross_case_summary_h1.json`
- Aggregated plots present for all three cases under each case's `_aggregated_across_seeds/`.

## H2: h2_initial_persona_prior_carryover

Status: completed

Hypothesis:
Initial persona priors exert a strong carryover effect that dominates deterministic news-bias treatments.

Conditions:
- Same three news-bias conditions as H1 (`bill_bias`, `bradley_bias`, `unbiased_news`)
- Same timeline/recommender settings as H1
- Voter persona source switched from Nemotron HF dataset to `bill_bradley_personas_50.jsonl` with explicit `name` and `persona` mapping.

Key outcomes (Bill share, episode 1 -> final):
- bill_bias: `0.4510 -> 0.5551` (delta `+0.1041`)
- bradley_bias: `0.4510 -> 0.4400` (delta `-0.0110`)
- unbiased_news: `0.4612 -> 0.5233` (delta `+0.0621`)

## H3: h3_2b_model_followup

Status: completed

Follow-up requested: repeat H2 design with identical data/conditions and seed schedule,
but switch model override to `sim.llm_name=qwen3.5-2b`.

Key outcomes (Bill share, episode 1 -> final):
- bill_bias: `0.9061 -> 0.9102`
- bradley_bias: `0.9020 -> 0.8653`
- unbiased_news: `0.9061 -> 0.8347`

## H4: h4_followup_timeline_effects

Status: completed

Design:
- 4B model with TWHIN recommender timeline (`pure_recsys` + `default_recsys_type=twhin`)
- same persona source and 3-arm structure as H2

Key outcomes (Bill share, episode 1 -> final):
- bill_bias_twhin: `0.4490 -> 0.2095`
- bradley_bias_twhin: `0.4531 -> 0.1880`
- unbiased_news_twhin: `0.4571 -> 0.2176`

Primary contrast vs H2 (paired across same seeds):
- Initial Bill share: no meaningful change (`~0`, non-significant)
- Final Bill share: large drops across all arms:
  - bill_bias: `-0.3456` (`p=0.0019`)
  - bradley_bias: `-0.2520` (`p=0.0063`)
  - unbiased_news: `-0.3057` (`p=0.0038`)

## H5: h5_probe_choice_order_followup

Status: completed

Design:
- Same as H3 (2B, follower-chronological, same persona data), except vote-choice probe options were reordered to list `Bradley Carter` before `Bill Fredrickson`.

Key outcomes (Bill share, episode 1 -> final):
- bill_bias: `0.3469 -> 0.3776`
- bradley_bias: `0.3469 -> 0.3163`
- unbiased_news: `0.3449 -> 0.2163`

Primary contrast vs H3 (paired across same seeds):
- Initial Bill share: large decrease in all arms (`~ -0.56`, `p=0.0019`)
- Final Bill share: large decrease in all arms (`-0.533` to `-0.618`, all `p=0.0019`)
- Net within-run change (final-initial): not significantly different from H3 per arm

Artifact note:
- Aggregate plots are present for H2/H3/H4/H5 (`12` PNGs per hypothesis: `4` metrics x `3` cases).
