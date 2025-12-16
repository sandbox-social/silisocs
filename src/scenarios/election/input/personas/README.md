# Reddit Data Processing Pipeline Documentation

## Part (A): Getting Reddit Data from the r/politics subReddit

*Section placeholder for Reddit data collection process*

## Part (B): Ranking Posts Based on Interaction

The code conducts a statistical post-processing of a dataset of social media submissions. Starting from an input JSON file representing multiple posts, it extracts three primary variables for each entry:
- The `aggregate score` (a measure of popularity or user appraisal)
- The `upvote ratio` (proportion of positive votes out of total votes)
- The `number of comments`

It uses these raw metrics to derive two derived parameters:

1. **Number of Votes**: Estimated using a formula that relates the post's total score to its upvote ratio. This calculation is grounded in an inverse mapping from the combined attributes of a post's score and the fraction of users who positively engaged with it.

    Mathematically, it assumes the relationship:
    ```
    Number of Votes = Score / (2 × Upvote Ratio – 1)
    ```
    This formula treats the score and upvote ratio as correlates of an underlying voting process, translating the observed aggregated metrics into an approximate count of total votes cast.

2. **Engagement Ratio**: Computed as the ratio of the number of comments to the inferred number of votes:
    ```
    Engagement Ratio = Number of Comments / Number of Votes
    ```
    The engagement ratio quantifies how actively the post's audience participated through discussion relative to their voting behavior.

The code filters out invalid posts and sorts them in descending order by their computed engagement ratio, outputting the results to a new JSON file.

## Part (B.1): Extracting the Top-k Posts

This component:
- Loads a JSON dataset
- Sorts records by a specified numeric metric in descending order
- Selects the top `k` objects
- Writes the results to a new JSON file

## Part (B.2): Extracting Top Users' Usernames

This section:
- Reads from the top-k posts JSON file
- Extracts author names from the `author_name` field
- Outputs the names to a plain text file

## Part (C): Getting Reddit Users' Data

*Section placeholder for user data collection process*

## Part (D): Cleaning the Reddit Data

This script transforms JSON objects representing posts by:
- Parsing input JSON containing raw scraped data
- Grouping posts by `author_id`
- Consolidating all `titles` associated with each author
- Outputting a new JSON file organized by author

## Part (E): Persona Generation with the LLM

### 1. Input Data Structure
```json
{
    "author_id": "",
    "titles": []
}
```

### 2. Prompts

#### System Message
```
"You're a psychologist working in social and political sciences. You have to extract information about people based on interactions they have with others, and use it to design personas for a simulation."
```

#### User Message Components
- JSON-serialized batch of user interaction data
- Persona descriptor template including:
  - Basic identifying information (name, user reference, gender/sex)
  - Political identity
  - Personality traits (Big 5)
  - Schwartz values
  - Interaction-based description

### 3. Batch Processing
- Processes data in batches
- Sends structured prompts to OpenAI API
- Captures model output directly

### 4. Output Format
JSON-formatted persona objects containing:
- Name
- User_Reference
- Political Identity
- Big Five trait scores
- Schwartz value ratings
- Qualitative description

## Part (F): Post-processing Cleaning

### Process Steps:
1. **Reading Raw Text**
   - Reads persona.txt containing multiple JSON objects

2. **Markup Removal**
   - Removes code blocks and formatting artifacts
   - Cleans triple backticks and language hints

3. **JSON Structure Validation**
   - Ensures proper array structure with `[` and `]`
   - Fixes object concatenation issues
   - Uses regex for structural corrections:
     - Replaces `"}\s*{"` with `"},\n{"`
     - Removes unnecessary commas
     - Eliminates trailing commas

4. **Error Handling**
   - Validates JSON structure with `json.loads()`
   - Provides diagnostic information for parsing errors

5. **Output**
   - Writes cleaned data to `cleaned_personas.json`