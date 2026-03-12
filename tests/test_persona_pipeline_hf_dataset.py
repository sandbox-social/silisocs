"""Tests for hf_dataset persona pipeline loading in the generic agent builder."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from omegaconf import OmegaConf

from mastodon_sim.agents.builders import BaseAgentBuilder
from mastodon_sim.runtime.dataclasses import AgentConfig


class _TestBuilder(BaseAgentBuilder):
    """Concrete test builder; role-based building is unused in these tests."""

    def build_role_agents(self, role: str, count: int) -> list:
        return []


def test_hf_dataset_builds_expected_agent_params(monkeypatch) -> None:
    """Build from hf_dataset and verify mapped/normalized params in AgentConfig."""

    def fake_load_dataset(dataset: str, split: str):
        assert dataset == "Tianyi-Lab/Personas"
        assert split == "train"
        return [
            {
                "id": ["Ava", "Stone"],
                "persona": [
                    "Follows local politics closely.",
                    "Values practical policies.",
                ],
                "style": ["concise", "friendly"],
                "goal": ["Stay informed", "vote on election day"],
                "memories": ["Attended town hall", "Shared election resources"],
            }
        ]

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "defaults": {
                    "params": {
                        "election_info": "Storhampton election context",
                        "goal": "Fallback goal that should be overridden",
                    },
                    "shared_memories": ["Base shared memory"],
                },
                "classes": {
                    "voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "Tianyi-Lab/Personas",
                            "split": "train",
                        },
                        "field_map": {
                            "name": "id",
                            "context": "persona",
                            "style": "style",
                            "goal": "goal",
                        },
                        "specific_memories_field": "memories",
                        "shared_memories": "Class-specific shared memory",
                    }
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert len(agents) == 1
    built = agents[0]
    assert built.prefab == "simple__Entity"
    assert built.params["name"] == "Ava Stone"
    assert built.params["context"] == "Follows local politics closely.\nValues practical policies."
    assert built.params["style"] == "concise\nfriendly"
    assert built.params["goal"] == "Stay informed\nvote on election day"
    assert built.params["specific_memories"] == [
        "Attended town hall",
        "Shared election resources",
    ]
    assert built.params["sim_role"] == {
        "name": "voter",
        "module_path": "scenarios.election.entity_lib.simple",
    }
    assert built.params["election_info"] == "Storhampton election context"
    assert built.params["shared_memories"] == [
        "Base shared memory",
        "Class-specific shared memory",
    ]


def test_hf_dataset_preserves_explicit_specific_memories(monkeypatch) -> None:
    """Explicit specific_memories params take precedence over fallback record field."""

    def fake_load_dataset(dataset: str, split: str):
        assert dataset == "proj-persona/PersonaHub"
        assert split == "train"
        return [
            {
                "id": "user-001",
                "persona": "Pre-built persona text.",
                "memories": ["Record memory that should not replace explicit values"],
            }
        ]

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "classes": {
                    "voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "proj-persona/PersonaHub",
                            "split": "train",
                        },
                        "field_map": {
                            "name": "id",
                            "context": "persona",
                        },
                        "params": {
                            "specific_memories": ["Pinned explicit memory"],
                        },
                        "specific_memories_field": "memories",
                    }
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert len(agents) == 1
    assert agents[0].params["specific_memories"] == ["Pinned explicit memory"]


def test_field_map_template_combines_multiple_fields() -> None:
    """Template field maps can compose multiple source fields into one target."""
    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "classes": {
                    "voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "inline",
                            "records": [
                                {
                                    "id": "voter_1",
                                    "context_profile": "Enjoys civic discussions.",
                                    "profile_demographic": "Lives in Storhampton, age 34.",
                                }
                            ],
                        },
                        "field_map": {
                            "name": "id",
                            "context": "{context_profile}\n\n{profile_demographic}",
                        },
                    }
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert len(agents) == 1
    assert (
        agents[0].params["context"] == "Enjoys civic discussions.\n\nLives in Storhampton, age 34."
    )


def test_hf_dataset_persists_cache_and_derives_name_from_context(monkeypatch) -> None:
    """HF records are persisted to input cache and name can be derived from context."""

    def fake_load_dataset(dataset: str, split: str):
        assert dataset == "nvidia/Nemotron-Personas-USA"
        assert split == "train"
        return [
            {
                "persona": "Jordan Rivera is a civic-minded resident focused on local policy.",
            }
        ]

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "classes": {
                    "voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "nvidia/Nemotron-Personas-USA",
                            "split": "train",
                        },
                        "name_from_context_words": 2,
                        "field_map": {
                            "context": "persona",
                        },
                    }
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert len(agents) == 1
    assert agents[0].params["name"] == "Jordan Rivera"
    assert agents[0].params["context"].startswith("Jordan Rivera is a civic-minded")

    cache_path = Path(
        "src/mastodon_sim/scenarios/election/input/personas/.hf_cache/"
        "nvidia_Nemotron-Personas-USA__default__train.json"
    )
    assert cache_path.exists()
    with open(cache_path) as f:
        cached_records = json.load(f)
    assert isinstance(cached_records, list)
    assert cached_records[0]["persona"].startswith("Jordan Rivera is")


def test_hf_dataset_loads_nemotron_and_scope_formats(monkeypatch) -> None:
    """Both Nemotron and SCOPE hf_dataset classes load with expected formatting."""

    def fake_load_dataset(dataset: str, split: str):
        assert split == "train"
        if dataset == "nvidia/Nemotron-Personas-USA":
            return [
                {
                    "persona": (
                        "Taylor Brooks is a community volunteer and follows municipal policy debates."
                    ),
                }
            ]
        if dataset == "Salesforce/SCOPE-Persona":
            return [
                {
                    "demographic_information": "Age 42, lives in Storhampton.",
                    "personal_identity_and_life_narratives": "Former teacher, now runs a local nonprofit.",
                    "personality_traits": "Pragmatic, empathetic, detail-oriented.",
                    "sociodemographic_behavior": "Attends town halls and engages in civic groups.",
                }
            ]
        raise AssertionError(f"unexpected dataset: {dataset}")

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "classes": {
                    "nemotron_voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "nvidia/Nemotron-Personas-USA",
                            "split": "train",
                        },
                        "field_map": {
                            "context": "persona",
                        },
                    },
                    "scope_voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "Salesforce/SCOPE-Persona",
                            "split": "train",
                        },
                        "field_map": {
                            "context": (
                                "{demographic_information}\n\n"
                                "{personal_identity_and_life_narratives}\n\n"
                                "{personality_traits}\n\n"
                                "{sociodemographic_behavior}"
                            )
                        },
                    },
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert len(agents) == 2

    nemotron_agent = agents[0]
    assert nemotron_agent.params["name"] == "Taylor Brooks"
    assert nemotron_agent.params["context"].startswith("Taylor Brooks is a community volunteer")

    scope_agent = agents[1]
    assert scope_agent.params["name"] == "scope_voter_1"
    assert scope_agent.params["context"] == (
        "Age 42, lives in Storhampton.\n\n"
        "Former teacher, now runs a local nonprofit.\n\n"
        "Pragmatic, empathetic, detail-oriented.\n\n"
        "Attends town halls and engages in civic groups."
    )

    nemotron_cache = Path(
        "src/mastodon_sim/scenarios/election/input/personas/.hf_cache/"
        "nvidia_Nemotron-Personas-USA__default__train.json"
    )
    scope_cache = Path(
        "src/mastodon_sim/scenarios/election/input/personas/.hf_cache/"
        "Salesforce_SCOPE-Persona__default__train.json"
    )
    assert nemotron_cache.exists()
    assert scope_cache.exists()


def test_shared_memories_inline_multiline_text_is_not_treated_as_path() -> None:
    """Long multiline shared memories should be parsed as text, not file paths."""
    shared_memory_block = (
        "Storhampton Mayoral Election Campaign: Bill Fredrickson campaigns on tax breaks.\n"
        "Bradley Carter campaigns on environmental regulation.\n"
        "The election has become heated on local social media.\n"
    )
    expected_shared_memories = [line.strip() for line in shared_memory_block.splitlines() if line]

    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "defaults": {
                    "shared_memories": [shared_memory_block],
                },
                "classes": {
                    "voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "inline",
                            "records": [{"name": "Alex", "persona": "Cares about local policy."}],
                        },
                        "field_map": {
                            "name": "name",
                            "context": "persona",
                        },
                    }
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert len(agents) == 1
    assert agents[0].params["shared_memories"] == expected_shared_memories


def test_field_map_case_mismatch_still_resolves_context() -> None:
    """Case-only key mismatch in field_map should still resolve source values."""
    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "classes": {
                    "voter": {
                        "count": 1,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "inline",
                            "records": [
                                {"persona": "Local resident who follows city council updates."}
                            ],
                        },
                        "field_map": {
                            "context": "Persona",
                        },
                    }
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert len(agents) == 1
    assert agents[0].params["context"] == "Local resident who follows city council updates."


def test_class_pipeline_duplicate_names_are_skipped() -> None:
    """Class pipeline should skip duplicate names before simulation instantiation."""
    scenario_cfg = OmegaConf.create(
        {
            "scenario_name": "election",
            "persona_pipeline": {
                "processing_mode": "raw",
                "classes": {
                    "voter": {
                        "count": 3,
                        "prefab_module": "scenarios.election.entity_lib.simple",
                        "data": {
                            "source": "inline",
                            "records": [
                                {"name": "Alex Kim", "persona": "First record."},
                                {
                                    "name": "Alex Kim",
                                    "persona": "Duplicate name should be skipped.",
                                },
                                {"name": "Jordan Lee", "persona": "Unique record."},
                            ],
                        },
                        "field_map": {"name": "name", "context": "persona"},
                    }
                },
            },
        }
    )

    builder = _TestBuilder(scenario_cfg)
    agents = builder.build_agents({})

    assert [a.params["name"] for a in agents] == ["Alex Kim", "Jordan Lee"]
    assert len(agents) == 2


def test_role_based_duplicate_names_are_skipped() -> None:
    """Legacy role-based builder path should also skip duplicate names."""

    class _RoleBuilder(BaseAgentBuilder):
        def build_role_agents(self, role: str, count: int) -> list[AgentConfig]:
            if role == "voter":
                return [
                    AgentConfig(
                        prefab="simple__Entity",
                        params={
                            "name": "Chris Doe",
                            "context": "Voter context",
                            "sim_role": {
                                "name": "voter",
                                "module_path": "scenarios.election.entity_lib.simple",
                            },
                            "style": "",
                            "goal": None,
                        },
                    )
                ]
            if role == "candidate":
                return [
                    AgentConfig(
                        prefab="simple__Entity",
                        params={
                            "name": "Chris Doe",
                            "context": "Candidate context",
                            "sim_role": {
                                "name": "candidate",
                                "module_path": "scenarios.election.entity_lib.simple",
                            },
                            "style": "",
                            "goal": None,
                        },
                    )
                ]
            return []

    builder = _RoleBuilder(OmegaConf.create({"scenario_name": "election"}))
    agents = builder.build_agents({"voter": 1, "candidate": 1})

    assert len(agents) == 1
    assert agents[0].params["name"] == "Chris Doe"
    assert agents[0].params["sim_role"]["name"] == "voter"
