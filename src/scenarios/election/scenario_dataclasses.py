# scenarios/election/config_dataclasses.py

from dataclasses import dataclass, field

from sim.config_utils.social_media_dataclasses import (
    SocialMediaUserParams,
)

# ============================================================================
# Agent Parameter Classes
# ============================================================================


@dataclass(frozen=True)
class VoterParams(SocialMediaUserParams):
    election_info: str


@dataclass(frozen=True)
class NewsAccountParams(SocialMediaUserParams):
    posts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateInfo:
    name: str
    policy_proposals: str


@dataclass(frozen=True)
class CandidatesInfo:
    conservative: CandidateInfo
    progressive: CandidateInfo


@dataclass(frozen=True)
class InteractionPremiseTemplate:
    candidate: str | None = None
    candidate1: str | None = None
    candidate2: str | None = None


@dataclass(frozen=True)
class AgentInputs:
    use_news_agent: str = "with_images"
    news_file: str = "default_news.json"
    persona_file: str = "personas.csv"
    persona_type: str = "Reddit.Big5"


@dataclass(frozen=True)
class SettingDetails:
    candidate_info: CandidatesInfo
