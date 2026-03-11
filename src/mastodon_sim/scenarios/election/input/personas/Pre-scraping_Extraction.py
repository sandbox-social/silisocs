import json

# Load the JSON file
input_file = "/Users/gayatrikrishnakumar/Desktop/World_Adapter/Persona Generation/top_k_data.json"  # Replace with your input JSON file name
output_file = "author_names.txt"

try:
    # Read the JSON data
    with open(input_file) as file:
        data = json.load(file)

    # Extract author names
    author_names = [entry.get("author_name") for entry in data if entry.get("author_name")]

    # Write author names to a .txt file
    with open(output_file, "w") as file:
        file.write("\n".join(author_names))

    print(f"Author names successfully written to {output_file}")

except Exception as e:
    print(f"An error occurred: {e}")
