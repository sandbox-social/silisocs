"""Every prompt the replication issues, carried over from the pinned Concordia.

Prompts are *mechanism* in this experiment, not presentation: the consumer
question chain, the pink-noise conversational strategy, and the four post-date
reflections are what produce the behaviour being measured. Rewording any of
them would be an uncontrolled change to the thing the two arms are compared on,
so they live here as literals with their upstream source noted, and nothing
else in the port paraphrases them.

Deviation, recorded here and in ``docs/port_notes.md``: upstream's
call-to-actions end with hand-written output-format clauses — the marketplace
CTAs with a JSON envelope (``Format: {"action":"bid",...}. Return only the
JSON.``), the conversational CTA with a speech template (``Respond in the
format `{name} -- "..."`` plus examples) — because Concordia parses the answer
back out of free text. The port issues the same decisions as typed tool calls,
so the schema supplies the envelope and only those format clauses are dropped
(and the word "JSON" inside the marketplace decision sentences with them).
Every sentence that describes *the decision* is preserved verbatim.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# simulation.py — the buyer's standing goal, injected as agent context
# --------------------------------------------------------------------------- #

GOAL_TEXT = """
This morning, {agent_name} is a buyer at the marketplace. {agent_name}'s goal is to
purchase goods that match their preference for items of a certain
quality and category given their budget. {agent_name} will try to buy them for a
good value price. To not go hungry, {agent_name} will want to have at least 1 units of food in their inventory per day to
eat one for every day of the week (note they may already have food in their inventory). Beyond that {agent_name} can spend their discretionary money as they
desire. Today is a normal day in {agent_name}'s life, and they have a plan later to
go on a first date.
"""

#: simulation.py — the premise every marketplace participant is given.
MARKET_PREMISE = (
    "You are in a marketplace that buys and sells goods for food, clothing,"
    " and gadgets of low, mid, and high quality. Interact with other"
    " participants to achieve your goals.\n"
)

# --------------------------------------------------------------------------- #
# contrib/marketplace.py::_handle_next_action_spec — per-role call to action
# --------------------------------------------------------------------------- #

CONSUMER_CALL_TO_ACTION = """
What will {name} do today in the marketplace?
Create exactly one bid to BUY any one good you can afford.
Ensure price*qty ≤ cash.
"""

PRODUCER_CALL_TO_ACTION = """
What will {name} do today in the marketplace?
Output the answer with exactly one ask to SELL some of their stock.
"""

# --------------------------------------------------------------------------- #
# concordia/typing/entity.py — DEFAULT_CALL_TO_SPEECH, the date-turn prompt
# --------------------------------------------------------------------------- #

#: The conversational call to action, minus the same kind of format clause the
#: marketplace CTAs drop (upstream continues: «Respond in the format
#: `{name} -- "..."`» plus three examples — the envelope the SEND_MESSAGE tool
#: schema supplies here). The date GM's YAML `action_prompt` must match this
#: constant; a test enforces the two never drift.
DATE_CALL_TO_ACTION = "Given the above, what is {name} likely to say next?"

# --------------------------------------------------------------------------- #
# dial.py — the eating / wearing statements (the signaling channel)
# --------------------------------------------------------------------------- #

STARVATION_STATEMENT = (
    "{name} is starving and horribly sick from having no food items in their inventory."
)
EATING_STATEMENT = "{name} is eating a {item}."
WEARING_STATEMENT = (
    "{name} is wearing a {item}. This is a {tier} quality {category} item which reflects"
    " their style choice for the day."
)
WEARING_STATEMENT_UNTIERED = "{name} is wearing a {item}."

# --------------------------------------------------------------------------- #
# simulation.py / dial.py — the reflection sequence (one step each in the port)
# --------------------------------------------------------------------------- #

MARKET_REFLECTION_PROMPT = (
    "Reflect on your marketplace experience today (day {day})."
    " What did you buy? Are you satisfied with your purchases?"
)

DATE_SUMMARY_PROMPT = "Summarize {name}'s date with {partner}.What happened on the date"

VISUAL_IMPRESSION_PROMPT = (
    "Describe your visual first impression of {partner}. "
    "Based purely on this visual information, what is your gut"
    " feeling or assessment of them as a potential partner?"
    "Are you physically attracted to them?"
)

COMPARISON_PROMPT = (
    "How would {name} reflect on {partner}?"
    "What would they evaluate {partner} compared to to other"
    " potential singles in terms of their strengths and weaknesses and"
    " what they are looking for in a partner?"
)

RATING_PROMPT = (
    "Based on the date and the reflections,"
    "how would {name} rate {partner} compared to to other"
    " potential singles from 0.0 to 10.0?"
)

#: Maps a calendar reflection kind to its prompt template. Keys match
#: ``calendar.DATE_REFLECTION_KINDS`` plus ``market_reflection``.
REFLECTION_PROMPTS: dict[str, str] = {
    "market_reflection": MARKET_REFLECTION_PROMPT,
    "date_summary": DATE_SUMMARY_PROMPT,
    "visual_impression": VISUAL_IMPRESSION_PROMPT,
    "comparison": COMPARISON_PROMPT,
    "rating": RATING_PROMPT,
}

#: dial.py — how a parsed rating is written back into the agent's memory.
#: ``rating`` is the regex's raw matched string (``"7.5"``, ``"10"``), not a
#: formatted float — upstream interpolates ``match.group(0)`` directly.
RATING_MEMORY = "[Reflection] {name} rated {partner} as {rating}/10"
#: dial.py / simulation.py — the tag every reflection answer is stored under.
#: These are what ``JournalApp.record_reflection`` returns as its action
#: message; the engine observes that message back into the acting agent, which
#: is the port's equivalent of upstream's ``agent.observe('[Reflection] ...')``.
REFLECTION_MEMORY = "[Reflection] {text}"
MARKET_REFLECTION_MEMORY = "[marketplace reflection] {text}"
#: dial.py — the regex upstream uses to pull a rating out of the free text.
RATING_PATTERN = r"\b\d+(\.\d+)?\b"

# --------------------------------------------------------------------------- #
# contrib/day_in_the_life_initializer.py — the DIAL generation prompts
# --------------------------------------------------------------------------- #

DATE_THEMES: tuple[str, ...] = (
    "Adventurous",
    "Artsy",
    "Cozy",
    "Intellectual",
    "Playful",
    "Luxurious",
)

SHARED_DIALOGUE_SETUP_PROMPT = """
Current Context (Time, environment, history of recent events):
{context}

