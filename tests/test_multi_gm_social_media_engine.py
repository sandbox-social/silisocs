"""Unit tests for MultiGMSocialMediaEngine."""

import pytest
from unittest.mock import MagicMock, patch
from omegaconf import OmegaConf

from mastodon_sim.environments.engines.multi_gm_social_media import MultiGMSocialMediaEngine


class MockGameMaster:
    """Mock game master for testing."""

    def __init__(self, name: str):
        self.name = name


class TestMultiGMSocialMediaEngineInit:
    """Test MultiGMSocialMediaEngine initialization."""

    def test_engine_initializes(self):
        """Test engine can be initialized."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            assert engine._gm_sequence is None
            assert engine._gm_instances == {}
            assert engine._agent_to_gm_map == {}

    def test_engine_has_required_methods(self):
        """Test engine has all required methods."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            assert hasattr(engine, 'get_agent_gms')
            assert hasattr(engine, 'detect_gm_conflicts')
            assert hasattr(engine, 'log_orchestration_info')
            assert hasattr(engine, 'validate_gm_sequence')


class TestAgentToGMMapping:
    """Test agent-to-GM mapping functionality."""

    def test_build_simple_mapping(self):
        """Test building agent to GM mapping with single class."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()

            gm_config = OmegaConf.create({
                "agent_classes": {
                    "alice": "human",
                    "bob": "bot",
                },
                "class_to_gms": {
                    "human": "gm1",
                    "bot": "gm2",
                }
            })

            engine._build_agent_to_gm_mapping(None, gm_config)

            assert engine._agent_to_gm_map["alice"] == ["gm1"]
            assert engine._agent_to_gm_map["bob"] == ["gm2"]

    def test_build_mapping_with_multiple_classes(self):
        """Test building mapping when agent has multiple classes."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()

            gm_config = OmegaConf.create({
                "agent_classes": {
                    "alice": ["human", "verified"],
                    "bob": "bot",
                },
                "class_to_gms": {
                    "human": ["gm1", "gm2"],
                    "verified": ["gm2", "gm3"],
                    "bot": ["gm4"],
                }
            })

            engine._build_agent_to_gm_mapping(None, gm_config)

            # alice should be in gm1, gm2, gm3 (union of human + verified)
            assert sorted(engine._agent_to_gm_map["alice"]) == ["gm1", "gm2", "gm3"]
            assert engine._agent_to_gm_map["bob"] == ["gm4"]

    def test_build_mapping_with_gm_lists(self):
        """Test building mapping when classes route to multiple GMs."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()

            gm_config = OmegaConf.create({
                "agent_classes": {
                    "alice": ["human"],
                    "bob": ["bot"],
                },
                "class_to_gms": {
                    "human": ["gm1", "gm2", "gm3"],
                    "bot": ["gm4"],
                }
            })

            engine._build_agent_to_gm_mapping(None, gm_config)

            assert sorted(engine._agent_to_gm_map["alice"]) == ["gm1", "gm2", "gm3"]
            assert engine._agent_to_gm_map["bob"] == ["gm4"]

    def test_get_agent_gms(self):
        """Test getting GMs for a specific agent."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._agent_to_gm_map = {
                "alice": ["gm1", "gm2"],
                "bob": ["gm3"],
            }

            assert engine.get_agent_gms("alice") == ["gm1", "gm2"]
            assert engine.get_agent_gms("bob") == ["gm3"]
            assert engine.get_agent_gms("charlie") == []

    def test_detect_no_conflicts(self):
        """Test detecting when no agent conflicts exist."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._agent_to_gm_map = {
                "alice": ["gm1"],
                "bob": ["gm2"],
            }

            conflicts = engine.detect_gm_conflicts()
            assert conflicts == {}

    def test_detect_conflicts(self):
        """Test detecting when agents are in multiple GMs."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._agent_to_gm_map = {
                "alice": ["gm1", "gm2"],  # Conflict
                "bob": ["gm2"],
                "charlie": ["gm1", "gm2", "gm3"],  # Conflict
            }

            conflicts = engine.detect_gm_conflicts()
            assert "alice" in conflicts
            assert "charlie" in conflicts
            assert "bob" not in conflicts
            assert conflicts["alice"] == ["gm1", "gm2"]
            assert conflicts["charlie"] == ["gm1", "gm2", "gm3"]

    def test_no_duplicates_in_gm_mapping(self):
        """Test that duplicate GMs are removed from agent's GM list."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()

            gm_config = OmegaConf.create({
                "agent_classes": {
                    "alice": ["human", "verified"],
                },
                "class_to_gms": {
                    "human": ["gm1", "gm2"],
                    "verified": ["gm2", "gm3"],  # gm2 appears in both
                }
            })

            engine._build_agent_to_gm_mapping(None, gm_config)

            # gm2 should only appear once
            assert engine._agent_to_gm_map["alice"] == ["gm1", "gm2", "gm3"]


class TestGMSequenceValidation:
    """Test GM sequence validation."""

    def test_validate_none_sequence(self):
        """Test validating None sequence (single-GM mode)."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._gm_sequence = None

            assert engine.validate_gm_sequence() is True

    def test_validate_empty_sequence(self):
        """Test validating empty sequence."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._gm_sequence = []

            assert engine.validate_gm_sequence() is False

    def test_validate_valid_sequence(self):
        """Test validating valid sequence."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._gm_sequence = ["gm1", "gm2", "gm3"]

            assert engine.validate_gm_sequence() is True

    def test_validate_invalid_type(self):
        """Test validating invalid sequence type."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._gm_sequence = "gm1,gm2"  # Should be list, not string

            assert engine.validate_gm_sequence() is False


class TestOrchestrationLogging:
    """Test orchestration logging."""

    def test_log_single_gm_mode(self):
        """Test logging in single-GM mode."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._gm_sequence = None
            engine._agent_to_gm_map = {}

            # Should not raise
            engine.log_orchestration_info()

    def test_log_multi_gm_mode(self):
        """Test logging in multi-GM mode."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._gm_sequence = ["gm1", "gm2"]
            engine._agent_to_gm_map = {
                "alice": ["gm1"],
                "bob": ["gm2"],
                "charlie": ["gm1", "gm2"],
            }

            # Should not raise
            engine.log_orchestration_info()

    def test_log_with_conflicts(self):
        """Test logging when agents are in multiple GMs."""
        with patch('mastodon_sim.environments.engines.social_media.FlowSocialMediaEngine.__init__', return_value=None):
            engine = MultiGMSocialMediaEngine()
            engine._gm_sequence = ["gm1", "gm2", "gm3"]
            engine._agent_to_gm_map = {
                "alice": ["gm1", "gm2"],
                "bob": ["gm2", "gm3"],
            }

            # Should not raise, should log warnings
            engine.log_orchestration_info()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
