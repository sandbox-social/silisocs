# OASIS Reddit Herding Scenario

This scenario examines how community consensus forms through social influence on upvotes/downvotes.

## Quick Start

```bash
# Run the simulation
cd scenarios/oasis_reddit_herding
uv run mastodon-sim --config-path conf sim.num_agents=50 sim.num_steps=40

# Run evaluation
python eval/analysis.py
```

## Scenario Details

- **Platform**: Reddit-like
- **Agents**: ~50 users
- **Duration**: ~40 simulation steps
- **Key Actions**: Upvote, downvote, post, comment
- **Recommendation**: Reddit hot-score algorithm (engagement + recency)

## Evaluation Metrics

- **Mean engagement score**: Average (upvotes - downvotes) across posts
- **95% CI**: Confidence interval showing consensus stability
- **Distribution**: Shows if engagement is polarized or balanced

## Expected Results

- Posts should accumulate engagement over time
- Community consensus should emerge (similar scores)
- Hot-score algorithm should boost recent posts

## Configuration Files

- `conf/config.yaml` - Main config (loads OASIS defaults)
- `conf/scenario/oasis_reddit_herding.yaml` - Scenario specifics
