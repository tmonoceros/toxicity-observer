from dataclasses import asdict
from googleapiclient import discovery
from googleapiclient.errors import HttpError
from pathlib import Path
import json
import logging
import time

from ..models import ScoreResult

logger = logging.getLogger(__name__)

CACHE_PATH = Path("cache/perspective_cache.json")

class PerspectiveScorer:
    def __init__(self, config: dict):
        self._api_key = config.get("api_key")
        self._toxicity_threshold = config.get("toxicity_threshold", 0.5)
        self._cache = self._load_cache()

    def _load_cache(self) -> dict:
        """Load the perspective score cache from disk."""
        if CACHE_PATH.exists():
            logger.info("Loading Perspective cache from disk")
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Deserialize cached dicts back into ScoreResult objects (None stays None)
            return {k: ScoreResult(**v) if v is not None else None for k, v in raw.items()}
        logger.info("Initializing empty perspective cache")
        return {}

    def _save_cache(self) -> None:
        """Write the perspective score cache to disk."""
        serializable = {k: asdict(v) if v is not None else None for k, v in self._cache.items()}
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)

    def score_message(self, message: str, url: str = "") -> ScoreResult | None:
        """Score a message using the Perspective API. Returns a ScoreResult or None if unscorable."""
        if message in self._cache:
            logger.debug(f"Cache hit for comment: {message[:50]}...")
            return self._cache[message]

        if self._api_key is None:
            raise ValueError("Perspective API key not configured")
        client = discovery.build(
            "commentanalyzer",
            "v1alpha1",
            developerKey=self._api_key,
            discoveryServiceUrl="https://commentanalyzer.googleapis.com/$discovery/rest?version=v1alpha1",
            static_discovery=False,
            cache_discovery=False,
        ) 

        analyze_request = {
            'comment': {'text': message},
            'requestedAttributes': {'TOXICITY': {}},
            'doNotStore': True
        }

        # Perspective API quotas are per-minute
        delay = 60
        max_failures = 5
        failures = 0
        while True:
            try:
                response = client.comments().analyze(body=analyze_request).execute()
                break
            except HttpError as e:
                if e.resp.status == 400:
                    self._cache[message] = None
                    self._save_cache()
                    if "LANGUAGE_NOT_SUPPORTED_BY_ATTRIBUTE" in str(e):
                        link_info = f" ({url})" if url else ""
                        logger.warning(f"Skipping comment with unsupported language{link_info}")
                        return None
                    logger.error(f"Bad request to Perspective API for comment: {message[:50]}... URL: {url}")
                    return None
                if e.resp.status == 429:
                    failures += 1
                    if failures > max_failures:
                        raise
                    logger.warning(f"Perspective API rate limited, retrying in {delay}s (attempt {failures+1})")
                    time.sleep(delay)
                    continue
                raise

        score = response.get("attributeScores", {}).get("TOXICITY", {}).get("summaryScore", {}).get("value", -1)
        result = ScoreResult(
            toxic=score >= self._toxicity_threshold,
            message=message,
            url=url,
            toxicity_score=score,
        )
        self._cache[message] = result
        self._save_cache()
        return result
