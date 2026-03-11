import json


def extract_top_k(file_path, key, k, output_file):
    with open(file_path) as file:
        data = json.load(file)

    if key not in data[0]:
        print(f"The key '{key}' is not found in the JSON objects.")
        return

    sorted_data = sorted(data, key=lambda x: x[key], reverse=True)

    top_k_data = sorted_data[:k]

    with open(output_file, "w") as output:
        json.dump(top_k_data, output, indent=4)

    print(f"Top {k} JSON objects have been saved to '{output_file}'.")


file_path = "sorted_data.json"
key = "engagement_ratio"
k = 100
output_file = "top_k_data.json"


extract_top_k(file_path, key, k, output_file)
