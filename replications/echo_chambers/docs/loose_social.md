# Echo Chambers Loose Social-Media Follow-Up

This setup loosens the exact reproduction's strict action assumption.

Agents now act in the normal twitter-like backend with a restricted action set:

- `create_tweet`
- `reply_to_tweet`
- `repost_tweet`

The app is `EchoChamberSocialApp`, which subclasses the exact reproduction app.
It still owns `EchoChamberWorld` for belief-state measurement and paper-aligned
metrics, but the visible social environment is the twitter-like SQLite backend.

Recommendation/exposure is implemented as GM observation formation:

1. The app retrieves the agent's real timeline from the twitter-like backend.
2. `EchoBeliefFilteredTimelineObservation` maps post authors back to Echo agents.
3. It filters visible posts by the configured belief-distance policy:
   - `similarity`: keep posts from authors within the threshold.
   - `opposite`: keep posts from authors at or above the threshold.
   - `random`: keep all posts, then shuffle before truncating.
4. The agent sees a normal timeline containing real post IDs.

Beliefs are measured once at the end of each action window:

1. `FixedActionsThenBeliefProbePolicy` runs the configured number of normal
   social-media actions.
2. `EchoSocialToolResolve` executes those tool calls against the backend without
   probing belief after each action.
3. After the action window, the policy sends the active agent one private
   `CHOICE` action spec tagged `echo_belief_probe`.
4. The Echo social agent treats the raw observations since the previous probe as
   short-term memory, updates long-term memory once, and reports one of
   `-2, -1, 0, 1, 2`.
5. The policy passes that belief report back to the GM, and the app-owned
   `EchoChamberWorld` records it for metrics.

The GM does not judge or infer belief.  It only resolves social actions and
records the terminal belief report routed back through resolve.

Loose-social permits multiple actions per active agent by using the custom
engine turn policy:

- `engine.turn_policy.class_path=replications.echo_chambers.components.turn_policy.FixedActionsThenBeliefProbePolicy`
- default `count=5`

That gives each active agent up to five separate post/reply/repost decisions in
a day, followed by one long-memory update and one belief choice.  The app
expects one belief event per agent per day.

Config entrypoints:

- Scenario: `world=loose_social`
- Concordia social tool agent: `agents=loose_social`
- Simple default social agent: `agents=simple_social`
- Environment variant: `conf/env/loose_social.yaml`
- Engine variant: `conf/sim/loose_social.yaml`

Example dry run:

```bash
cd /home/sneheel/mastodon-sim
uv run silisocs-study \
  --study replications/echo_chambers/study.yaml \
  run \
  --only-hypothesis h3_loose_action_structure \
  --only-condition loose_social_similarity_5seed \
  --max-concurrent 2 \
  --dry-run
```