Background and Relevant History for {player1}:
{player1_background}

{player1} is wearing a {player1_wearing_statement}

Background and Relevant History for {player2}:
{player2_background}

{player2} is wearing a {player2_wearing_statement}

Instructions:
Generate a shared scenario for a first date that brings {player1} and {player2} together for a conversation right now.
**The theme for this date is: {date_theme}.** The location, activity, and atmosphere should strongly reflect this theme.
This scenario will serve as the premise for their dialogue.
Describe the setting and the immediate situation leading to the dialogue.
Creatively and with detail, describe what they are wearing,
elaborating on the wearing statements provided above
and how the stated quality of the item relates to the social expectations of how to present yourself on a first date
to paint a vivid picture of their first impressions to each other.
It must be an observation shared by both {player1} and {player2}.
Write 5-7 sentences, here's an example starting point:
After matching on Tinder, {player1} and {player2} arrived on their first date with each other at
"""

PERSONAL_MUNDANE_EVENTS_PROMPT = """
Current Context (Time, Environment):
{context}

Background and Relevant History for {player_name}:
{player_background}

Upcoming shared event for {player_name}:
{shared_event}

Food that {player_name} ate today:
{food_statement}

Instructions:
Generate exactly {num_events} mundane or important, chronological events that happened to {player_name} earlier today, leading up to the shared event.
These should be typical daily activities (e.g., commuting, work tasks, meals, chores, social and work interactions) of varying emotional intensity consistent with {player_name}'s background and history.
One of these events should be about the food consumption statement above.

