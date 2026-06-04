"""Tests for hf_dataset persona pipeline loading in the generic agent builder."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from silisocs.runtime.construction.agent_builders import AgentBuilder, PersonaPipelineAgentBuilder
from silisocs.runtime.construction.agent_configs import build_agent_configs
from silisocs.runtime.construction.specs import AgentConfig


class _TestBuilder(PersonaPipelineAgentBuilder):
    """Concrete test builder for persona-pipeline tests."""


class _ExplicitCustomBuilder(AgentBuilder):
    """Tiny importable custom builder for explicit class_path tests."""

    def build_agent_configs(self) -> list[AgentConfig]:
        return [
            AgentConfig(
                class_path="silisocs.agents.native.NativeAgent",
                params={
                    "name": str(self.params["name"]),
                    "context": "Built by explicit custom builder.",
                },
            )
        ]


class _DuplicateCustomBuilder(AgentBuilder):
    """Custom builder that violates the unique-name contract."""

    def build_agent_configs(self) -> list[AgentConfig]:
        return [
            AgentConfig(
                class_path="silisocs.agents.native.NativeAgent",
                params={"name": "Duplicate", "context": "First."},
            ),
            AgentConfig(
                class_path="silisocs.agents.native.NativeAgent",
                params={"name": "Duplicate", "context": "Second."},
            ),
        ]


class _MissingNameCustomBuilder(AgentBuilder):
    """Custom builder that violates the explicit-name contract."""

    def build_agent_configs(self) -> list[AgentConfig]:
        return [
            AgentConfig(
                class_path="silisocs.agents.native.NativeAgent",
                params={"context": "No runtime identity."},
            )
        ]


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

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
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
                        "class_path": "silisocs.agents.native.NativeAgent",
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

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 1
    built = agents[0]
    assert built.class_path == "silisocs.agents.native.NativeAgent"
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
        "module_path": "silisocs.agents.native.NativeAgent",
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

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
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

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 1
    assert agents[0].params["specific_memories"] == ["Pinned explicit memory"]


def test_field_map_template_combines_multiple_fields() -> None:
    """Template field maps can compose multiple source fields into one target."""
    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
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

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 1
    assert (
        agents[0].params["context"] == "Enjoys civic discussions.\n\nLives in Storhampton, age 34."
    )


def test_nemotron_hf_dataset_derives_name_from_persona(monkeypatch) -> None:
    """The default builder preserves Nemotron-specific name derivation."""

    def fake_load_dataset(dataset: str, split: str):
        assert dataset == "nvidia/Nemotron-Personas-USA"
        assert split == "train"
        return [
            {
                "persona": "Jordan Rivera is a civic-minded resident focused on local policy.",
            }
        ]

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "nvidia/Nemotron-Personas-USA",
                            "split": "train",
                        },
                        "field_map": {
                            "context": "persona",
                        },
                    }
                },
            },
        }
    )

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 1
    assert agents[0].params["name"] == "Jordan Rivera"
    assert agents[0].params["context"] == (
        "Jordan Rivera is a civic-minded resident focused on local policy."
    )


def test_unknown_hf_dataset_requires_mapped_or_derived_name(monkeypatch) -> None:
    """Other HF datasets fail unless they map or explicitly derive a name."""

    def fake_load_dataset(dataset: str, split: str):
        assert dataset == "example/UnnamedPersonas"
        assert split == "train"
        return [{"persona": "Jordan Rivera is a civic-minded resident."}]

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "example/UnnamedPersonas",
                            "split": "train",
                        },
                        "field_map": {
                            "context": "persona",
                        },
                    }
                },
            },
        }
    )

    with pytest.raises(ValueError, match="missing `name`"):
        _TestBuilder(world_cfg).build_agent_configs()


def test_explicit_name_derivation_option_for_inline_records() -> None:
    """Scenario builders can request name derivation as intentional builder logic."""
    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "derive_name_from_context": True,
                        "name_from_context_words": 3,
                        "data": {
                            "source": "inline",
                            "records": [
                                {
                                    "persona": "Jordan Rivera volunteers at the local library.",
                                }
                            ],
                        },
                        "field_map": {
                            "context": "persona",
                        },
                    }
                },
            },
        }
    )

    agents = _TestBuilder(world_cfg).build_agent_configs()

    assert len(agents) == 1
    assert agents[0].params["name"] == "Jordan Rivera volunteers"


def test_empty_mapped_name_fails_loudly() -> None:
    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "inline",
                            "records": [{"name": "  ", "persona": "Has context."}],
                        },
                        "field_map": {"name": "name", "context": "persona"},
                    }
                },
            },
        }
    )

    with pytest.raises(ValueError, match="missing `name`"):
        _TestBuilder(world_cfg).build_agent_configs()


def test_hf_dataset_loads_nemotron_and_scope_formats(monkeypatch) -> None:
    """Both Nemotron and SCOPE hf_dataset classes load with expected formatting."""

    def fake_load_dataset(dataset: str, split: str):
        assert split == "train"
        if dataset == "nvidia/Nemotron-Personas-USA":
            return [
                {
                    "name": "Taylor Brooks",
                    "persona": (
                        "Taylor Brooks is a community volunteer and follows municipal policy debates."
                    ),
                }
            ]
        if dataset == "Salesforce/SCOPE-Persona":
            return [
                {
                    "name": "Maya Patel",
                    "demographic_information": "Age 42, lives in Storhampton.",
                    "personal_identity_and_life_narratives": "Former teacher, now runs a local nonprofit.",
                    "personality_traits": "Pragmatic, empathetic, detail-oriented.",
                    "sociodemographic_behavior": "Attends town halls and engages in civic groups.",
                }
            ]
        raise AssertionError(f"unexpected dataset: {dataset}")

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "nemotron_voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "nvidia/Nemotron-Personas-USA",
                            "split": "train",
                        },
                        "field_map": {
                            "name": "name",
                            "context": "persona",
                        },
                    },
                    "scope_voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "Salesforce/SCOPE-Persona",
                            "split": "train",
                        },
                        "field_map": {
                            "name": "name",
                            "context": (
                                "{demographic_information}\n\n"
                                "{personal_identity_and_life_narratives}\n\n"
                                "{personality_traits}\n\n"
                                "{sociodemographic_behavior}"
                            ),
                        },
                    },
                },
            },
        }
    )

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 2

    nemotron_agent = agents[0]
    assert nemotron_agent.params["name"] == "Taylor Brooks"
    assert nemotron_agent.params["context"].startswith("Taylor Brooks is a community volunteer")

    scope_agent = agents[1]
    assert scope_agent.params["name"] == "Maya Patel"
    assert scope_agent.params["context"] == (
        "Age 42, lives in Storhampton.\n\n"
        "Former teacher, now runs a local nonprofit.\n\n"
        "Pragmatic, empathetic, detail-oriented.\n\n"
        "Attends town halls and engages in civic groups."
    )


def test_hf_dataset_materializes_only_requested_count(monkeypatch) -> None:
    """HF loading should only materialize class count rows, not full split."""

    class _FakeDataset:
        def __init__(self, size: int):
            self.size = size
            self.selected_n: int | None = None

        def __len__(self) -> int:
            return self.size

        def select(self, indices):
            # Capture requested select size; return only selected rows.
            rows = list(indices)
            self.selected_n = len(rows)
            return [{"name": f"Person {i}", "persona": f"Person {i} from dataset"} for i in rows]

    fake_dataset = _FakeDataset(size=1_000_000)

    def fake_load_dataset(dataset: str, split: str):
        assert dataset == "nvidia/Nemotron-Personas-USA"
        assert split == "train"
        return fake_dataset

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=fake_load_dataset))

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 3,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "hf_dataset",
                            "dataset": "nvidia/Nemotron-Personas-USA",
                            "split": "train",
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

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 3
    assert fake_dataset.selected_n == 3


def test_jsonl_source_builds_agents_with_name_and_context(tmp_path: Path) -> None:
    """JSONL persona source should map record fields into agent params."""
    jsonl_path = tmp_path / "test_personas.jsonl"
    jsonl_path.write_text(
        '{"name":"Alex Kim","persona":"Cares about local policy."}\n'
        '{"name":"Jordan Lee","persona":"Prefers pragmatic solutions."}\n',
        encoding="utf-8",
    )

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 2,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "jsonl",
                            "path": str(jsonl_path),
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

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 2
    assert agents[0].params["name"] == "Alex Kim"
    assert agents[0].params["context"] == "Cares about local policy."
    assert agents[1].params["name"] == "Jordan Lee"
    assert agents[1].params["context"] == "Prefers pragmatic solutions."


def test_shared_memories_inline_multiline_text_is_not_treated_as_path() -> None:
    """Long multiline shared memories should be parsed as text, not file paths."""
    shared_memory_block = (
        "Storhampton Mayoral Election Campaign: Bill Fredrickson campaigns on tax breaks.\n"
        "Bradley Carter campaigns on environmental regulation.\n"
        "The election has become heated on local social media.\n"
    )
    expected_shared_memories = [line.strip() for line in shared_memory_block.splitlines() if line]

    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "defaults": {
                    "shared_memories": [shared_memory_block],
                },
                "classes": {
                    "voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
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

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 1
    assert agents[0].params["shared_memories"] == expected_shared_memories


def test_field_map_case_mismatch_still_resolves_context() -> None:
    """Case-only key mismatch in field_map should still resolve source values."""
    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 1,
                        "class_path": "silisocs.agents.native.NativeAgent",
                        "data": {
                            "source": "inline",
                            "records": [
                                {
                                    "name": "Morgan",
                                    "persona": "Local resident who follows city council updates.",
                                }
                            ],
                        },
                        "field_map": {
                            "name": "Name",
                            "context": "Persona",
                        },
                    }
                },
            },
        }
    )

    builder = _TestBuilder(world_cfg)
    agents = builder.build_agent_configs()

    assert len(agents) == 1
    assert agents[0].params["context"] == "Local resident who follows city council updates."


def test_class_pipeline_duplicate_names_fail_loudly() -> None:
    """Class pipeline should reject duplicate runtime agent names."""
    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "persona_pipeline": {
                "classes": {
                    "voter": {
                        "count": 3,
                        "class_path": "silisocs.agents.native.NativeAgent",
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

    builder = _TestBuilder(world_cfg)
    with pytest.raises(ValueError, match="duplicate names: Alex Kim"):
        builder.build_agent_configs()


def test_agent_builder_contract_allows_custom_subclass() -> None:
    """Custom builders are a config-to-AgentConfig translator, not an object factory."""

    class _CustomBuilder(AgentBuilder):
        def build_agent_configs(self) -> list[AgentConfig]:
            return [
                AgentConfig(
                    class_path="silisocs.agents.native.NativeAgent",
                    params={"name": "Moderator", "context": "Keeps the forum grounded."},
                )
            ]

    agents = _CustomBuilder(OmegaConf.create({})).build_agent_configs()

    assert len(agents) == 1
    assert agents[0].params["name"] == "Moderator"


def test_explicit_builder_class_path_is_used() -> None:
    """Runtime construction uses only agents.builder.class_path for custom builders."""
    cfg = OmegaConf.create(
        {
            "world_name": "election",
            "agents": {
                "builder": {
                    "class_path": f"{__name__}._ExplicitCustomBuilder",
                    "params": {"name": "Custom Builder Agent"},
                }
            },
        }
    )

    agents = build_agent_configs(cfg)

    assert [agent.params["name"] for agent in agents] == ["Custom Builder Agent"]


@pytest.mark.parametrize(
    ("builder_class", "message"),
    [
        ("_DuplicateCustomBuilder", "duplicate names: Duplicate"),
        ("_MissingNameCustomBuilder", "missing names"),
    ],
)
def test_custom_builder_outputs_must_have_unique_names(
    builder_class: str,
    message: str,
) -> None:
    cfg = OmegaConf.create(
        {
            "world_name": "election",
            "agents": {
                "builder": {
                    "class_path": f"{__name__}.{builder_class}",
                    "params": {},
                }
            },
        }
    )

    with pytest.raises(ValueError, match=message):
        build_agent_configs(cfg)


def test_builder_params_cannot_override_reserved_runtime_values() -> None:
    """Runtime-supplied builder params are explicit and protected."""
    cfg = OmegaConf.create(
        {
            "world_name": "election",
            "agents": {
                "builder": {
                    "class_path": f"{__name__}._ExplicitCustomBuilder",
                    "params": {"world_name": "wrong"},
                }
            },
        }
    )

    with pytest.raises(ValueError, match="reserved runtime param"):
        build_agent_configs(cfg)


def test_old_agent_builder_import_path_is_absent() -> None:
    """The builder extension surface lives under runtime construction."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("silisocs.agents.builders")


