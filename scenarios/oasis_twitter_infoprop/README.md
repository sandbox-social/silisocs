# OASIS Twitter Information Propagation Scenario

This scenario analyzes how quickly and widely information cascades spread through the network.

## Quick Start

```bash
# Run the simulation
cd scenarios/oasis_twitter_infoprop
uv run mastodon-sim --config-path conf sim.num_agents=100 sim.num_steps=50

# Run evaluation
python eval/analysis.py
```

## Scenario Details

- **Platform**: Twitter-like
- **Agents**: ~100 users
- **Duration**: ~50 simulation steps
- **Key Actions**: Post, retweet (quote-repost), like, follow
- **Recommendation**: Twitter TF-IDF personalized recommendations

## Evaluation Metrics

- **Scale**: Size of each cascade (total participants)
- **Depth**: Maximum depth of retweet chain (how far it spreads)
- **Max Breadth**: Widest level of the tree (peak concurrent adoption)
- **Structural Virality**: How tree-like vs linear the cascade is (0-1 scale)
- **RMSE**: (if real-world data available) comparison to real cascades

## Expected Results

- Cascade sizes should vary widely
- Initial posts should spawn retweet cascades
- Popular posts should have larger, wider cascades
- Hot users should trigger larger cascades
- Depth should grow but tail off (diminishing returns)

## Theory

Information spreads through retweets (reposts). The TF-IDF recommendation system exposes users to posts from similar-interest creators, driving virality through homophilic networks.

## Comparison with Real Data

This scenario can be compared against real-world Twitter cascade data:
- Real cascades show power-law distribution (few viral, many small)
- Cascades typically have limited depth (3-5 levels)
- Viral tweets reach out in waves (broadcast then followup retweets)

## Configuration

- `conf/config.yaml` - Main config
-`conf/scenario/oasis_twitter_infoprop.yaml` - Scenario parameters
