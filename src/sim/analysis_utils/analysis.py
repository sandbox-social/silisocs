import json
import os
import re
import sys

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sim_utils")))
from media_utils import GptLanguageModel

# --- Page Configuration ---
# Set the layout to wide to make more space for the text columns
st.set_page_config(layout="wide", page_title="LLM Interaction Dashboard")


# --- LLM Configuration & Function using GptLanguageModel ---
load_dotenv()
LLM_CONFIG = {
    "model": "gpt-4.1-mini",
}


# Instantiate the LLM model (singleton for the app)
@st.cache_resource
def get_llm_model():
    api_key = os.getenv("OPENAI_API_KEY")
    return GptLanguageModel(
        model_name=LLM_CONFIG["model"],
        api_key=api_key,
        log_file=None,
        debug=False,
    )


def get_llm_response(prompt_text, config):
    """
    Calls the GptLanguageModel's sample_text method to get a real LLM response.
    """
    model = get_llm_model()
    return model.sample_text(
        prompt=prompt_text,
        terminators=(),
        max_tokens=2200,
    )


# --- Data Loading and Caching ---
# Use st.cache_data to load the data only once, speeding up the app.
@st.cache_data
def load_data(uploaded_file):
    """
    Loads data from a JSONL file, filtering for valid prompts.
    A valid prompt is one that starts with '## ROLE-PLAYING INSTRUCTIONS'.
    """
    if uploaded_file is None:
        return pd.DataFrame()  # Return empty dataframe if no file

    data = []
    # To read the file uploaded by st.file_uploader, we need to decode it
    lines = uploaded_file.getvalue().decode("utf-8").splitlines()
    for line in lines:
        try:
            record = json.loads(line)
            # This is the critical filter requested by the user
            if "prompt" in record and record["prompt"].startswith("## ROLE-PLAYING INSTRUCTIONS"):
                data.append(record)
        except json.JSONDecodeError:
            # Silently ignore lines that aren't valid JSON
            continue
    return pd.DataFrame(data)


def extract_action_type(output_text):
    """Helper function to get the action type from the output for display."""
    match = re.search(r"ACTION TYPE:\s*(\w+)", output_text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return "UNKNOWN"


# --- Main Application UI ---
st.title("Interactive LLM Prompt Dashboard")
st.markdown(
    "Upload a `.jsonl` file to begin. The app will filter for records where the prompt starts with `## ROLE-PLAYING INSTRUCTIONS`."
)

# File uploader allows users to use their own data
uploaded_file = st.file_uploader("Choose a JSONL file", type=["jsonl"])
df = load_data(uploaded_file)

if df.empty:
    st.warning(
        "The uploaded file contains no valid records or is empty. Please check the file content."
    )
else:
    # --- Sidebar for Filters ---
    st.sidebar.header("Filters")

    # Dropdown for Agent Name
    agent_names = df["agent_name"].unique()
    selected_agent: str = st.sidebar.selectbox("Select Agent Name", options=agent_names)

    # Filter dataframe based on selected agent
    agent_df = df[df["agent_name"] == selected_agent]

    # Dropdown for Episode, dependent on the selected agent
    episode_indices = sorted(agent_df["episode_idx"].unique())
    selected_episode = st.sidebar.selectbox("Select Episode", options=episode_indices)

    # --- Display Filtered Actions ---
    st.header(f"Displaying Actions for '{selected_agent}' in Episode '{selected_episode}'")

    # Final filtering based on both agent and episode
    filtered_df = agent_df[agent_df["episode_idx"] == selected_episode]

    if filtered_df.empty:
        st.info("No actions found for this agent/episode combination.")
    else:
        # Iterate through each row (action) of the filtered data
        for index, row in filtered_df.iterrows():
            action_type = extract_action_type(row["output"])
            expander_title = f"Action #{index}: {row['agent_name']} performs '{action_type}'"

            # Use an expander for each action, which can be clicked to open
            with st.expander(expander_title):
                # Two columns for original prompt/output and the editing area
                col1, col2 = st.columns(2)

                # --- Column 1: Display Original Data ---
                with col1:
                    st.subheader("Original Prompt")
                    # Use a disabled text_area to show the prompt and respect formatting
                    st.text_area(
                        "Original Prompt Text",
                        value=row["prompt"],
                        height=300,
                        disabled=True,
                        key=f"orig_prompt_{index}",
                    )
                    # Add some vertical space between prompt and output for better separation
                    st.markdown("<div style='height: 55px;'></div>", unsafe_allow_html=True)

                    st.subheader("Original Output")
                    # Use st.code to display the output with a fixed-width font
                    st.code(row["output"], language=None)

                # --- Column 2: Edit, Rerun, and See New Output ---
                with col2:
                    st.subheader("Edit and Re-run")

                    # The editable dialogue box for the prompt
                    edited_prompt = st.text_area(
                        "Editable Prompt",
                        value=row["prompt"],
                        height=300,
                        key=f"edited_prompt_{index}",
                    )

                    # The button to trigger the LLM call
                    if st.button("Generate New Output", key=f"rerun_button_{index}"):
                        with st.spinner("Calling LLM API..."):
                            new_output = get_llm_response(edited_prompt, LLM_CONFIG)
                            st.session_state[f"new_output_{index}"] = new_output

                    # Display the newly generated output if it exists in the session state
                    if f"new_output_{index}" in st.session_state:
                        st.subheader("Newly Generated Output")
                        st.code(st.session_state[f"new_output_{index}"], language=None)
