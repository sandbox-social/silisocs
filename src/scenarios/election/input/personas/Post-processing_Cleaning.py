import json
import re

with open("/Users/gayatrikrishnakumar/Desktop/World_Adapter/persona.txt") as f:
    raw_data = f.read()

    data = raw_data.replace("```json", "").replace("```", "")
    data = data.strip()

    if not data.startswith("["):
        data = f"[{data}"
    if not data.endswith("]"):
        data = f"{data}]"

    data = re.sub(r"}\s*{", "},\n{", data)

    data = re.sub(r",\s*,", ",", data)

    data = re.sub(r",\s*\]", "]", data)

    try:
        parsed = json.loads(data)

        with open("cleaned_personas.json", "w") as f:
            json.dump(parsed, f, indent=4, ensure_ascii=False)
        print("JSON cleaned and saved to cleaned_personas.json")
    except json.JSONDecodeError as e:
        print("Error parsing JSON:", e)

        print(data)