def test_runtime_builder_selection_has_no_world_name_auto_detection() -> None:
    """Custom builders must be selected explicitly with agents.builder.class_path."""
    from silisocs.runtime.construction import agent_configs

    source = Path(agent_configs.__file__).read_text(encoding="utf-8")
    assert "spec_from_file_location" not in source
    assert "worlds.<name>.builders" not in source


def test_fixed_action_set_renders_into_fixed_agent_plan() -> None:
    """Fixed-action helper rendering remains available through the builder facade."""
    world_cfg = OmegaConf.create(
        {
            "world_name": "election",
            "fixed_action_sets": {
                "inline": {
                    "news_actions": {
                        "actions": [
                            {
                                "action": "create_tweet",
                                "args": {"status": "Breaking: {topic}"},
                            }
                        ]
                    }
                }
            },
            "persona_pipeline": {
                "classes": {
                    "news_bot": {
                        "count": 1,
                        "class_path": "silisocs.agents.fixed.FixedAgent",
                        "data": {
                            "source": "inline",
                            "records": [
                                {
                                    "name": "News Bot",
                                    "persona": "Posts verified updates.",
                                    "topic": "river cleanup",
                                }
                            ],
                        },
                        "field_map": {"name": "name", "context": "persona"},
                        "fixed_action": {
                            "enabled": True,
                            "action_set_ref": "news_actions",
                            "on_exhaustion": "finish",
                        },
                    }
                }
            },
        }
    )

    agents = _TestBuilder(world_cfg).build_agent_configs()

    assert agents[0].params["fixed_action_plan"] == {
        0: [
            {
                "action_type": "create_tweet",
                "target_id": "",
                "content": "Breaking: river cleanup",
                "reasoning": "Fixed action set item.",
                "tool_kwargs": {"status": "Breaking: river cleanup"},
            }
        ]
    }
    assert agents[0].params["emit_finished_on_episode_end"] is True
