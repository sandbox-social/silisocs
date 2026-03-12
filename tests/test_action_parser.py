from mastodon_sim.environments.gm_components.act import find_and_parse_action_data


def test_parse_post_with_empty_target_id() -> None:
    raw = """FINAL DECISION:
ACTION TYPE: POST
TARGET ID:
CONTENT: Hello world
REASONING: New post should not require target id.
"""
    parsed = find_and_parse_action_data(raw)
    assert parsed is not None
    assert parsed["action_type"] == "POST"
    assert parsed["target_id"] == ""
    assert parsed["content"] == "Hello world"


def test_parse_post_with_na_target_id() -> None:
    raw = """FINAL DECISION:
ACTION TYPE: POST
TARGET ID: N/A
CONTENT: Another standalone post.
REASONING: Posting does not reference an existing item.
"""
    parsed = find_and_parse_action_data(raw)
    assert parsed is not None
    assert parsed["action_type"] == "POST"
    assert parsed["target_id"] == ""


def test_parse_reply_extracts_numeric_target_id() -> None:
    raw = """FINAL DECISION:
ACTION TYPE: REPLY
TARGET ID: Tweet ID: 4650
CONTENT: Reply text
REASONING: Replying to a specific tweet.
"""
    parsed = find_and_parse_action_data(raw)
    assert parsed is not None
    assert parsed["action_type"] == "REPLY"
    assert parsed["target_id"] == "4650"
    assert parsed["content"] == "Reply text"
