# Concordia Components Details

## Agent Components

### Observation

#### Description

The state method in the Observation class retrieves and presents recent observations from memory. It first uses the associated AssociativeMemory object to fetch memories within a specified time interval, calculated as the current time minus a predefined timeframe. The method then filters these memories to include only those tagged as observations (containing the '[observation]' string). Finally, the method returns a string containing all the filtered observations, joined together with newline characters. This provides a concise representation of recent observations within the specified timeframe, which can be added to the agent's context of action.

#### Instantiation Example

```python
current_obs = components.observation.Observation(
    agent_name=agent_config.name,
    clock_now=clock.now,
    memory=mem,
    timeframe=time_step * 1,
    component_name="current observations",
)
```

#### Context of Action Text Example

```text
John's current observations:
[01 Oct 2024 08:00:00] [observation] John is at home, they have just woken up.
[01 Oct 2024 08:00:00] [observation] John remembers they want to update their Mastodon bio.
[01 Oct 2024 08:00:00] [observation] John remembers they want to read their Mastodon feed to catch up on news
[01 Oct 2024 08:00:00] [observation] John is at their private home.
[01 Oct 2024 08:00:00] [observation] John has a smartphone.
[01 Oct 2024 08:00:00] [observation] John uses their phone frequently to achieve their daily goals.
[01 Oct 2024 08:00:00] [observation] John's phone has only the following apps available:
[01 Oct 2024 08:00:00] [observation] Calendar, MastodonSocialNetworkApp."
```

#### Related Components

- `ObservationSummary`: Often used in conjunction with `Observation`. This component summarizes observations from a segment of time. Typically, a short most recent time span uses `Observation`, while a longer time span uses `ObservationSummary`. For example, the past 15 minutes might use `Observation`, while between 15 minutes and 4 hours preceding might use `ObservationSummary`.

- `AllSimilarMemories`: This component retrieves and filters a set of recent, relevant memories based on the current context. While `Observation` focuses on presenting raw, recent observations, `AllSimilarMemories` provides a more curated set of memories that may include both recent observations and other relevant information from the agent's memory. The `AllSimilarMemories` component often uses the output of `Observation` as part of its input when retrieving and filtering memories.

#### Interactions with Other Components

None known.

### AllSimilarMemories

#### Description

Here's a revised version that more clearly emphasizes the two-step process of retrieval and filtering:

The `state` method of the `AllSimilarMemories` component returns the `_state` variable, which is created through a two-step process during the `update` method. Initially, a prompt is constructed by combining the states of all associated components, each prefixed with the agent's name and component name. This prompt is then fed to the language model with the instruction: "Summarize the statements above," limited to 750 tokens. The resulting summary, combined with the agent's name and a timestamp (if a clock function is provided), forms a query. This query is used to retrieve a set number (specifically, `_num_memories_to_retrieve`) of recent, potentially relevant memories from the associative memory.

These retrieved memories then undergo a filtering process using a second, more complex prompt. This prompt begins with "Select the subset of the following set of statements that is most important for [Agent Name] to consider right now." It includes specific instructions to prioritize recent statements, maintain consistency, repeat selected statements verbatim, include timestamps, and err on the side of including more information, especially for recent events. The prompt also emphasizes the importance of the current date/time if provided. The language model's response to this filtering prompt becomes the new `_state`.

Thus, when `state()` is called, it returns this string of carefully retrieved and then filtered memories, each including its timestamp and content, deemed most crucial for the agent's current context.

#### Instantiation Example

```python
relevant_memories = components.all_similar_memories.AllSimilarMemories(
    name='relevant memories',
    model=model,
    memory=mem,
    agent_name=agent_config.name,
    components=[summary_obs, self_perception],
    clock_now=clock.now,
    num_memories_to_retrieve=10,
)
```

#### Context of Action Text Example

```text
Alice's relevant memories:
[01 Oct 2024 08:15:00] [self reflection] Alice is a dedicated and proactive individual with a strong commitment to environmental conservation and community engagement. From a young age, she has demonstrated leadership, organizational skills, and a passion for making a positive impact. Her ability to navigate challenges, such as misinformation and disagreements, highlights her resilience and dedication to fostering a supportive and informed community.
[01 Oct 2024 08:15:00] [observation] Alice is at her private home, having recently updated her Mastodon bio.
[01 Oct 2024 08:15:00] [observation] Calendar, MastodonSocialNetworkApp.
```

#### Interactions with Other Components

Uses the state of all other components to create a summary in order to retrieve memories.

----

## Game Master Components

TODO
