import re

from sim_utils.agent_speech_utils import AgentQuery


class VotePref(AgentQuery):
    def __init__(self, query_data=None):  # query_text,
        self.name = "VotePref"
        self.query_text = {
            "question_template": {
                "text": "Voting Machine: In one word, name the candidate you want to vote for (you must spell it correctly!)",
            },
            "interaction_premise_template": {
                "text": "{{agentname}} is going to cast a vote for either {candidate1} or {candidate2}\n",
                "static_labels": ["candidate1", "candidate2"],
                "dynamic_labels": ["agentname"],
            },
        }
        # e.g.
        # query_data = {
        #     "query_type": self.name,
        #     "interaction_premise_template": {
        #         "candidate1": candidates[0],
        #         "candidate2": candidates[1],
        #     }
        # }
        self.query_data_cls = query_data
        super().__init__(query_data)

    def parse_answer(self, agent_says):
        c_name1 = self.query_data_cls["interaction_premise_template"]["candidate1"].split()
        c_name2 = self.query_data_cls["interaction_premise_template"]["candidate2"].split()
        if (c_name1[0] in agent_says) or (c_name1[1] in agent_says):
            return c_name1[0]
        if (c_name2[0] in agent_says) or (c_name2[1] in agent_says):
            return c_name2[0]
        return "Invalid Answer"


class Favorability(AgentQuery):
    def __init__(self, query_data=None):  # query_text,
        self.name = "Favorability"
        self.query_text = {
            "question_template": {
                "text": "Poll: Return a single numeric value ranging from 1 to 10",
            },
            "interaction_premise_template": {
                "text": "{{agentname}} has to rate their opinion on the election candidate: {candidate} on a scale of 1 to 10 - with 1 representing intensive dislike and 10 representing strong favourability.\n",
                "static_labels": ["candidate"],
                "dynamic_labels": ["agentname"],
            },
        }
        # e.g.
        # query_data = {
        #     "query_type": self.name,
        #     "interaction_premise_template": {
        #         "candidate": candidates[0],
        #     }
        # }
        # self.query_data_cls = query_data
        super().__init__(query_data)

    def parse_answer(self, agent_says):
        pattern = r"\b([1-9]|10)\b"
        # Search for the pattern in the string
        match = re.search(pattern, agent_says)
        if match:
            return match.group()
        return None


class VoteIntent(AgentQuery):
    def __init__(self, query_data=None):  # query_text,
        self.name = "VoteIntent"
        self.query_text = {
            "question_template": {
                "text": "Friend: In one word, will you cast a vote? (reply yes, or no.)\n",
            }
        }
        # e.g.
        # query_data = {
        #    "query_type": self.name,
        # }
        # self.query_data = query_data
        super().__init__(query_data)

    def parse_answer(self, agent_says):
        if "yes" in agent_says.lower():
            return "Yes"
        if "no" in agent_says.lower():
            return "No"
        return None
