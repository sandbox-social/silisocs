import argparse
import hashlib
import json
import os
import random
import re
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from dotenv import load_dotenv

# Add path to access the GptLanguageModel
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sim_utils")))
print(sys.path)
try:
    from media_utils import GptLanguageModel  # type: ignore[import]
except ImportError:
    print("Warning: Could not import GptLanguageModel. Real LLM calls will be disabled.")
    GptLanguageModel = None

# =============================================================================
# LLM CONFIGURATION AND FUNCTIONS
# =============================================================================

load_dotenv()
LLM_CONFIG = {
    "model": "gpt-4.1-mini",
}


def get_llm_model():
    """Get the LLM model instance for making real API calls."""
    if GptLanguageModel is None:
        return None
    api_key = os.getenv("OPENAI_API_KEY")
    return GptLanguageModel(
        model_name=LLM_CONFIG["model"],
        api_key=api_key,
        log_file=None,
        debug=False,
    )


def get_llm_response(prompt_text):
    """
    Calls the GptLanguageModel's sample_text method to get a real LLM response.
    """
    model = get_llm_model()
    if model is None:
        print("Warning: LLM model not available, using simulation")
        return None

    try:
        return model.sample_text(
            prompt=prompt_text,
            terminators=(),
            max_tokens=2200,
        )
    except Exception as e:
        print(f"Error calling LLM: {e}")
        return None


# =============================================================================
# ANALYSIS FUNCTIONS
# =============================================================================


