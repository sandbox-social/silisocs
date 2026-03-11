# scenarios/election/config_dataclasses.py

from dataclasses import dataclass, field

from mastodon_sim.utils.social_media_dataclasses import (
    SocialMediaUserParams,
)


@dataclass(frozen=True)
class AgentInputs:
    use_news_agent: str = "with_images"  # whether or not to include stored images
    news_file: str = "default_news.json"  # from where to get news headlines+images for news account
    persona_file: str = "personas.csv"  # from where do get persona data
    persona_type: str = "Reddit.Big5"  # label of persona data


# ============================================================================
# Agent Models
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
class SettingDetails:
    candidate_info: CandidatesInfo


@dataclass(frozen=True)
class InteractionPremiseTemplate:
    candidate: str | None = None
    candidate1: str | None = None
    candidate2: str | None = None
