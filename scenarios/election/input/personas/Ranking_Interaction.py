import json
import sys


def calculate_number_of_votes(score, upvote_ratio):
    # sourcery skip: assign-if-exp, reintroduce-else
    """
    Calculate the number of votes based on score and upvote ratio.
    Number of Votes = Score / (2 * Upvote Ratio - 1)
    """
    denominator = (2 * upvote_ratio) - 1
    if denominator <= 0:
        return None
    return score / denominator


def calculate_engagement_ratio(num_comments, number_of_votes):
    # sourcery skip: assign-if-exp, reintroduce-else
    """
    Calculate the engagement ratio.
    Engagement Ratio = Number of Comments / Number of Votes
    """
    if number_of_votes == 0:
        return None
    return num_comments / number_of_votes


def process_posts(data):
    processed = []
    for post in data:
        score = post.get("score", 0)
        upvote_ratio = post.get("upvote_ratio", 0)
        num_comments = post.get("num_comments", 0)

        number_of_votes = calculate_number_of_votes(score, upvote_ratio)

        if number_of_votes is None:
            continue

        engagement_ratio = calculate_engagement_ratio(num_comments, number_of_votes)

        if engagement_ratio is None:
            continue

        post["engagement_ratio"] = engagement_ratio
        processed.append(post)

    return processed


def main():
    input_file = "submissions.json"
    output_file = "sorted_data.json"

    try:
        with open(input_file) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: The file '{input_file}' was not found.")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: The file '{input_file}' is not valid JSON.")
        sys.exit(1)

    if not isinstance(data, list):
        print("Error: JSON data is not a list of posts.")
        sys.exit(1)

    processed_posts = process_posts(data)

    if not processed_posts:
        print("No valid posts to process.")
        sys.exit(0)

    sorted_posts = sorted(processed_posts, key=lambda x: x["engagement_ratio"], reverse=True)

    with open(output_file, "w") as f:
        json.dump(sorted_posts, f, indent=4)

    print(f"Successfully sorted posts by engagement ratio and saved to '{output_file}'.")
    print(f"Total processed posts: {len(processed_posts)}")
    print(f"Total skipped posts due to invalid data: {len(data) - len(processed_posts)}")


if __name__ == "__main__":
    main()
