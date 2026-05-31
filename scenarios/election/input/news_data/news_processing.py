import os

import requests
from news_agent_utils import get_openai_client, save_json

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


def fetch_news_headlines(api_key, query="environment sustainability climate"):
    url = f"https://newsapi.org/v2/everything?q={query}&language=en&sortBy=publishedAt&pageSize=100&apiKey={api_key}"
    response = requests.get(url)
    data = response.json()
    headlines = []
    if data.get("status") == "ok":
        for article in data.get("articles", []):
            title = article.get("title")
            if title and title != "[Removed]":
                clean_title = title.replace(" - ", " ")
                if clean_title not in headlines:
                    headlines.append(clean_title)
    return headlines


def transform_news_headline_for_sim(headlines, batch_size=1):
    system = (
        "Act as a journalist mapping real-world news articles to the events or characteristics of a given simulation.\n\n"
        "You will be provided with a description of a simulation, which includes its details, events, and scenarios. "
        "Your task is to identify parallels between current real-world news stories and the events described in the simulation. "
        "You should make connections that help readers understand how the simulation mirrors or differs from reality.\n\n"
        "**Steps**\n\n"
        "1. **Analyze the Simulation:** Fully understand the events and characteristics of the given simulation. "
        "Take note of key elements such as themes, scenarios, and notable events.\n\n"
        "2. **Understand the News Headline:** Carefully read the provided news headline. Identify the main points, themes, "
        "and significant facts that could relate back to elements of the simulation.\n\n"
        "3. **Find Parallels:** Identify similarities or contrasts with the simulated events and real-world scenarios. "
        "Reflect on aspects such as outcomes, causes, and potential consequences. Make sure to establish both the "
        "similarities and differences.\n\n"
        "4. **Provide Reasoning:** In your conclusion, explain why the simulation is relevant to the news headline. "
        "Offer a thoughtful discussion of the potential insight or lessons learned from mapping the two together.\n\n"
        "**Output Format**\n\n"
        "- **Provide a brief summary of the news story.**\n"
        "- **Indicate the specific simulation elements that relate to the news story.**\n"
        "- **Explain how these elements parallel or differ from the real-world scenario.**\n"
        "- **Use bullet points for distinct aspects to compare/contrast, followed by a concluding discussion.**\n\n"
        "**Notes**\n\n"
        "- Be objective in presenting the connections.\n"
        "- When identifying parallels, provide enough context so readers can understand specifics without having knowledge "
        "of the simulation or news article in advance.\n"
        "- Use accessible language that makes complex connections easy for readers to follow.\n"
        "-Ensure that the headline is not biased or opinionated."
    )

    # Initialize prompt
    init_prompt = f"""
    Here is some information about the simulation environment.

    The town history: {town_history}

    There is an Election taking place in the town of Storhampton. Here is the information about the candidates:

    Information about candidates: {candidate_info}

    All mappings should be within the information provided about the simulation (including the election and candidates) and nothing else.
    I will give you news article headings and you should give me the corresponding mapped headings. I want a single news heading only.
    """

    client = get_openai_client()
    all_mapped = []
    batches = [headlines[i : i + batch_size] for i in range(0, len(headlines), batch_size)]
    for batch in batches:
        prompt = (
            "\\n".join(batch)
            + "\\nMap ALL headlines. Use NER (e.g., replace Trump with Bill Fredrickson)."
        )
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": init_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
            )
            for line in response.choices[0].message.content.split("\\n"):
                if line.strip() and "[not possible]" not in line.lower():
                    all_mapped.append(line.strip("- ").strip())
        except Exception as e:
            print("API Error:", e)
            continue
    return all_mapped


if __name__ == "__main__":
    raw = fetch_news_headlines(api_key=os.getenv("NEWS_API_KEY"))
    mapped = transform_news_headline_for_sim(raw)
    save_json(mapped, "./v1_news_headlines_no_bias.json")
