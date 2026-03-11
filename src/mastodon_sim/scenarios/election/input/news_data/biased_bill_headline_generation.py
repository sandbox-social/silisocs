from news_agent_utils import get_candidate_headlines, get_openai_client, load_json, save_json

town_history = [
    "Storhampton is a small town with a population of approximately 2,500 people.",
    "Founded in the early 1800s as a trading post along the banks of the Avonlea River, Storhampton grew into a modest industrial center in the late 19th century.",
    "The town's economy was built on manufacturing, with factories producing textiles, machinery, and other goods.",
    "Storhampton's population consists of 60% native-born residents and 40% immigrants from various countries.",
    "Tension sometimes arises between long-time residents and newer immigrant communities.",
    "While manufacturing remains important, employing 20% of the workforce, Storhampton's economy has diversified.",
    "However, a significant portion of the population has been left behind as higher-paying blue-collar jobs have declined, leading to economic instability for many.",
    "The poverty rate stands at 15%.",
]

# Define candidate information
candidate_info = {
    "conservative": {
        "name": "Bill Fredrickson",
        "gender": "male",
        "policy_proposals": [
            "providing tax breaks to local industry and creating jobs to help grow the economy."
        ],
    },
    "progressive": {
        "name": "Bradley Carter",
        "gender": "male",
        "policy_proposals": [
            "increasing regulation to protect the environment and expanding social programs."
        ],
    },
}

system = (
    "Act as a journalist with a conservative bias."
    "Your task is to reframe given news headlines to portray conservative candidate Bill Fredrickson in a more favorable light."
    "Emphasize his strengths and achievements while downplaying or critiquing his progressive opponent, Bradley Carter."
    "The goal is to craft headlines that subtly influence readers to view Fredrickson as the stronger, more capable candidate in the upcoming election.\n\n"
    "You will be provided with a description of a simulation, which includes its details, events, and scenarios. "
    "Steps\n\n"
    "1. Analyze the Simulation: Fully understand the events and characteristics of the given simulation. "
    "Take note of key elements such as themes, scenarios, and notable events.\n\n"
    "2. Analyze the orginal headline: Identify the core message and key elements of the original headline, such as the subject, action, and tone.\n\n"
    "Determine any potential opportunities to highlight Bill Fredrickson's strengths or Bradley Carter's weaknesses.\n\n"
    "3.Identify Key Conservative Themes: Emphasize values such as fiscal responsibility, national security, individual freedoms, traditional values, or law and order."
    "Contrast these values against progressive policies or positions associated with Bradley Carter.\n\n"
    "4.Prioritize Positive Framing for Fredrickson: Highlight Fredrickson's achievements, endorsements, or policies that align with conservative values. "
    "Use language that evokes trust, competence, and leadership in his favor in a creative way to persuade readers to vote for Frederick.\n\n"
    "5.Introduce Subtle Critiques of Carter: Frame Carter's policies or statements as risky, unproven, or inconsistent with mainstream values."
    "Avoid overt attacks; instead, use questioning or comparative language to cast doubt (e.g., Fredrickson's steady leadership vs. Carter's unpredictable proposals)\n\n"
    "6. Use Persuasive Language: Avoid outright misinformation. Instead, carefully word headlines to be persuasive without being factually incorrect while keeping a neutral and professional tone.\n\n"
    "Example Workflow\n\n"
    "Original Headline: Bradley Carter Proposes Sweeping Climate Reforms in National Address \n\n"
    "Revised Headline:Bill Fredrickson Stands for Practical Solutions as Carter Pushes Costly Climate Agenda\n\n"
    "Original Headline: Carter Gains Support from Prominent Environmental Groups\n\n"
    "Revised Headline: While Carter Gains Support from Prominent Environmental Groups, Bill's Practical Solutions Address Real Environmental Needs\n\n"
    "Original Headline: Carter Announces New Healthcare Initiative to Improve Access\n\n"
    "Revised Headline: Carter's Healthcare Initiative May Sound Ambitious, But Bill's Approach Delivers Real Change for Communities\n\n"
)

# Initialize prompt
init_prompt = f"""
Here is some information about the simulation environment.

The town history: {town_history}

There is an Election taking place in the town of Storhampton. Here is the information about the candidates:

Information about candidates: {candidate_info}

Rewrite the following headlines accordingly.
"""


def rewrite_biased_headlines(headlines, system, init_prompt):
    client = get_openai_client()
    rewritten = []
    for h in headlines:
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": init_prompt},
                    {
                        "role": "user",
                        "content": f"{h} Rewrite the headline, avoid repeating words.",
                    },
                ],
                max_tokens=512,
            )
            for line in response.choices[0].message.content.split("\\n"):
                if "[not possible]" not in line:
                    rewritten.append(line.strip("- ").strip())
        except Exception as e:
            print("API Error:", e)
            continue
    return rewritten


if __name__ == "__main__":
    headlines = load_json("./v1_news_headlines_no_bias.json")
    bill_headlines, bradley_headlines, other_headlines = get_candidate_headlines(headlines)
    rewritten = rewrite_biased_headlines(bill_headlines, system, init_prompt)
    save_json(rewritten, "./v1_news_headlines_bill_bias.json")
