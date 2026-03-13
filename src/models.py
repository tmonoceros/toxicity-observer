from dataclasses import dataclass


@dataclass
class SteamPost:
    author_name: str
    author_profile: str
    timestamp: int
    content: str
    url: str
    title: str | None


@dataclass
class ScoreResult:
    toxic: bool
    message: str
    url: str
    toxicity_score: float


@dataclass
class ToxicPost:
    """Combined post + score data for a toxic post, used in reporting."""
    author_name: str
    timestamp: int
    url: str
    message: str
    toxicity_score: float
