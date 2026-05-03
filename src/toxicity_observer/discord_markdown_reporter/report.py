import logging

import requests

from ..models import ToxicPost

logger = logging.getLogger(__name__)

DISCORD_MAX_LENGTH = 2000


class DiscordReporter:
    def __init__(self, config: dict):
        self._webhook_url = config.get("webhook_url")

    def report(self, results: dict[str, list[ToxicPost]]) -> None:
        """Send Discord reports for all apps. Sends a summary if nothing was toxic."""
        any_toxic = False
        for app_name, toxic_posts in results.items():
            if len(toxic_posts) > 0:
                any_toxic = True
                report = self._format_report(toxic_posts, app_name)
                self._send_report(report)

        if not any_toxic:
            self._send_report("**Scan Complete** — No toxic posts found.")

    def _format_report(self, toxic_posts: list[ToxicPost], app_name: str | None = None) -> str:
        """Format toxic posts into a Discord markdown summary."""
        header_lines = []
        if app_name:
            header_lines.append(f"# {app_name}")

        if len(toxic_posts) == 0:
            header_lines.append("**Scan Complete** — No posts found.")
            return "\n".join(header_lines)

        lines = header_lines + [f"**{len(toxic_posts)}** toxic post(s) found\n"]

        for entry in toxic_posts:
            content_preview = entry.message[:200]
            if len(entry.message) > 200:
                content_preview += "…"

            lines.append(
                f"**{entry.author_name}** — <t:{entry.timestamp}:f>\n"
                f"Toxicity Score: `{entry.toxicity_score:.2f}`\n"
                f"> {content_preview}\n"
                f"[Link]({entry.url})\n"
            )

        return "\n".join(lines)

    def _send_report(self, report: str) -> None:
        """Send a markdown report to the configured Discord webhook.

        Splits into multiple messages if the report exceeds Discord's 2000 char limit.
        """
        if not self._webhook_url:
            raise ValueError("Discord webhook URL not configured")

        chunks = _split_message(report, DISCORD_MAX_LENGTH)

        for chunk in chunks:
            try:
                resp = requests.post(self._webhook_url, json={"content": chunk, "flags": 4}, timeout=10)
                resp.raise_for_status()
            except requests.RequestException:
                logger.exception("Failed to send Discord webhook message")
                raise 


def _split_message(text: str, max_length: int) -> list[str]:
    """Split text into chunks of at most max_length, breaking at newlines."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks
