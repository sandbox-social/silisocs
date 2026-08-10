"""specs module. Auto-generated module docstring."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# ScenarioSpec — shared social world
# ---------------------------------------------------------------------------


class Setting(BaseModel):
    """Setting."""

    name: str
    background: list[str]


class Event(BaseModel):
    """Event."""

    name: str
    context: str


class AgentDetails(BaseModel):
    """AgentDetails."""

    name: str
    username: str
    context: str
    style: str
    goal: str
    seed_post: str = ""


class ActivityConfig(BaseModel):
    """ActivityConfig."""

    inactive_to_active: float = 0.7
    active_to_inactive: float = 0.2


class AgentClass(BaseModel):
    """AgentClass."""

    role: str
    sim_role_name: str
    class_path: str = "silisocs.agents.native.NativeAgent"
    count: int
    agents: list[AgentDetails]
    activity: ActivityConfig = Field(default_factory=ActivityConfig)


class NetworkConfig(BaseModel):
    """NetworkConfig."""

    fully_connected_targets: list[str] = Field(default_factory=list)
    base_followership_probability: float = 0.5
    network_type: Literal["barabasi_albert", "fully_connected"] = "barabasi_albert"
    barabasi_albert_m: int = 2


class BackendConfig(BaseModel):
    """BackendConfig."""

    type: str = "twitter_like"
    timeline_mode: Literal["follower_chronological", "pure_recsys"] = "follower_chronological"
    enabled_actions: list[str] = Field(
        default_factory=lambda: [
            "create_tweet",
            "reply_to_tweet",
            "like_tweet",
            "repost_tweet",
            "FINISHED",
        ]
    )


class ProbeDeployment(BaseModel):
    """Per-probe deployment overrides (mirrors ``eval.probes.deployment``).

    Every field is optional; only the ones set are emitted, so an omitted field
    inherits the scenario's global ``eval.probes.deployment`` block exactly as
    the probe deployer resolves it.
    """

    enabled: bool | None = None
    start_step: int | None = None
    every_n_steps: int | None = None
    include_agents: list[str] | None = None
    exclude_agents: list[str] | None = None
    include_flows: list[str] | None = None
    exclude_flows: list[str] | None = None


class ProbeSpec(BaseModel):
    """One measurement instrument, rendered into ``eval.probes.probes.<id>``.

    Mirrors the shipped scenarios' ``conf/eval.yaml`` probe shape: an id key, a
    ``probe_type``, and a ``probe_data`` block carrying the question the agent
    is asked. ``lo``/``hi`` are the rating bounds ``NumericRatingProbe`` reads
    (and its ``{lo}``/``{hi}`` template slots); they are omitted when unset.
    """

    id: str
    probe_type: str = "FreeTextProbe"
    name: str = ""
    question: str
    context: str = ""
    lo: int | None = None
    hi: int | None = None
    deployment: ProbeDeployment | None = None


class ScenarioSpec(BaseModel):
    """ScenarioSpec."""

    name: str
    scenario_name: str
    jobname_format: str
    num_agents: int
    num_steps: int
    seed: int = 42
    run_name: str
    setting: Setting
    event: Event
    data: dict[str, Any] = Field(default_factory=dict)
    agent_classes: list[AgentClass]
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    custom_agent_stubs: list[str] = Field(default_factory=list)
    # Optional: absent (the default) writes an empty `eval.probes.probes` block,
    # exactly as before. Declaring probes here is the only scaffold path to the
    # measurement instrument.
    probes: list[ProbeSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# StudySpec — research question on top of a world
# ---------------------------------------------------------------------------


class StudyNotes(BaseModel):
    """StudyNotes."""

    objective: str = ""
    context: str = ""
    constraints: str = ""


class RunDefaults(BaseModel):
    """RunDefaults."""

    config_path: str
    run_name_template: str = "{study_id}_{hypothesis_id}_{condition_id}_{scenario}_seed{seed}"
    output_root_override: str = "experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run"
    seed_start: int = 42
    seed_repeats: int = 3
    overrides: dict[str, Any] = Field(default_factory=dict)


class Evaluation(BaseModel):
    """Evaluation."""

    id: str
    preset: str


class Condition(BaseModel):
    """Condition."""

    id: str
    sub_experiment: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["run", "reuse_existing"] = "run"
    reuse_source: str | None = None


class Hypothesis(BaseModel):
    """Hypothesis."""

    id: str
    statement: str
    independent_variable: str
    prediction: str
    status: Literal["testing", "supported", "refuted", "inconclusive"] = "testing"
    follows_from: str | None = None
    motivation: str | None = None
    conditions: list[Condition]


class StudySpec(BaseModel):
    """StudySpec."""

    study_id: str
    name: str
    question: str
    scenarios: list[str]
    notes: StudyNotes = Field(default_factory=StudyNotes)
    run_defaults: RunDefaults
    evaluations: list[Evaluation] = Field(default_factory=list)
    hypotheses: list[Hypothesis]
    generate_notebook: bool = False