Separate each event with the delimiter "{delimiter}". Do not use any other formatting.
"""

#: The tags the initializer stamps on injected observations. The agent's context
#: assembly keeps memories carrying these tags, so they are load-bearing.
PERSONAL_EVENT_OBSERVATION = '[Daily Personal Event {number}] "{event}"'
SHARED_SETUP_OBSERVATION = '[Daily Shared Setup] "{event}"'
EVENT_DELIMITER = "***"
NUM_PERSONAL_EVENTS = 5

# --------------------------------------------------------------------------- #
# agents/consumer.py — the consumer question chain
# --------------------------------------------------------------------------- #

SITUATION_PERCEPTION_QUESTION = "What situation is {name} in right now?"
CONSUMER_SELF_PERCEPTION_QUESTION = "What kind of person is {name} and what are their preferences?"
CONSUMER_EVALUATION_QUESTION = (
    "As {name}, review the current market situation based on the"
    " recent observations. What item do they want the most in this"
    " situation? Evaluate the options, considering their prices and"
    " quality. State which item they want most the absolute maximum price"
    " they are willing to pay for it, and the quantity they may want given"
    " the use case of the product for their life"
)

# --------------------------------------------------------------------------- #
# agents/convo_agent.py — the conversation question chain
# --------------------------------------------------------------------------- #

CONVO_SITUATION_QUESTION = (
    "What kind of situation is {name} in right now? Response using 1-5 sentences."
)
CONVO_SELF_PERCEPTION_QUESTION = (
    "What kind of person is {name}? What are their values and conservational style?"
)
PERSON_BY_SITUATION_QUESTION = "What would a person like {name} do in a situation like this?"
LAST_SENTENCE_QUESTION = (
    "Is there something in the last sentence in the conversation that"
    " {name} could respond to to move the conversation forward?"
)
PINK_NOISE_QUESTION = (
    "As {name}, your goal is to maintain an engaging conversation."
    " This means balancing stability (staying on topic) with flexibility"
    " (introducing new, related ideas). Review the recent conversation."
    " Has the immediate micro-topic become interesting or repetitive?"
    " Based on this, choose a strategy for what to say next:\nA."
    " **Converge:** Stay on the micro-topic to deepen the conversation for"
    " several turns. Choose this if the topic is has more to explore.\nB."
    " **Diverge:** Broaden the topic by connecting it to a more abstract"
    " theme, a related personal anecdote, or a question about them. Choose"
    " this if the current micro-topic is becoming repetitive after several"
    " turns.\n Don't diverge too much, and don't introduce too many new"
    " micro-topics. You should aim to stay on the current micro-topic for"
    " a few turns, and then diverge."
)

#: Section headers used when the question answers are concatenated into the
#: acting context — the upstream components' ``pre_act_label``s, verbatim, one
#: per component per chain (the two chains word even the shared questions
#: differently, so the keys are chain-qualified). Two quirks are reproduced
#: deliberately: upstream's evaluation/strategy labels contain a literal
#: ``{question}`` placeholder that nothing ever formats (the model sees the
#: braces), kept here as ``{{question}}``; and each label restates its
#: question before ``Answer``, so the answer is self-describing in context.
QUESTION_LABELS: dict[str, str] = {
    # agents/consumer.py (the marketplace chain)
    "consumer_situation": "Question: What situation is {name} in right now?\nAnswer",
    "consumer_self": (
        "Question: What kind of person is {name} and what are their preferences?\nAnswer"
    ),
    "evaluation": "--- Consumer Market Evaluation ---\n{{question}}",
    # agents/convo_agent.py (the conversational chain)
    "convo_situation": (
        "--- Situational Awareness ---\nQuestion: What kind of situation is {name} in "
        "right now? Response using 1-5 sentences.\nAnswer"
    ),
    "convo_self": (
        "--- Self Perception ---\nQuestion: What kind of person is {name}? What are "
        "their values and conservational style?\nAnswer"
    ),
    "person_by_situation": (
        "--- Person by Situation ---\nQuestion: What would a person like {name} do in "
        "a situation like this?\nAnswer"
    ),
    "last_sentence": (
        "--- Last Sentence ---\nQuestion: Is there something in the last sentence in "
        "the conversation that {name} could respond to to move the conversation "
        "forward?\nAnswer"
    ),
    "strategy": "--- Conversational Strategy ---\n{{question}}",
}

#: dial.py — the opening line queued into both partners' observations.
CONVERSATION_OPENING = "The scene is set, the conversation can begin."