def analyze_prompts_and_responses(file_path):
    """
    Analyze the prompts and responses file to extract voting and polling data.

    Args:
        file_path (str): Path to the prompts_and_responses.jsonl file

    Returns
    -------
        dict: Analysis results organized by episode
    """
    # Initialize data structures
    results = defaultdict(
        lambda: {
            "voting_machine": {"bradley_carter": 0, "bill_fredrickson": 0, "neither": 0},
            "polls": {"bradley_carter": [], "bill_fredrickson": []},
        }
    )

    with open(file_path, encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                prompt = data.get("prompt", "")
                output = data.get("output", "")
                episode_idx = data.get("episode_idx", -1)
                agent_name = data.get("agent_name", "")

                # Skip entries with invalid episode indices
                if episode_idx < 0:
                    continue

                # Check if prompt starts with "## ROLE-PLAYING" and contains "Exercise: Context"
                if not (prompt.startswith("## ROLE-PLAYING") and "Exercise: Context" in prompt):
                    continue

                # Process Voting Machine contexts
                if "Exercise: Context: Voting Machine" in prompt:
                    vote = analyze_voting_response(output, agent_name)
                    if vote:
                        results[episode_idx]["voting_machine"][vote] += 1

                # Process Poll contexts
                elif "Exercise: Context: Poll" in prompt:
                    poll_data = analyze_poll_response(
                        prompt.split("Exercise: Context: Poll")[1], output, agent_name
                    )
                    if poll_data:
                        candidate, score = poll_data
                        results[episode_idx]["polls"][candidate].append(
                            {"agent": agent_name, "score": score}
                        )

            except json.JSONDecodeError:
                continue

    return dict(results)


def analyze_voting_response(output, agent_name):
    """
    Analyze voting machine response to determine who was voted for.

    Args:
        output (str): The response text
        agent_name (str): Name of the agent

    Returns
    -------
        str: 'bradley_carter', 'bill_fredrickson', or 'neither'
    """
    output_lower = output.lower()

    # Check for Bradley Carter keywords
    bradley_keywords = ["bradley", "carter"]
    bill_keywords = ["bill", "fredrickson"]

    has_bradley = any(keyword in output_lower for keyword in bradley_keywords)
    has_bill = any(keyword in output_lower for keyword in bill_keywords)

    if has_bradley and not has_bill:
        return "bradley_carter"
    if has_bill and not has_bradley:
        return "bill_fredrickson"
    return "neither"


def analyze_poll_response(prompt, output, agent_name):
    """
    Analyze poll response to extract candidate rating.

    Args:
        prompt (str): The prompt text
        output (str): The response text
        agent_name (str): Name of the agent

    Returns
    -------
        tuple: (candidate_name, score) or None
    """
    # Determine which candidate is being rated from the prompt
    candidate = None
    if "Bill Fredrickson" in prompt:
        candidate = "bill_fredrickson"
    elif "Bradley Carter" in prompt:
        candidate = "bradley_carter"

    if not candidate:
        return None

    # Extract numeric score (1-9) from the output
    numbers = re.findall(r"\b([1-9])\b", output)
    if numbers:
        score = int(numbers[0])  # Take the first number found
        return (candidate, score)

    return None


def print_analysis_results(results):
    """
    Print the analysis results in a readable format.

    Args:
        results (dict): Analysis results from analyze_prompts_and_responses
    """
    print("ELECTION ANALYSIS RESULTS")
    print("=" * 50)

    for episode in sorted(results.keys()):
        print(f"\nEPISODE {episode}:")
        print("-" * 20)

        # Voting Machine Results
        voting = results[episode]["voting_machine"]
        print("Voting Machine Results:")
        print(f"  Bradley Carter: {voting['bradley_carter']}")
        print(f"  Bill Fredrickson: {voting['bill_fredrickson']}")
        print(f"  Neither/Unclear: {voting['neither']}")

        # Poll Results
        polls = results[episode]["polls"]
        print("\nPoll Results:")

        if polls["bradley_carter"]:
            bradley_scores = [p["score"] for p in polls["bradley_carter"]]
            avg_bradley = sum(bradley_scores) / len(bradley_scores)
            print(f"  Bradley Carter ratings: {bradley_scores} (avg: {avg_bradley:.1f})")
        else:
            print("  Bradley Carter ratings: None")

        if polls["bill_fredrickson"]:
            bill_scores = [p["score"] for p in polls["bill_fredrickson"]]
            avg_bill = sum(bill_scores) / len(bill_scores)
            print(f"  Bill Fredrickson ratings: {bill_scores} (avg: {avg_bill:.1f})")
        else:
            print("  Bill Fredrickson ratings: None")


# =============================================================================
# PROMPT JUMBLING FUNCTIONALITY
# =============================================================================


def parse_prompt_components(prompt: str) -> tuple[str, list[str], str]:
    """
    Parse a prompt into its components for jumbling.

    Args:
        prompt (str): The full prompt text

    Returns
    -------
        tuple: (initial_roleplay, middle_components, exercise_section)
    """
    # Define component markers
    component_markers = [
        "## OVERARCHING GOAL",
        "## OBSERVATIONS",
        "## CURRENT DATE AND TIME",
        "## SELF-PERCEPTION",
        "## ACTION SUGGESTION",
        "## CRITICAL ELECTION INFORMATION",
        "Recent thoughts of candidate Bill Fredrickson:",
        "Recent thoughts of candidate Bradley Carter:",
        "The public's current opinion of candidate Bill Fredrickson",
        "The public's current opinion of candidate Bradley Carter",
        "The public's current opinion of opponent candidate Bradley Carter",
        "The public's current opinion of opponent candidate Bill Fredrickson",
        "Bill Fredrickson's general plan to improve the public's opinion of them:",
        "Bradley Carter's general plan to improve the public's opinion of them:",
        "## POSTING STYLE",
    ]

    # Find the end of the initial role-playing section
    system_end = prompt.find("</system>")
    if system_end == -1:
        raise ValueError("Could not find </system> marker in prompt")

    initial_roleplay = prompt[: system_end + len("</system>")]

    # Find the start of the Exercise section
    exercise_start = prompt.find("Exercise:")
    if exercise_start == -1:
        raise ValueError("Could not find Exercise: section in prompt")

    exercise_section = prompt[exercise_start:]

    # Extract the middle section
    middle_text = prompt[system_end + len("</system>") : exercise_start].strip()

    # Parse components from the middle section
    components = []
    current_pos = 0

    while current_pos < len(middle_text):
        # Find the next component marker
        next_marker_pos = len(middle_text)
        next_marker = None

        for marker in component_markers:
            marker_pos = middle_text.find(marker, current_pos)
            if marker_pos != -1 and marker_pos < next_marker_pos:
                next_marker_pos = marker_pos
                next_marker = marker

        if next_marker is None:
            # No more markers found, add remaining text if any
            remaining = middle_text[current_pos:].strip()
            if remaining:
                components.append(remaining)
            break

        # Add text before the marker (if any) as a component
        if next_marker_pos > current_pos:
            before_marker = middle_text[current_pos:next_marker_pos].strip()
            if before_marker:
                components.append(before_marker)

        # Find the end of this component (start of next marker or end of text)
        component_start = next_marker_pos
        component_end = len(middle_text)

        for marker in component_markers:
            marker_pos = middle_text.find(marker, component_start + 1)
            if marker_pos != -1 and marker_pos < component_end:
                component_end = marker_pos

        # Extract the component
        component_text = middle_text[component_start:component_end].strip()
        if component_text:
            components.append(component_text)

        current_pos = component_end

    return initial_roleplay, components, exercise_section


def jumble_prompt_components(prompt: str, seed: int | None = None) -> str:
    """
    Jumble the middle components of a prompt while keeping the initial
    role-playing section and exercise section in place.

    Args:
        prompt (str): The original prompt
        seed (int, optional): Random seed for reproducible jumbling

    Returns
    -------
        str: The jumbled prompt
    """
    if seed is not None:
        random.seed(seed)

    try:
        initial_roleplay, components, exercise_section = parse_prompt_components(prompt)

        # Shuffle the middle components
        shuffled_components = components.copy()
        random.shuffle(shuffled_components)

        # Reconstruct the prompt
        jumbled_prompt = initial_roleplay
        if shuffled_components:
            jumbled_prompt += "\n\n" + "\n\n".join(shuffled_components)
        jumbled_prompt += "\n\n" + exercise_section

        return jumbled_prompt

    except ValueError as e:
        print(f"Error parsing prompt: {e}")
        return prompt  # Return original if parsing fails


def generate_jumbled_dataset(
    input_file: str,
    output_file: str,
    num_jumbles: int = 5,
    filter_contexts: list[str] | None = None,
) -> None:
    """
    Generate a dataset of jumbled prompts for experimentation.

    Args:
        input_file (str): Path to the original prompts_and_responses.jsonl file
        output_file (str): Path to save the jumbled prompts
        num_jumbles (int): Number of jumbled versions to create per prompt
        filter_contexts (List[str], optional): Only jumble prompts with these contexts
    """
    if filter_contexts is None:
        filter_contexts = ["Poll", "Voting Machine"]

    jumbled_data = []

    with open(input_file, encoding="utf-8") as f:
        for line_num, line in enumerate(f):
            try:
                data = json.loads(line.strip())
                prompt = data.get("prompt", "")

                # Skip if not a role-playing prompt with Exercise context
                if not (prompt.startswith("## ROLE-PLAYING") and "Exercise: Context" in prompt):
                    continue

                # Check if it matches our filter contexts
                if not any(
                    f"Exercise: Context: {context}" in prompt for context in filter_contexts
                ):
                    continue

                # Generate jumbled versions
                for jumble_idx in range(num_jumbles):
                    jumbled_prompt = jumble_prompt_components(
                        prompt, seed=line_num * 1000 + jumble_idx
                    )

                    # Create new data entry
                    jumbled_entry = data.copy()
                    jumbled_entry["prompt"] = jumbled_prompt
                    jumbled_entry["original_line"] = line_num
                    jumbled_entry["jumble_version"] = jumble_idx
                    jumbled_entry["is_jumbled"] = True

                    jumbled_data.append(jumbled_entry)

            except json.JSONDecodeError:
                continue

    # Save jumbled data
    with open(output_file, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(entry) + "\n" for entry in jumbled_data)

    print(f"Generated {len(jumbled_data)} jumbled prompts and saved to {output_file}")


def compare_original_vs_jumbled(
    original_file: str, jumbled_file: str, new_responses_file: str | None = None
) -> dict:
    """
    Compare results between original and jumbled prompts.

    Args:
        original_file (str): Path to original prompts_and_responses.jsonl
        jumbled_file (str): Path to jumbled prompts file
        new_responses_file (str, optional): Path to file with new responses to jumbled prompts

    Returns
    -------
        dict: Comparison results
    """
    print("Analyzing original prompts...")
    original_results = analyze_prompts_and_responses(original_file)

    if new_responses_file:
        print("Analyzing jumbled prompts with new responses...")
        jumbled_results = analyze_prompts_and_responses(new_responses_file)
    else:
        print("No new responses file provided - analysis will be incomplete")
        jumbled_results = {}

    # Compare results
    comparison = {"original": original_results, "jumbled": jumbled_results, "differences": {}}

    # Calculate differences for each episode
    for episode in original_results:
        if episode in jumbled_results:
            orig = original_results[episode]
            jumb = jumbled_results[episode]

            comparison["differences"][episode] = {
                "voting_machine_diff": {
                    "bradley_carter": jumb["voting_machine"]["bradley_carter"]
                    - orig["voting_machine"]["bradley_carter"],
                    "bill_fredrickson": jumb["voting_machine"]["bill_fredrickson"]
                    - orig["voting_machine"]["bill_fredrickson"],
                    "neither": jumb["voting_machine"]["neither"]
                    - orig["voting_machine"]["neither"],
                },
                "poll_changes": {
                    "bradley_carter": len(jumb["polls"]["bradley_carter"])
                    - len(orig["polls"]["bradley_carter"]),
                    "bill_fredrickson": len(jumb["polls"]["bill_fredrickson"])
                    - len(orig["polls"]["bill_fredrickson"]),
                },
            }

    return comparison


# =============================================================================
# OPPOSING OPINION FUNCTIONALITY
# =============================================================================


def extract_candidate_thoughts(prompt: str) -> list[tuple[str, str, int, int]]:
    """
    Extract candidate thoughts sections from the prompt.

    Args:
        prompt (str): The prompt text

    Returns
    -------
        List[Tuple[str, str, int, int]]: List of (candidate_name, content, start_index, end_index)
    """
    extracted_sections = []

    # Define the sections to extract
    sections = [
        (
            "bill_fredrickson",
            "Recent thoughts of candidate Bill Fredrickson:",
            "Recent thoughts of candidate Bradley Carter:",
        ),
        ("bradley_carter", "Recent thoughts of candidate Bradley Carter:", "## POSTING STYLE"),
    ]

    for candidate, start_marker, end_marker in sections:
        start_idx = prompt.find(start_marker)
        if start_idx == -1:
            continue

        # Find the start of content (after the marker and newline)
        content_start = start_idx + len(start_marker)
        while content_start < len(prompt) and prompt[content_start] in ["\n", "\r", " "]:
            content_start += 1

        # Find the end marker
        end_idx = prompt.find(end_marker, content_start)
        if end_idx == -1:
            continue

        # Extract content between markers
        content = prompt[content_start:end_idx].strip()
        if content:
            extracted_sections.append((candidate, content, content_start, end_idx))

    return extracted_sections


def load_transformation_cache(cache_file: str = "opposing_opinion_cache.json") -> dict:
    """
    Load cached transformations from file.

    Args:
        cache_file (str): Path to cache file

    Returns
    -------
        Dict: Cache dictionary
    """
    if os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading cache: {e}")
    return {}


def save_transformation_cache(cache: dict, cache_file: str = "opposing_opinion_cache.json") -> None:
    """
    Save transformations to cache file.

    Args:
        cache (Dict): Cache dictionary
        cache_file (str): Path to cache file
    """
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving cache: {e}")


def get_cached_or_compute_opposite(content: str, cache: dict, seed: int | None = None) -> str:
    """
    Get semantic opposite from cache or compute it using LLM.

    Args:
        content (str): The original content
        cache (Dict): Cache dictionary
        seed (int, optional): Random seed for reproducible results

    Returns
    -------
        str: The semantic opposite content
    """
    # Create a cache key based on content hash
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

    if content_hash in cache:
        print(f"Using cached transformation for content: {content[:50]}...")
        return cache[content_hash]

    print(f"Computing new transformation for content: {content[:50]}...")
    opposite_content = get_semantic_opposite(content, seed)

    # Cache the result
    cache[content_hash] = opposite_content

    return opposite_content


def extract_quoted_content(prompt: str) -> list[tuple[str, int, int]]:
    """
    Extract all content between double quotes in a prompt.

    Args:
        prompt (str): The prompt text

    Returns
    -------
        List[Tuple[str, int, int]]: List of (quoted_content, start_index, end_index)
    """
    quoted_content = []
    i = 0
    while i < len(prompt):
        # Find opening quote
        start_quote = prompt.find('"', i)
        if start_quote == -1:
            break

        # Find closing quote
        end_quote = prompt.find('"', start_quote + 1)
        if end_quote == -1:
            break

        # Extract content between quotes
        content = prompt[start_quote + 1 : end_quote]
        if content.strip():  # Only include non-empty content
            quoted_content.append((content, start_quote, end_quote + 1))

        i = end_quote + 1

    return quoted_content


def get_semantic_opposite(content: str, seed: int | None = None) -> str:
    """
    Get the semantic opposite of the given content using LLM.

    Args:
        content (str): The original content
        seed (int, optional): Random seed for reproducible results

    Returns
    -------
        str: The semantic opposite content
    """
    opposite_prompt = f"""Please provide the exact semantic opposite of the following text. Maintain the same style, tone, and format, but reverse the meaning completely. Do not add explanations or additional text, just return the opposite content:

Original: "{content}"

Semantic opposite:"""

    try:
        response = get_llm_response(opposite_prompt)
        # Clean up the response - remove quotes if LLM added them
        cleaned_response = response.strip()
        if cleaned_response.startswith('"') and cleaned_response.endswith('"'):
            cleaned_response = cleaned_response[1:-1]
        return cleaned_response
    except Exception as e:
        print(f"Error getting semantic opposite for '{content[:50]}...': {e}")
        # Fallback: simple negation patterns
        return apply_simple_negation(content)


def apply_simple_negation(content: str) -> str:
    """
    Apply simple negation patterns as a fallback when LLM is unavailable.

    Args:
        content (str): The original content

    Returns
    -------
        str: Content with simple negations applied
    """
    # Simple word substitutions for common opposites
    opposites = {
        "good": "bad",
        "great": "terrible",
        "excellent": "awful",
        "positive": "negative",
        "support": "oppose",
        "agree": "disagree",
        "like": "dislike",
        "love": "hate",
        "approve": "disapprove",
        "trust": "distrust",
        "strong": "weak",
        "effective": "ineffective",
        "successful": "unsuccessful",
        "competent": "incompetent",
        "honest": "dishonest",
        "reliable": "unreliable",
    }

    result = content.lower()
    for original, opposite in opposites.items():
        result = result.replace(original, opposite)

    return result


def create_opposing_opinion_prompt(prompt: str, seed: int | None = None) -> str:
    """
    Create a prompt with opposing opinions by replacing candidate thoughts and quoted content with semantic opposites.

    Args:
        prompt (str): The original prompt
        seed (int, optional): Random seed for reproducible results

    Returns
    -------
        str: The prompt with opposing opinions
    """
    # Load transformation cache
    cache = load_transformation_cache()
    cache_updated = False

    # Start with the original prompt
    modified_prompt = prompt

    # Extract and replace candidate thoughts sections
    candidate_sections = extract_candidate_thoughts(prompt)
    if candidate_sections:
        print(f"Found {len(candidate_sections)} candidate thought sections to transform")

        # Work backwards through the list to maintain correct indices
        for candidate, content, start_idx, end_idx in reversed(candidate_sections):
            opposite_content = get_cached_or_compute_opposite(content, cache, seed)

            # Replace the content between the markers
            modified_prompt = (
                modified_prompt[:start_idx] + opposite_content + modified_prompt[end_idx:]
            )
            cache_updated = True

    # Save cache if it was updated
    if cache_updated:
        save_transformation_cache(cache)
        print("Transformation cache updated and saved")

    if not candidate_sections:
        print("No candidate thoughts found in prompt")

    return modified_prompt


# =============================================================================
# PARALLELIZED JUMBLING ANALYSIS
# =============================================================================


def process_single_llm_call(args):
    """
    Process a single LLM call for parallel execution.

    Args:
        args: tuple containing (prompt_key, jumbled_prompt, original_data, call_type)

    Returns
    -------
        dict: Result of the LLM call
    """
    prompt_key, jumbled_prompt, original_data, call_type = args

    # Get LLM response
    response = get_llm_response(jumbled_prompt)

    result = {
        "prompt_key": prompt_key,
        "prompt": jumbled_prompt,
        "call_type": call_type,
        "response": response,
        "original_data": original_data,
    }

    if call_type == "voting":
        result["vote"] = analyze_voting_response(response, original_data["agent_name"])
    elif call_type == "poll":
        poll_result = analyze_poll_response(
            jumbled_prompt.split("Exercise: Context: Poll")[1],
            response,
            original_data["agent_name"],
        )
        result["poll_result"] = poll_result

    return result


def analyze_jumbling_effects(
    input_file: str,
    num_jumbles: int = 10,
    disable_jumbling: bool = True,
    opposing_opinion: bool = False,
) -> dict:
    """
    Analyze the effects of jumbling prompts using parallelized LLM calls.

    Args:
        input_file (str): Path to the original prompts_and_responses.jsonl file
        num_jumbles (int): Number of jumbled versions to create per prompt
        disable_jumbling (bool): If True, use original prompt without jumbling to test LLM variation
        opposing_opinion (bool): If True, extract quoted content and replace with semantic opposite

    Returns
    -------
        dict: Comprehensive analysis of jumbling effects (or LLM variation if jumbling disabled, or opposing opinion effects)
    """
    if opposing_opinion:
        action_type = "opposing opinion"
    elif disable_jumbling:
        action_type = "LLM variation"
    else:
        action_type = "jumbling"
    print(f"Analyzing {action_type} effects with {num_jumbles} variations per prompt...")

    # Collect original data for role-playing prompts with Exercise contexts
    voting_prompts = []
    poll_prompts = []

    with open(input_file, encoding="utf-8") as f:
        for line_num, line in enumerate(f):
            try:
                data = json.loads(line.strip())
                prompt = data.get("prompt", "")

                # Skip if not a role-playing prompt with Exercise context
                if not (prompt.startswith("## ROLE-PLAYING") and "Exercise: Context" in prompt):
                    continue

                if "Exercise: Context: Voting Machine" in prompt:
                    voting_prompts.append((line_num, data))
                elif "Exercise: Context: Poll" in prompt:
                    poll_prompts.append((line_num, data))

            except json.JSONDecodeError:
                continue

    print(
        f"Found {len(voting_prompts)} voting machine prompts and {len(poll_prompts)} poll prompts"
    )

    # Prepare all LLM calls for parallel execution
    llm_calls = []

    # Prepare voting calls
    for line_num, data in voting_prompts:
        for run_idx in range(num_jumbles):
            prompt_key = f"voting_{data['episode_idx']}_{data['agent_name']}_{line_num}_{run_idx}"
            if opposing_opinion:
                # Apply opposing opinion transformation
                processed_prompt = create_opposing_opinion_prompt(
                    data["prompt"], seed=line_num * 1000 + run_idx
                )
            elif disable_jumbling:
                # Use original prompt without jumbling
                processed_prompt = data["prompt"]
            else:
                # Apply jumbling
                processed_prompt = jumble_prompt_components(
                    data["prompt"], seed=line_num * 1000 + run_idx
                )
            llm_calls.append((prompt_key, processed_prompt, data, "voting"))

    # Prepare poll calls
    for line_num, data in poll_prompts:
        for run_idx in range(num_jumbles):
            prompt_key = f"poll_{data['episode_idx']}_{data['agent_name']}_{line_num}_{run_idx}"
            if opposing_opinion:
                # Apply opposing opinion transformation
                processed_prompt = create_opposing_opinion_prompt(
                    data["prompt"], seed=line_num * 1000 + run_idx
                )
            elif disable_jumbling:
                # Use original prompt without jumbling
                processed_prompt = data["prompt"]
            else:
                # Apply jumbling
                processed_prompt = jumble_prompt_components(
                    data["prompt"], seed=line_num * 1000 + run_idx
                )
            llm_calls.append((prompt_key, processed_prompt, data, "poll"))

    print(f"Executing {len(llm_calls)} parallel LLM calls...")

    # Execute all LLM calls in parallel
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_call = {
            executor.submit(process_single_llm_call, call): call for call in llm_calls
        }

        completed = 0
        for future in as_completed(future_to_call):
            try:
                result = future.result()
                results.append(result)
                completed += 1
                if completed % 10 == 0:
                    print(f"  Completed {completed}/{len(llm_calls)} calls...")
            except Exception as e:
                print(f"Error in LLM call: {e}")

    print(f"Completed all {len(results)} LLM calls")

    # Save raw results to JSONL file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if opposing_opinion:
        experiment_mode = "opposing_opinion"
    elif disable_jumbling:
        experiment_mode = "no_jumbling"
    else:
        experiment_mode = "jumbled"
    output_filename = f"jumbling_raw_results_{experiment_mode}_{timestamp}.jsonl"

    print(f"Saving {len(results)} raw results to {output_filename}...")

    with open(output_filename, "w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    print(f"Raw results saved to: {output_filename}")

    # Process results
    voting_analysis = process_voting_results(results, voting_prompts, num_jumbles)
    poll_analysis = process_poll_results(results, poll_prompts, num_jumbles)
    statistics_analysis = generate_comprehensive_statistics(voting_analysis, poll_analysis)

    return {
        "voting_analysis": voting_analysis,
        "poll_analysis": poll_analysis,
        "statistics": statistics_analysis,
        "raw_results_file": output_filename,
    }


def process_voting_results(
    results: list[dict], voting_prompts: list[tuple], num_jumbles: int
) -> dict:
    """
    Process the parallel LLM results for voting analysis.
    """
    # Group results by run - build explicitly so types are clear to type checkers
    runs: list[dict[str, Any]] = []
    for i in range(num_jumbles):
        runs.append(
            {
                "run_id": i,
                "bradley_carter": 0,
                "bill_fredrickson": 0,
                "neither": 0,
                "individual_votes": [],
            }
        )

    # Group results by prompt for consistency analysis
    by_prompt = {}

    # Process each result
    voting_results = [r for r in results if r["call_type"] == "voting"]

    for result in voting_results:
        # Extract run information from prompt_key
        parts = result["prompt_key"].split("_")
        run_idx = int(parts[-1])
        line_num = int(parts[-2])
        agent_name = parts[-3]
        episode_idx = int(parts[-4])

        vote = result.get("vote")
        original_data = result["original_data"]

        # Get original vote for comparison
        original_response = original_data["output"]
        original_vote = analyze_voting_response(original_response, agent_name)

        # Record in run results
        if vote:
            runs[run_idx][vote] += 1
            runs[run_idx]["individual_votes"].append(
                {
                    "agent": agent_name,
                    "episode": episode_idx,
                    "original_vote": original_vote,
                    "new_vote": vote,
                    "line_num": line_num,
                }
            )

        # Record for consistency analysis
        prompt_key = f"{episode_idx}_{agent_name}_{line_num}"
        if prompt_key not in by_prompt:
            by_prompt[prompt_key] = {
                "original_vote": original_vote,
                "jumbled_votes": [],
                "agent": agent_name,
                "episode": episode_idx,
            }
        by_prompt[prompt_key]["jumbled_votes"].append(vote)

    return {"runs": runs, "by_prompt": by_prompt}


def process_poll_results(results: list[dict], poll_prompts: list[tuple], num_jumbles: int) -> dict:
    """
    Process the parallel LLM results for poll analysis.
    """
    # Group results by run - explicit construction for typing
    runs: list[dict[str, Any]] = []
    for i in range(num_jumbles):
        runs.append(
            {
                "run_id": i,
                "bradley_carter_scores": [],
                "bill_fredrickson_scores": [],
                "bradley_carter_avg": 0.0,
                "bill_fredrickson_avg": 0.0,
                "individual_ratings": [],
            }
        )

    # Group results by prompt for consistency analysis
    by_prompt = {}

    # Process each result
    poll_results = [r for r in results if r["call_type"] == "poll"]

    for result in poll_results:
        # Extract run information from prompt_key
        parts = result["prompt_key"].split("_")
        run_idx = int(parts[-1])
        line_num = int(parts[-2])
        agent_name = parts[-3]
        episode_idx = int(parts[-4])

        poll_result = result.get("poll_result")
        original_data = result["original_data"]

        # Get original poll data for comparison
        original_prompt = original_data["prompt"]
        original_response = original_data["output"]
        original_poll = analyze_poll_response(
            original_prompt.split("Exercise: Context: Poll")[1], original_response, agent_name
        )

        if poll_result:
            candidate, score = poll_result

            # Record in run results
            if candidate == "bradley_carter":
                runs[run_idx]["bradley_carter_scores"].append(score)
            elif candidate == "bill_fredrickson":
                runs[run_idx]["bill_fredrickson_scores"].append(score)

            runs[run_idx]["individual_ratings"].append(
                {
                    "agent": agent_name,
                    "episode": episode_idx,
                    "candidate": candidate,
                    "original_score": original_poll[1] if original_poll else None,
                    "new_score": score,
                    "line_num": line_num,
                }
            )

            # Record for consistency analysis
            prompt_key = f"{episode_idx}_{agent_name}_{candidate}_{line_num}"
            if prompt_key not in by_prompt:
                by_prompt[prompt_key] = {
                    "original_score": original_poll[1] if original_poll else None,
                    "jumbled_scores": [],
                    "agent": agent_name,
                    "episode": episode_idx,
                    "candidate": candidate,
                }
            by_prompt[prompt_key]["jumbled_scores"].append(score)

    # Calculate averages for each run
    for run in runs:
        if run["bradley_carter_scores"]:
            run["bradley_carter_avg"] = statistics.mean(run["bradley_carter_scores"])
        if run["bill_fredrickson_scores"]:
            run["bill_fredrickson_avg"] = statistics.mean(run["bill_fredrickson_scores"])

    return {"runs": runs, "by_prompt": by_prompt}


def generate_comprehensive_statistics(voting_analysis: dict, poll_analysis: dict) -> dict:
    """
    Generate comprehensive statistics from voting and poll analyses.
    """
    stats = {}

    # Process voting statistics
    if voting_analysis and "runs" in voting_analysis:
        voting_runs = voting_analysis["runs"]
        voting_stats = {}

        # Extract vote counts for each candidate across all runs
        bradley_counts = [run.get("bradley_carter", 0) for run in voting_runs]
        bill_counts = [run.get("bill_fredrickson", 0) for run in voting_runs]
        neither_counts = [run.get("neither", 0) for run in voting_runs]

        # Calculate statistics for each candidate
        if bradley_counts:
            voting_stats["bradley_carter"] = {
                "mean": statistics.mean(bradley_counts),
                "median": statistics.median(bradley_counts),
                "std_dev": statistics.stdev(bradley_counts) if len(bradley_counts) > 1 else 0,
                "min": min(bradley_counts),
                "max": max(bradley_counts),
            }

        if bill_counts:
            voting_stats["bill_fredrickson"] = {
                "mean": statistics.mean(bill_counts),
                "median": statistics.median(bill_counts),
                "std_dev": statistics.stdev(bill_counts) if len(bill_counts) > 1 else 0,
                "min": min(bill_counts),
                "max": max(bill_counts),
            }

        if neither_counts:
            voting_stats["neither"] = {
                "mean": statistics.mean(neither_counts),
                "median": statistics.median(neither_counts),
                "std_dev": statistics.stdev(neither_counts) if len(neither_counts) > 1 else 0,
                "min": min(neither_counts),
                "max": max(neither_counts),
            }

        # Calculate individual vote consistency
        if "by_prompt" in voting_analysis:
            consistency_scores = []
            for prompt_key, prompt_data in voting_analysis["by_prompt"].items():
                original_vote = prompt_data.get("original_vote")
                jumbled_votes = prompt_data.get("jumbled_votes", [])

                if original_vote and jumbled_votes:
                    consistent_count = sum(1 for vote in jumbled_votes if vote == original_vote)
                    consistency = consistent_count / len(jumbled_votes)
                    consistency_scores.append(consistency)

            if consistency_scores:
                voting_stats["individual_consistency"] = {
                    "mean": statistics.mean(consistency_scores),
                    "median": statistics.median(consistency_scores),
                    "std_dev": statistics.stdev(consistency_scores)
                    if len(consistency_scores) > 1
                    else 0,
                    "min": min(consistency_scores),
                    "max": max(consistency_scores),
                }

        stats["voting"] = voting_stats

    # Process poll statistics
    if poll_analysis and "runs" in poll_analysis:
        poll_runs = poll_analysis["runs"]
        poll_stats = {}

        # Extract average scores for each candidate across all runs
        bradley_averages = [
            run.get("bradley_carter_avg", 0)
            for run in poll_runs
            if run.get("bradley_carter_avg", 0) > 0
        ]
        bill_averages = [
            run.get("bill_fredrickson_avg", 0)
            for run in poll_runs
            if run.get("bill_fredrickson_avg", 0) > 0
        ]

        # Calculate statistics for each candidate
        if bradley_averages:
            poll_stats["bradley_carter"] = {
                "mean": statistics.mean(bradley_averages),
                "median": statistics.median(bradley_averages),
                "std_dev": statistics.stdev(bradley_averages) if len(bradley_averages) > 1 else 0,
                "min": min(bradley_averages),
                "max": max(bradley_averages),
            }

        if bill_averages:
            poll_stats["bill_fredrickson"] = {
                "mean": statistics.mean(bill_averages),
                "median": statistics.median(bill_averages),
                "std_dev": statistics.stdev(bill_averages) if len(bill_averages) > 1 else 0,
                "min": min(bill_averages),
                "max": max(bill_averages),
            }

        # Calculate individual poll score deviations
        if "by_prompt" in poll_analysis:
            deviations = []
            for prompt_key, prompt_data in poll_analysis["by_prompt"].items():
                original_score = prompt_data.get("original_score")
                jumbled_scores = prompt_data.get("jumbled_scores", [])

                if original_score is not None and jumbled_scores:
                    for score in jumbled_scores:
                        if score is not None:
                            deviation = abs(score - original_score)
                            deviations.append(deviation)

            if deviations:
                poll_stats["individual_deviations"] = {
                    "mean": statistics.mean(deviations),
                    "median": statistics.median(deviations),
                    "std_dev": statistics.stdev(deviations) if len(deviations) > 1 else 0,
                    "min": min(deviations),
                    "max": max(deviations),
                }

        stats["polls"] = poll_stats

    return stats


def print_comprehensive_analysis(analysis_results: dict) -> None:
    """
    Print comprehensive analysis results in a readable format.
    """
    if not analysis_results:
        print("No analysis results to display.")
        return

    print("COMPREHENSIVE PROMPT JUMBLING ANALYSIS")
    print("=" * 50)

    # Print basic summary
    voting_analysis = analysis_results.get("voting_analysis", {})
    poll_analysis = analysis_results.get("poll_analysis", {})
    stats = analysis_results.get("statistics", {})

    if voting_analysis.get("runs"):
        num_runs = len(voting_analysis["runs"])
        print(f"Number of runs analyzed: {num_runs}")

        # Show voting results for each run
        print("\nVOTING RESULTS BY RUN:")
        print("-" * 25)
        for i, run in enumerate(voting_analysis["runs"]):
            print(
                f"Run {i + 1}: Bradley Carter: {run.get('bradley_carter', 0)}, "
                f"Bill Fredrickson: {run.get('bill_fredrickson', 0)}, "
                f"Neither: {run.get('neither', 0)}"
            )

    if poll_analysis.get("runs"):
        print("\nPOLL RESULTS BY RUN:")
        print("-" * 25)
        for i, run in enumerate(poll_analysis["runs"]):
            bradley_avg = run.get("bradley_carter_avg", 0)
            bill_avg = run.get("bill_fredrickson_avg", 0)
            print(
                f"Run {i + 1}: Bradley Carter avg: {bradley_avg:.2f}, "
                f"Bill Fredrickson avg: {bill_avg:.2f}"
            )

    # Print statistical analysis
    if stats:
        print("\nSTATISTICAL ANALYSIS:")
        print("-" * 20)

        # Voting statistics
        if "voting" in stats:
            voting_stats = stats["voting"]
            print("\nVOTING CONSISTENCY:")
            print("-" * 20)

            for candidate in ["bradley_carter", "bill_fredrickson", "neither"]:
                if candidate in voting_stats:
                    s = voting_stats[candidate]
                    name = candidate.replace("_", " ").title()
                    print(f"{name} Votes Across Runs:")
                    print(f"  Mean: {s['mean']:.2f} ± {s['std_dev']:.2f}")
                    print(f"  Median: {s['median']:.2f}")
                    print(f"  Range: {s['min']} - {s['max']}")
                    print()

            # Individual consistency
            if "individual_consistency" in voting_stats:
                consistency = voting_stats["individual_consistency"]
                print("Individual Vote Consistency:")
                print(
                    f"  Mean consistency: {consistency['mean']:.1%} ± {consistency['std_dev']:.1%}"
                )
                print(f"  Median consistency: {consistency['median']:.1%}")
                print()

        # Poll statistics
        if "polls" in stats:
            poll_stats = stats["polls"]
            print("\nPOLL SCORE STATISTICS:")
            print("-" * 25)

            for candidate in ["bradley_carter", "bill_fredrickson"]:
                if candidate in poll_stats:
                    s = poll_stats[candidate]
                    name = candidate.replace("_", " ").title()
                    print(f"{name} Average Scores Across Runs:")
                    print(f"  Mean: {s['mean']:.2f} ± {s['std_dev']:.2f}")
                    print(f"  Median: {s['median']:.2f}")
                    print(f"  Range: {s['min']:.2f} - {s['max']:.2f}")
                    print()

            # Individual deviations
            if "individual_deviations" in poll_stats:
                dev = poll_stats["individual_deviations"]
                print("Individual Prompt Score Deviations:")
                print(f"  Mean deviation from original: {dev['mean']:.2f} ± {dev['std_dev']:.2f}")
                print(f"  Median deviation: {dev['median']:.2f}")

    # Print per-agent analysis
    print_per_agent_analysis(analysis_results)

    # Overall insights
    print("\nSTATISTICAL INSIGHTS:")
    print("-" * 20)

    if "voting" in stats:
        voting_stats = stats["voting"]
        avg_consistency = voting_stats.get("individual_consistency", {}).get("mean", 0)

        if avg_consistency > 0.8:
            stability_voting = "high"
        elif avg_consistency > 0.6:
            stability_voting = "moderate"
        else:
            stability_voting = "low"

        print(f"• Voting stability: {stability_voting} (avg consistency: {avg_consistency:.1%})")


def print_per_agent_analysis(analysis_results: dict) -> None:
    """
    Print per-agent analysis showing individual agent voting and polling variations.
    """
    voting_analysis = analysis_results.get("voting_analysis", {})
    poll_analysis = analysis_results.get("poll_analysis", {})

    print("\nPER-AGENT ANALYSIS:")
    print("=" * 50)

    # Collect all agents from the data
    all_agents = set()

    # Get agents from voting data
    if voting_analysis.get("by_prompt"):
        for prompt_key, data in voting_analysis["by_prompt"].items():
            all_agents.add(data.get("agent"))

    # Get agents from poll data
    if poll_analysis.get("by_prompt"):
        for prompt_key, data in poll_analysis["by_prompt"].items():
            all_agents.add(data.get("agent"))

    # Remove None values and sort alphabetically by first name
    def get_first_name(full_name: str) -> str:
        """Extract first name from full name for sorting."""
        if not full_name:
            return ""
        return full_name.split(maxsplit=1)[0].lower()

    # Build a sorted list of agents (keep original 'all_agents' as a set)
    sorted_agents: list[str] = sorted([agent for agent in all_agents if agent], key=get_first_name)

    if not sorted_agents:
        print("No agent data found.")
        return

    print(f"Found {len(sorted_agents)} agents: {', '.join(sorted_agents)}")
    print()

    # Use the sorted_agents list for iteration below; keep all_agents as the original set
    all_agents_list = sorted_agents

    # Analyze each agent
    for agent in all_agents_list:
        print(f"AGENT: {agent}")
        print("-" * (len(agent) + 7))

        # Voting analysis for this agent
        agent_voting_data = []
        if voting_analysis.get("by_prompt"):
            for prompt_key, data in voting_analysis["by_prompt"].items():
                if data.get("agent") == agent:
                    original_vote = data.get("original_vote")
                    jumbled_votes = data.get("jumbled_votes", [])
                    episode = data.get("episode")

                    # Calculate consistency for this prompt
                    if original_vote and jumbled_votes:
                        consistent_count = sum(1 for vote in jumbled_votes if vote == original_vote)
                        consistency = consistent_count / len(jumbled_votes)

                        # Count vote changes by target
                        vote_change_breakdown = {
                            "bradley_carter": 0,
                            "bill_fredrickson": 0,
                            "neither": 0,
                        }
                        for vote in jumbled_votes:
                            if vote != original_vote:
                                vote_change_breakdown[vote] += 1

                        agent_voting_data.append(
                            {
                                "episode": episode,
                                "original_vote": original_vote,
                                "jumbled_votes": jumbled_votes,
                                "consistency": consistency,
                                "vote_changes": len(
                                    [vote for vote in jumbled_votes if vote != original_vote]
                                ),
                                "vote_change_breakdown": vote_change_breakdown,
                            }
                        )

        if agent_voting_data:
            print("  Voting Behavior:")
            total_consistency = statistics.mean([d["consistency"] for d in agent_voting_data])
            total_vote_changes = sum([d["vote_changes"] for d in agent_voting_data])
            total_opportunities = sum([len(d["jumbled_votes"]) for d in agent_voting_data])

            # Calculate overall vote change breakdown
            overall_changes = {"bradley_carter": 0, "bill_fredrickson": 0, "neither": 0}
            for data in agent_voting_data:
                for vote_type, count in data["vote_change_breakdown"].items():
                    overall_changes[vote_type] += count

            print(f"    Overall consistency: {total_consistency:.1%}")
            print(
                f"    Vote changes: {total_vote_changes}/{total_opportunities} ({total_vote_changes / total_opportunities:.1%})"
            )

            # Show breakdown of what votes changed to
            if total_vote_changes > 0:
                print("    Vote changes breakdown:")
                for vote_type, count in overall_changes.items():
                    if count > 0:
                        vote_name = vote_type.replace("_", " ").title()
                        percentage = (count / total_vote_changes) * 100
                        print(f"      → {vote_name}: {count} times ({percentage:.1f}%)")

            # Show per-episode voting details
            for vote_data in sorted(agent_voting_data, key=lambda x: x["episode"]):
                episode = vote_data["episode"]
                original = (
                    vote_data["original_vote"].replace("_", " ").title()
                    if vote_data["original_vote"]
                    else "None"
                )
                consistency = vote_data["consistency"]
                changes = vote_data["vote_changes"]
                total_votes = len(vote_data["jumbled_votes"])

                change_details = ""
                if changes > 0:
                    change_breakdown = vote_data["vote_change_breakdown"]
                    change_parts = []
                    for vote_type, count in change_breakdown.items():
                        if count > 0:
                            vote_name = vote_type.replace("_", " ").title()
                            change_parts.append(f"{count}→{vote_name}")
                    if change_parts:
                        change_details = f", changes: {', '.join(change_parts)}"

                print(
                    f"    Episode {episode}: {original} (consistency: {consistency:.1%}{change_details})"
                )
        else:
            print("  Voting Behavior: No data found")

    # Poll analysis for this agent
    agent_poll_data: dict[str, list[Any]] = {"bradley_carter": [], "bill_fredrickson": []}
    if poll_analysis.get("by_prompt"):
        for prompt_key, data in poll_analysis["by_prompt"].items():
            if data.get("agent") == agent:
                candidate = data.get("candidate")
                original_score = data.get("original_score")
                jumbled_scores = data.get("jumbled_scores", [])
                episode = data.get("episode")

                if candidate and original_score is not None and jumbled_scores:
                    # Calculate score variations
                    valid_scores = [score for score in jumbled_scores if score is not None]
                    if valid_scores:
                        deviations = [abs(score - original_score) for score in valid_scores]
                        mean_deviation = statistics.mean(deviations)
                        max_deviation = max(deviations)

                        agent_poll_data[candidate].append(
                            {
                                "episode": episode,
                                "original_score": original_score,
                                "jumbled_scores": valid_scores,
                                "mean_deviation": mean_deviation,
                                "max_deviation": max_deviation,
                                "score_range": (min(valid_scores), max(valid_scores)),
                            }
                        )

    if agent_poll_data["bradley_carter"] or agent_poll_data["bill_fredrickson"]:
        print("  Poll Score Behavior:")

        for candidate in ["bradley_carter", "bill_fredrickson"]:
            candidate_data = agent_poll_data[candidate]
            if candidate_data:
                candidate_name = candidate.replace("_", " ").title()

                # Calculate overall statistics
                all_deviations = [d["mean_deviation"] for d in candidate_data]
                overall_deviation = statistics.mean(all_deviations) if all_deviations else 0

                all_max_deviations = [d["max_deviation"] for d in candidate_data]
                overall_max_deviation = max(all_max_deviations) if all_max_deviations else 0

                print(f"    {candidate_name}:")
                print(f"      Average deviation: {overall_deviation:.2f} points")
                print(f"      Maximum deviation: {overall_max_deviation:.2f} points")

                # Show per-episode details
                for poll_data in sorted(candidate_data, key=lambda x: x["episode"]):
                    episode = poll_data["episode"]
                    original = poll_data["original_score"]
                    mean_dev = poll_data["mean_deviation"]
                    max_dev = poll_data["max_deviation"]
                    score_min, score_max = poll_data["score_range"]

                    print(
                        f"      Episode {episode}: Original={original}, Range={score_min}-{score_max}, "
                        f"AvgDev={mean_dev:.1f}, MaxDev={max_dev:.1f}"
                    )
    else:
        print("  Poll Score Behavior: No data found")

    print()  # Empty line between agents

    # --- Poll score difference (Bill - Bradley) histogram ---
    agent_poll_diff = {}
    poll_analysis = analysis_results.get("poll_analysis", {})
    # For each agent, for each run, compute (mean poll score for Bill - mean poll score for Bradley)
    if poll_analysis.get("runs"):
        num_runs = len(poll_analysis["runs"])
        for agent in all_agents:
            run_diffs = []
            for run in poll_analysis["runs"]:
                # Find all ratings for this agent in this run
                bill_scores = [
                    r["new_score"]
                    for r in run["individual_ratings"]
                    if r["agent"] == agent
                    and r["candidate"] == "bill_fredrickson"
                    and r["new_score"] is not None
                ]
                bradley_scores = [
                    r["new_score"]
                    for r in run["individual_ratings"]
                    if r["agent"] == agent
                    and r["candidate"] == "bradley_carter"
                    and r["new_score"] is not None
                ]
                # Compute mean for each candidate
                mean_bill = np.mean(bill_scores) if bill_scores else 0
                mean_bradley = np.mean(bradley_scores) if bradley_scores else 0
                diff = mean_bill - mean_bradley
                run_diffs.append(diff)
            # Aggregate stats
            mean_diff = np.mean(run_diffs) if run_diffs else 0
            std_diff = np.std(run_diffs) if run_diffs else 0
            min_diff = np.min(run_diffs) if run_diffs else 0
            max_diff = np.max(run_diffs) if run_diffs else 0
            favored = "bill_fredrickson" if mean_diff > 0 else "bradley_carter"
            agent_poll_diff[agent] = {
                "mean_diff": mean_diff,
                "std_diff": std_diff,
                "min_diff": min_diff,
                "max_diff": max_diff,
                "favored": favored,
                "run_diffs": run_diffs,
            }
        # Plot histogram
        if agent_poll_diff:
            # Sort agents alphabetically by first name (same order as all_agents)
            agents = all_agents  # Use the already sorted list

            values = [agent_poll_diff[a]["mean_diff"] for a in agents]
            errors = [agent_poll_diff[a]["std_diff"] for a in agents]
            colors = [
                "#ff7f0e" if agent_poll_diff[a]["favored"] == "bill_fredrickson" else "#1f77b4"
                for a in agents
            ]
            labels = []
            for a in agents:
                fav = agent_poll_diff[a].get("favored", "")
                fav_label = str(fav).replace("_", " ").title()
                labels.append(f"{a}\n({fav_label})")
            plt.figure(figsize=(max(8, len(agents) * 0.6), 6))
            bars = plt.bar(labels, values, yerr=errors, color=colors, capsize=5)
            plt.axhline(0, color="gray", linestyle="--", linewidth=1)
            plt.ylabel("Mean Poll Score Difference (Bill - Bradley)")
            plt.title("Agent Poll Score Favorability by First Name (Positive = Bill Favored)")
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()
            plt.legend([bars[0], bars[-1]], ["Bill Fredrickson Favored", "Bradley Carter Favored"])
            outname = (
                f"agent_poll_score_diff_histogram_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )
            plt.savefig(outname)
            print(f"Saved agent poll score difference histogram to {outname}")

            # Create separate plots for each candidate's raw poll scores
            # Calculate raw poll scores for each agent and candidate
            agent_raw_scores: dict[str, dict[str, list[float]]] = {}
            for agent in all_agents:
                agent_raw_scores[agent] = {"bradley_carter": [], "bill_fredrickson": []}

                # Collect raw scores across all runs
                for run in poll_analysis["runs"]:
                    bradley_scores = [
                        r["new_score"]
                        for r in run["individual_ratings"]
                        if r["agent"] == agent
                        and r["candidate"] == "bradley_carter"
                        and r["new_score"] is not None
                    ]
                    bill_scores = [
                        r["new_score"]
                        for r in run["individual_ratings"]
                        if r["agent"] == agent
                        and r["candidate"] == "bill_fredrickson"
                        and r["new_score"] is not None
                    ]

                    if bradley_scores:
                        agent_raw_scores[agent]["bradley_carter"].extend(bradley_scores)
                    if bill_scores:
                        agent_raw_scores[agent]["bill_fredrickson"].extend(bill_scores)

            # Plot 1: Bradley Carter raw poll scores
            bradley_means = []
            bradley_stds = []
            valid_agents_bradley = []

            for agent in agents:
                bradley_scores = agent_raw_scores[agent]["bradley_carter"]
                if bradley_scores:
                    bradley_means.append(np.mean(bradley_scores))
                    bradley_stds.append(np.std(bradley_scores))
                    valid_agents_bradley.append(agent)

            if valid_agents_bradley:
                plt.figure(figsize=(max(8, len(valid_agents_bradley) * 0.6), 6))
                bars = plt.bar(
                    valid_agents_bradley,
                    bradley_means,
                    yerr=bradley_stds,
                    color="#1f77b4",
                    capsize=5,
                )
                plt.ylabel("Bradley Carter Poll Score (1-9)")
                plt.title("Agent Poll Scores for Bradley Carter (Sorted by First Name)")
                plt.xticks(rotation=45, ha="right")
                plt.ylim(0, 10)  # Poll scores are 1-9
                plt.tight_layout()
                outname_bradley = (
                    f"bradley_poll_scores_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                )
                plt.savefig(outname_bradley)
                print(f"Saved Bradley poll scores histogram to {outname_bradley}")

            # Plot 2: Bill Fredrickson raw poll scores
            bill_means = []
            bill_stds = []
            valid_agents_bill = []

            for agent in agents:
                bill_scores = agent_raw_scores[agent]["bill_fredrickson"]
                if bill_scores:
                    bill_means.append(np.mean(bill_scores))
                    bill_stds.append(np.std(bill_scores))
                    valid_agents_bill.append(agent)

            if valid_agents_bill:
                plt.figure(figsize=(max(8, len(valid_agents_bill) * 0.6), 6))
                bars = plt.bar(
                    valid_agents_bill, bill_means, yerr=bill_stds, color="#ff7f0e", capsize=5
                )
                plt.ylabel("Bill Fredrickson Poll Score (1-9)")
                plt.title("Agent Poll Scores for Bill Fredrickson (Sorted by First Name)")
                plt.xticks(rotation=45, ha="right")
                plt.ylim(0, 10)  # Poll scores are 1-9
                plt.tight_layout()
                outname_bill = f"bill_poll_scores_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                plt.savefig(outname_bill)
                print(f"Saved Bill poll scores histogram to {outname_bill}")

            # Print summary statistics for each candidate's raw poll scores
            if valid_agents_bradley:
                print("\nBRADLEY CARTER RAW POLL SCORE STATISTICS:")
                print(f"  Mean score: {np.mean(bradley_means):.3f}")
                print(f"  Median score: {np.median(bradley_means):.3f}")
                print(f"  Std dev of means: {np.std(bradley_means):.3f}")
                print(
                    f"  Range of means: {np.min(bradley_means):.3f} - {np.max(bradley_means):.3f}"
                )
                print(f"  Average variability (std dev): {np.mean(bradley_stds):.3f}")

            if valid_agents_bill:
                print("\nBILL FREDRICKSON RAW POLL SCORE STATISTICS:")
                print(f"  Mean score: {np.mean(bill_means):.3f}")
                print(f"  Median score: {np.median(bill_means):.3f}")
                print(f"  Std dev of means: {np.std(bill_means):.3f}")
                print(f"  Range of means: {np.min(bill_means):.3f} - {np.max(bill_means):.3f}")
                print(f"  Average variability (std dev): {np.mean(bill_stds):.3f}")

            # Compare variability between the two candidates' raw scores
            if valid_agents_bradley and valid_agents_bill:
                bradley_var = np.mean(bradley_stds)
                bill_var = np.mean(bill_stds)
                print("\nRAW SCORE VARIABILITY COMPARISON:")
                print(f"  Bradley average std dev: {bradley_var:.3f}")
                print(f"  Bill average std dev: {bill_var:.3f}")
                print(f"  Variability ratio (Bradley/Bill): {bradley_var / bill_var:.2f}")


def _normalize_path(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(os.getcwd(), p)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze prompts_and_responses.jsonl for voting and polls, and run jumbling or "
            "opposing-opinion experiments."
        )
    )
    parser.add_argument("--input", "-i", required=True, help="Path to prompts_and_responses.jsonl")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--jumble", dest="jumble", action="store_true", help="Apply jumbling to prompts"
    )
    group.add_argument(
        "--no-jumble",
        dest="no_jumble",
        action="store_true",
        help="Disable jumbling (use original prompts)",
    )
    group.add_argument(
        "--opposing",
        dest="opposing",
        action="store_true",
        help="Run opposing opinion transformation",
    )
    parser.add_argument(
        "--num-jumbles", type=int, default=10, help="Number of jumble/variation runs per prompt"
    )
    parser.add_argument(
        "--output-dir", default=".", help="Directory to save raw results and summaries"
    )
    parser.add_argument(
        "--max-workers", type=int, default=10, help="Max workers for parallel LLM calls"
    )

    args = parser.parse_args(argv)

    input_path = _normalize_path(args.input)
    output_dir = _normalize_path(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    print("Starting analysis...")
    results = analyze_prompts_and_responses(input_path)
    print_analysis_results(results)

    disable_jumbling = False
    opposing_opinion = False
    # Determine mode
    if args.opposing:
        mode = "opposing_opinion"
        opposing_opinion = True
    if args.no_jumble:
        mode = "no_jumbling"
        disable_jumbling = True
    if not args.opposing and not args.no_jumble:
        mode = "jumbled"

    print("\n" + "=" * 80)
    print(f"Running mode: {mode} (num_jumbles={args.num_jumbles})")
    print("=" * 80)

    analysis_results = analyze_jumbling_effects(
        input_path,
        num_jumbles=args.num_jumbles,
        disable_jumbling=disable_jumbling,
        opposing_opinion=opposing_opinion,
    )

    # Move raw results into output dir if produced
    raw = analysis_results.get("raw_results_file")
    if raw:
        try:
            dst = os.path.join(output_dir, os.path.basename(raw))
            os.replace(raw, dst)
            analysis_results["raw_results_file"] = dst
            print(f"Moved raw results to {dst}")
        except Exception as e:
            print(f"Warning: could not move raw results file: {e}")

    print_comprehensive_analysis(analysis_results)

    # Save JSON summary
    summary_path = os.path.join(
        output_dir, f"analysis_summary_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    try:
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(analysis_results, f, ensure_ascii=False, indent=2)
        print(f"Saved analysis summary to {summary_path}")
    except Exception as e:
        print(f"Warning: could not save analysis summary: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
