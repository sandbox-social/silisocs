"""Unit tests for GameMasterFactory with flexible many-to-many routing."""

import pytest
from unittest.mock import MagicMock

from mastodon_sim.environments.gm.gm_factory import GameMasterFactory


class MockEntity:
    """Mock entity for testing."""

    def __init__(self, name: str):
        self.name = name


def test_factory_initialization():
    """Test factory initializes with correct attributes."""
    config = {"some_param": "value"}
    agents = ["alice", "bob"]
    factory = GameMasterFactory(config, agents)

    assert factory.agent_names == agents
    assert factory.gm_config == config
    # Default: all agents in "default" class
    assert factory.agent_to_classes == {"alice": ["default"], "bob": ["default"]}


def test_factory_with_single_agent_classes():
    """Test factory with single class per agent."""
    config = {}
    agents = ["alice", "bob", "charlie"]
    agent_to_classes = {
        "alice": "human",
        "bob": "bot",
        "charlie": "bot",
    }

    factory = GameMasterFactory(config, agents, agent_to_classes)

    # Single strings normalized to lists
    assert factory.agent_to_classes == {
        "alice": ["human"],
        "bob": ["bot"],
        "charlie": ["bot"],
    }


def test_factory_with_multiple_agent_classes():
    """Test factory with multiple classes per agent (full composability)."""
    config = {}
    agents = ["alice", "bob"]
    agent_to_classes = {
        "alice": ["human", "verified"],
        "bob": ["bot", "trusted"],
    }

    factory = GameMasterFactory(config, agents, agent_to_classes)

    assert factory.agent_to_classes == agent_to_classes


def test_factory_with_many_to_many_routing():
    """Test many-to-many: classes map to multiple GMs."""
    config = {
        "gm_configs": {
            "gm1": {"name": "gm1"},
            "gm2": {"name": "gm2"},
            "gm3": {"name": "gm3"},
        },
        "gm_sequence": ["gm1", "gm2", "gm3"],
    }
    agents = ["alice", "bob"]
    agent_to_classes = {
        "alice": ["human", "verified"],
        "bob": ["bot"],
    }
    class_to_gms = {
        "human": ["gm1", "gm2"],      # human class → 2 GMs
        "verified": ["gm2", "gm3"],   # verified class → 2 GMs
        "bot": ["gm3"],               # bot class → 1 GM
    }

    factory = GameMasterFactory(config, agents, agent_to_classes, class_to_gms)

    assert factory.class_to_gms == class_to_gms
    # Alice (human, verified) should be in gm1, gm2, gm3
    # Bob (bot) should be in gm3


def test_factory_class_to_gms_single_string():
    """Test class_to_gms with single string GM names normalized to lists."""
    config = {}
    agents = ["alice"]
    class_to_gms = {
        "human": "gm1",  # Single string
        "bot": "gm2",
    }

    factory = GameMasterFactory(config, agents, class_to_gms=class_to_gms)

    # Normalized to lists
    assert factory.class_to_gms == {
        "human": ["gm1"],
        "bot": ["gm2"],
    }


def test_factory_gm_sequence_order():
    """Test that gm_sequence controls execution order."""
    config = {
        "gm_sequence": ["gm3", "gm1", "gm2"],
        "gm_configs": {
            "gm1": {"name": "gm1"},
            "gm2": {"name": "gm2"},
            "gm3": {"name": "gm3"},
        },
    }
    agents = ["alice"]

    factory = GameMasterFactory(config, agents)

    # Verify gm_sequence is accessible
    assert factory.gm_config.get("gm_sequence") == ["gm3", "gm1", "gm2"]


def test_factory_unmapped_agent_defaults():
    """Test unmapped agents default to 'default' class."""
    config = {}
    agents = ["alice", "bob", "charlie"]
    agent_to_classes = {
        "alice": "human",
        # bob not mapped
        # charlie not mapped
    }

    factory = GameMasterFactory(config, agents, agent_to_classes)

    assert factory.agent_to_classes["alice"] == ["human"]
    assert factory.agent_to_classes["bob"] == ["default"]
    assert factory.agent_to_classes["charlie"] == ["default"]


def test_factory_infers_class_to_gms_from_agents():
    """Test factory infers class_to_gms from agent_to_classes when not provided."""
    config = {}
    agents = ["alice", "bob"]
    agent_to_classes = {
        "alice": ["human", "verified"],
        "bob": ["bot"],
    }

    factory = GameMasterFactory(config, agents, agent_to_classes)

    # Should create entries for all classes used
    assert "human" in factory.class_to_gms
    assert "verified" in factory.class_to_gms
    assert "bot" in factory.class_to_gms


def test_factory_advanced_mode_detection():
    """Test factory detects advanced multi-GM mode."""
    config = {
        "gm_configs": {
            "gm1": {"name": "gm1"},
            "gm2": {"name": "gm2"},
        },
        "gm_sequence": ["gm1", "gm2"],
    }
    agents = ["alice"]

    factory = GameMasterFactory(config, agents)

    # Has both gm_configs and gm_sequence == advanced mode
    assert factory.gm_config.get("gm_configs") is not None
    assert factory.gm_config.get("gm_sequence") is not None


def test_factory_legacy_fallback():
    """Test factory still recognizes legacy class_mapping format."""
    config = {
        "class_mapping": {
            "human": {"name": "human_gm"},
            "bot": {"name": "bot_gm"},
        },
    }
    agents = ["alice", "bob"]
    agent_to_classes = {
        "alice": "human",
        "bob": "bot",
    }

    factory = GameMasterFactory(config, agents, agent_to_classes)

    # Should have extracted class_mapping → class_to_gms
    assert factory.class_to_gms["human"] == ["human"]
    assert factory.class_to_gms["bot"] == ["bot"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

