import json

# Read data from an external JSON file
input_file = "/Users/gayatrikrishnakumar/Desktop/World_Adapter/Persona Generation/posts_byuser.json"  # Replace with your file path
output_file = "formatted_comments.json"  # Replace with your desired output file path

# Load the data from the file
with open(input_file) as file:
    data = json.load(file)

# Grouping titles by author_id
formatted_data = {}
for entry in data:
    author_id = entry["author_id"]
    if author_id not in formatted_data:
        formatted_data[author_id] = {"author_id": author_id, "titles": []}
    formatted_data[author_id]["titles"].append(entry["title"])

# Converting the result into a list of objects
result = list(formatted_data.values())

# Save the result into another JSON file
with open(output_file, "w") as file:
    json.dump(result, file, indent=4)

print(f"Formatted data has been saved to {output_file}")
