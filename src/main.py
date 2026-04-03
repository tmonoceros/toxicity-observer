import json
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from steam_scraper import SteamScraper
from perspective import PerspectiveScorer
from discord_markdown_reporter import DiscordReporter
from models import ToxicPost

CONFIG_PATH = os.environ.get("TOXICITY_OBSERVER_CONFIG_PATH")

if not CONFIG_PATH or not os.path.exists(CONFIG_PATH):
    logging.error(f"No config file found at {CONFIG_PATH}")
    raise SystemExit(1)

if __name__ == "__main__":
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    scraper = SteamScraper(config["steam_scraper_config"])
    scorer = PerspectiveScorer(config["perspective_config"])
    reporter = DiscordReporter(config["discord_reporter_config"])

    apps = config["steam_scraper_config"].get("steam_apps", [])

    if (len(apps) == 0):
        logging.error("No steam apps configured in config file")
        raise SystemExit(2)

    results: dict[str, list[ToxicPost]] = {}

    for app in apps:
        app_id = app["appId"]
        app_name = app["name"]
        logging.info(f"Processing {app_name} (appId={app_id})")

        posts = scraper.scrape_app(app_id)
        logging.info(f"[{app_name}] Retrieved {len(posts)} comments.")

        toxic_posts = []
        for post in posts:
            result = scorer.score_message(post.content, url=post.url)
            if result and result.toxic:
                toxic_posts.append(ToxicPost(
                    author_name=post.author_name,
                    timestamp=post.timestamp,
                    url=result.url,
                    message=result.message,
                    toxicity_score=result.toxicity_score,
                ))

        logging.info(f"[{app_name}] Ignored {len(posts) - len(toxic_posts)} non-toxic posts, found {len(toxic_posts)} toxic posts.")
        results[app_name] = toxic_posts

    reporter.report(results)
