# OASIS Twitter Polarization Scenario

This scenario measures how ideological echo chambers form and opinions become more extreme.

## Quick Start

```bash
# Run the simulation
cd scenarios/oasis_twitter_polarization
uv run mastodon-sim --config-path conf sim.num_agents=40 sim.num_steps=30

# Run evaluation
python eval/analysis.py
```

## Scenario Details

- **Platform**: Twitter-like
- **Agents**: ~40 users with ideological diversity
- **Duration**: ~30 simulation steps
- **Key Actions**: Post, like, dislike, retweet, follow
- **Recommendation**: Twitter TF-IDF (bio-to-content similarity)

## Evaluation Metrics

- **Opinion extremeness**: Measured via keyword analysis in responses
- **Echo chamber strength**: How many ideologically similar connections
- **Consensus formation**: Whether groups become more homogeneous

## Expected Results

- Agents should follow ideologically similar users
- TF-IDF algorithm should recommend similar-minded content
- Responses should become more ideologically extreme over time
- Two distinct ideological clusters should emerge

## Theory

The personalized TF-IDF recommendation system preferentially shows content similar to user bios, creating filter bubbles and reinforcing existing beliefs.

## Configuration

- `conf/config.yaml` - Main config
- `conf/scenario/oasis_twitter_polarization.yaml` - Scenario params
