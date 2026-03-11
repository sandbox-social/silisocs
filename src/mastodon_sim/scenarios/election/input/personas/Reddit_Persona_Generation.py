import json

from openai import OpenAI

OPENAI_API_KEY = ""


def gen_users(file_path, batch_size=5):
    system_message = (
        "You're a psychologist working in social and political sciences. "
        "You have to extract information about people based on interactions they have with others, "
        "and use it to design personas for a simulation."
    )

    with open(file_path) as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("Expected the JSON file to contain a list of interactions.")

    persona_descriptor = """
    {
        "Name": "Assign a realistic and appropriate name",
        "User_Reference": "Mention which user the persona is based on",
        "Sex": "Specify the sex/gender of the persona",
        "Political_Identity": "Describe the political orientation (e.g., liberal, conservative, moderate)",
        "Big5_traits": {
            "Openness": "Rate on a scale from 1 to 10",
            "Conscientiousness": "Rate on a scale from 1 to 10",
            "Extraversion": "Rate on a scale from 1 to 10",
            "Agreeableness": "Rate on a scale from 1 to 10",
            "Neuroticism": "Rate on a scale from 1 to 10"
        },
        "context": "Make observations about the user based on their interactions"
    }

    Assign scores thoughtfully: Use the scales provided to assign scores that accurately reflect the persona's characteristics based on the interactions.
    Consistency: Ensure that the scores for personality traits align logically with the behavioral indicators.
    Detail Orientation: Provide enough detail in each descriptor to make the persona realistic and relatable.
    Realistic Names: Give them realistic names and mention which user the persona is based on.
    Only generate the output json objects for each person and NOTHING else
    """

    client = OpenAI(api_key=OPENAI_API_KEY)

    total_items = len(data)
    batches = [data[i : i + batch_size] for i in range(0, total_items, batch_size)]

    # Open the output file once and append results after each batch
    with open("persona.txt", "w") as outfile:
        for batch_number, batch_data in enumerate(batches, start=1):
            batch_json = json.dumps(batch_data, ensure_ascii=False)
            prompt = (
                f"{batch_json}\n\n"
                "Based on these interactions, I want you to create personas with the following descriptors:\n"
                f"{persona_descriptor}\n\n"
                "IMPORTANT: Return ONLY the JSON objects for each person, nothing else. "
                "No additional text, explanations, or formatting. Only the JSON."
            )

            try:
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=1,
                    max_tokens=16383,
                    top_p=1,
                    frequency_penalty=0,
                    presence_penalty=0,
                )
            except openai.RateLimitError as e:
                print(f"Rate limit error on batch {batch_number}: {e}")
                continue
            except openai.APIError as e:
                print(f"API error on batch {batch_number}: {e}")
                continue

            summary = response.choices[0].message.content.strip()

            # Print raw output to inspect
            print(f"=== RAW OUTPUT FROM MODEL (Batch {batch_number}) ===")
            print(summary)
            print("========================================")

            # Write the raw output directly to the text file
            outfile.write(summary + "\n")

    print("All persona information has been written to persona.txt")


if __name__ == "__main__":
    gen_users("")  # path to formatted comments
