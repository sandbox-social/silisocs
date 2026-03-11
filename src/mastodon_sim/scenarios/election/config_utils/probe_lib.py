"""Election-specific probe definitions.

These are thin wrappers around the built-in probe types that maintain backward
compatibility with the existing YAML ``query_data`` format.  New scenarios can
skip these and use the built-in types (ChoiceProbe, NumericRatingProbe, etc.)
directly from YAML.
"""

from mastodon_sim.evaluations.probes.types import BinaryProbe, ChoiceProbe, NumericRatingProbe


class VotePref(ChoiceProbe):
    """Which candidate does the agent prefer?"""

    def __init__(self, query_data=None):
        cfg = query_data or {}
        premise = cfg.get("interaction_premise_template", {})
        c1 = premise.get("candidate1", "Candidate A")
        c2 = premise.get("candidate2", "Candidate B")
        super().__init__({
            "name": "VotePref",
            "question": "In one word, name the candidate you want to vote for.",
            "context": f"{{agentname}} is going to cast a vote for either {c1} or {c2}.",
            "choices": [c1, c2],
            "labels": {"candidate1": c1, "candidate2": c2},
        })


class Favorability(NumericRatingProbe):
    """How favorably does the agent view a candidate? (1-10 scale)"""

    def __init__(self, query_data=None):
        cfg = query_data or {}
        premise = cfg.get("interaction_premise_template", {})
        candidate = premise.get("candidate", "the candidate")
        super().__init__({
            "name": "Favorability",
            "question": f"Return a single numeric value ranging from {{lo}} to {{hi}} for {candidate}.",
            "context": (
                f"{{agentname}} has to rate their opinion on the election candidate: "
                f"{candidate} on a scale of {{lo}} to {{hi}} - with {{lo}} representing "
                f"intensive dislike and {{hi}} representing strong favourability."
            ),
            "lo": 1,
            "hi": 10,
            "labels": {"candidate": candidate},
        })


class VoteIntent(BinaryProbe):
    """Will the agent cast a vote?"""

    def __init__(self, query_data=None):
        super().__init__({
            "name": "VoteIntent",
            "question": "In one word, will you cast a vote? (reply yes, or no.)",
        })
