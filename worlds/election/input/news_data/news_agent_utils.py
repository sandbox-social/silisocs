import json
import os

import openai
from openai import OpenAI


def transform_news_headline_for_sim(headlines, batch_size=5):
    # Define simulation description and related content
    simulation_description = (
        "[Description of simulation about resource scarcity and societal collapse]"
    )
    news_article_title = (
        "The State of Drought: How Water Scarcity is Shaping Policies in [Country Name]"
    )
    news_article_summary = (
        "Governments are faced with potentially dire consequences as regions suffer "
        "through prolonged drought, affecting millions of people."
    )

    system = (
        f"Act as a journalist mapping real-world news articles to the events or characteristics of a given simulation.\n\n"
        f"You will be provided with a description of a simulation, which includes its details, events, and scenarios. "
        f"Your task is to identify parallels between current real-world news stories and the events described in the simulation. "
        f"You should make connections that help readers understand how the simulation mirrors or differs from reality.\n\n"
        f"**Steps**\n\n"
        f"1. **Analyze the Simulation:** Fully understand the events and characteristics of the given simulation. "
        f"Take note of key elements such as themes, scenarios, and notable events.\n\n"
        f"2. **Understand the News Article:** Carefully read the provided news article. Identify the main points, themes, "
        f"and significant facts that could relate back to elements of the simulation.\n\n"
        f"3. **Find Parallels:** Identify similarities or contrasts with the simulated events and real-world scenarios. "
        f"Reflect on aspects such as outcomes, causes, and potential consequences. Make sure to establish both the "
        f"similarities and differences.\n\n"
        f"4. **Provide Reasoning:** In your conclusion, explain why the simulation is relevant to the news article. "
        f"Offer a thoughtful discussion of the potential insight or lessons learned from mapping the two together.\n\n"
        f"**Output Format**\n\n"
        f"- **Provide a brief summary of the news story.**\n"
        f"- **Indicate the specific simulation elements that relate to the news story.**\n"
        f"- **Explain how these elements parallel or differ from the real-world scenario.**\n"
        f"- **Use bullet points for distinct aspects to compare/contrast, followed by a concluding discussion.**\n\n"
        f"**Examples**\n\n"
        f'*Simulation Description:* "{simulation_description}"\n\n'
        f'*News Article Summary:* "{news_article_title}. {news_article_summary}"\n\n'
        f"*Mapping:*\n\n"
        f"- **Simulation Element:** In the simulation, resource scarcity leads to increased conflict among regions.\n"
        f"  - **News Parallel:** The current article discusses water scarcity leading to potential political crisis and tensions.\n\n"
        f"- **Simulation Element:** Simulated responses include rationing, local community support, and government intervention.\n"
        f"  - **News Parallel:** The news highlights initiatives for rationing and regional diplomacy as governments try to "
        f"mediate the effects of the drought.\n\n"
        f"*Conclusion:*\n\n"
        f"In comparing the simulation with the real-world context, it’s apparent that the challenges described in the model "
        f"materialize similarly in this drought scenario, providing insight into the potential consequences if resource "
        f"conflicts intensify.\n\n"
        f"**Notes**\n\n"
        f"- Be objective in presenting the connections.\n"
        f"- When identifying parallels, provide enough context so readers can understand specifics without having knowledge "
        f"of the simulation or news article in advance.\n"
        f"- Use accessible language that makes complex connections easy for readers to follow.\n"
    )

    # Define town history
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

    # Initialize prompt
    init_prompt = f"""
    Here is some information about the simulation environment.

    The town history: {town_history}

    There is an Election taking place in the town of Storhampton. Here is the information about the candidates:

    Information about candidates: {candidate_info}

    All mappings should be within the information provided about the simulation (including the election and candidates) and nothing else.
    I will give you news article headings and you should give me the corresponding mapped headings. I want a single news heading only.
    """

    all_mapped_headlines = []
    client = get_openai_client()
    total_headlines = len(headlines)  # function input
    batches = [headlines[i : i + batch_size] for i in range(0, total_headlines, batch_size)]

    not_possible_count = 0
    for batch_number, batch_headlines in enumerate(batches, start=1):
        batch_headlines_str = "\n".join(batch_headlines)
        prompt = f"{batch_headlines_str}, Map ALL headlines in the list. I want ONLY the corresponding mapped heading. Use NER (for example, replace Trump with the name of the conservative candidate). Do NOT print the original heading or anything else. DO NOT index the headlines with anything. If there is no relevant mapping, print [not possible]"
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": init_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=16183,
            )
        except openai.RateLimitError as e:
            print(f"An error occurred during the API call: {e}")
            continue
        summary = response.choices[0].message.content.strip()

        print(f"\n--- Mapped Headlines for Batch {batch_number} ---\n")
        print(summary)

        for line in summary.split("\n"):
            if "[not possible]" not in line:
                # remove the bullet point
                line = line.replace("- ", "")
                all_mapped_headlines.append(line)
            else:
                not_possible_count += 1

    return all_mapped_headlines, not_possible_count


def get_candidate_headlines(mapped_headlines):
    bill_headlines, bradley_headlines, other_headlines = [], [], []
    for headline in mapped_headlines:
        if ("bill" in headline.lower() or "fredrickson" in headline.lower()) and (
            "bradley" in headline.lower() or "carter" in headline.lower()
        ):
            # if both of them are in the headline, split the headline into two: one for bill and one for bradley
            if "bradley" in headline.lower():
                splitted_headline = headline.lower().split("bradley")
            else:
                splitted_headline = headline.lower().split("carter")

            bill_headlines.append(splitted_headline[0])
            bradley_headlines.append(splitted_headline[1])
        elif "bill" in headline.lower() or "fredrickson" in headline.lower():
            bill_headlines.append(headline)
        elif "bradley" in headline.lower() or "carter" in headline.lower():
            bradley_headlines.append(headline)
        elif headline != "":
            other_headlines.append(headline)

    return bill_headlines, bradley_headlines, other_headlines


def save_json(data, path):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def get_openai_client():
    key = os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=key)
