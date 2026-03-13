import logging
import re
import time
import html
from datetime import datetime, timezone, timedelta
from pathlib import Path
from copy import copy

import requests
from bs4 import BeautifulSoup
import os

from models import SteamPost

logger = logging.getLogger(__name__)

STEAM_COMMUNITY = "https://steamcommunity.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
}
REQUEST_DELAY = 1.0  # seconds between requests


class SteamScraper:
    def __init__(self, config: dict):
        self._use_cache = config.get("development_web_cache", False)
        self._cutoff_hours = config.get("cutoff_hours", 24)
        if (self._use_cache and Path("cache/web_cache").exists()):
            logger.info(f"Development web cache enabled and found")
        elif self._use_cache:
            logger.info(f"Initialized empty development web cache")
        else: 
            logger.info(f"Development web cache disabled, live Steam requests only")

    def _get(self, url: str) -> BeautifulSoup:
        """Fetch a URL and return parsed soup."""
        os.makedirs("cache/web_cache", exist_ok=True)
        cache_path = Path("cache/web_cache") / (re.sub(r"[^a-zA-Z0-9]", "_", url) + ".html")
        if self._use_cache and cache_path.exists():
            with open(cache_path, "r", encoding="utf-8") as f:
                return BeautifulSoup(f.read(), "lxml")
        time.sleep(REQUEST_DELAY)
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        return BeautifulSoup(resp.text, "lxml")

    def scrape_app(self, app_id: str) -> list[SteamPost]:
        """Main entry point: scrape all recent posts for a Steam app."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self._cutoff_hours)
        logger.debug(f"Scraping Steam app {app_id}")
        logger.debug(f"Cutoff time: {cutoff.isoformat()}")

        subforums = self._discover_subforums(app_id)
        all_posts = []

        for sf in subforums:
            logger.debug(f"Processing subforum: {sf['url']}")
            threads = self._scan_threads(sf["url"], cutoff)

            for thread in threads:
                logger.debug(f"Extracting posts from: {thread['url']}")
                posts = self._extract_posts(thread["url"], cutoff)
                all_posts.extend(posts)
                logger.debug(f"Found {len(posts)} recent posts")

        logger.debug(f"Total posts collected: {len(all_posts)}")
        return [SteamPost(**post) for post in all_posts]

    def _discover_subforums(self, app_id: str) -> list[dict]:
        """Discover all subforums for an app."""
        url = f"{STEAM_COMMUNITY}/app/{app_id}/discussions/"
        logger.debug(f"Discovering subforums: {url}")
        soup = self._get(url)

        subforums = []
        forum_list = soup.select("div.rightbox.forum_list .rightbox_list_option")
        for option in forum_list:
            link = option.select_one(".forum_list_name a.whiteLink")
            if not link:
                continue
            href = link.get("href", "")
            count_el = option.select_one(".forum_list_postcount")
            thread_count = count_el.get_text(strip=True) if count_el else "0"
            subforums.append({"url": href, "thread_count": thread_count})

        logger.debug(f"Found {len(subforums)} subforums")
        return subforums

    def _scan_threads(self, subforum_url: str, cutoff: datetime, max_pages: int = 50) -> list[dict]:
        """Scan a subforum for threads with recent activity."""
        now = datetime.now(timezone.utc)
        recent_threads = []
        page = 0
        sep = "&" if "?" in subforum_url else "?"

        while True:
            page_url = f"{subforum_url}{sep}fp={page + 1}" if page > 0 else subforum_url
            logger.debug(f"Scanning threads page: {page_url}")
            soup = self._get(page_url)

            topics = soup.select("div.forum_topic")
            if not topics:
                break

            all_old = True
            unparseable = 0
            for topic in topics:
                overlay = topic.select_one("a.forum_topic_overlay")
                if not overlay:
                    continue

                thread_url = overlay.get("href", "")
                thread_id = topic.get("data-gidforumtopic", "")
                tooltip = topic.get("data-tooltip-forum", "")

                last_post_time = _parse_tooltip_timestamp(tooltip, now) if tooltip else None

                if last_post_time and last_post_time >= cutoff:
                    all_old = False
                    recent_threads.append({
                        "url": thread_url,
                        "id": thread_id,
                        "last_post_time": last_post_time,
                    })
                elif last_post_time and last_post_time < cutoff:
                    continue  # old thread, but keep checking this page
                else:
                    unparseable += 1
                    if tooltip:
                        logger.warning(f"Could not parse last post time for thread: {thread_url}")

            # If every thread on the page was unparseable (no tooltips), stop
            if unparseable == len(topics):
                logger.debug("No parseable timestamps on page, skipping subforum")
                break

            if all_old:
                logger.debug("All threads on page are older than 24h, stopping pagination")
                break

            page += 1
            if page >= max_pages:
                logger.debug(f"Reached max page limit ({max_pages}), stopping")
                break

        logger.debug(f"Found {len(recent_threads)} recent threads in subforum")
        return recent_threads

    def _extract_posts(self, thread_url: str, cutoff: datetime) -> list[dict]:
        """Extract recent posts from a thread, including the original post on page 1."""
        posts = []

        # First fetch to determine total pages
        soup = self._get(thread_url)
        total_pages = _extract_total_pages(soup)

        # Start from the last page and work backwards
        current_page = total_pages
        while current_page >= 1:
            if current_page != total_pages or total_pages > 1:
                page_url = f"{thread_url}?ctp={current_page}"
                soup = self._get(page_url)
            else:
                page_url = thread_url

            # Extract the original post on page 1 only
            if current_page == 1:
                op = self._extract_op(soup, page_url, cutoff)
                if op:
                    posts.append(op)

            comments = soup.select("div.commentthread_comment")
            found_old = False

            for comment in comments:
                # Timestamp
                ts_el = comment.select_one("span.commentthread_comment_timestamp")
                if not ts_el:
                    continue
                data_ts = ts_el.get("data-timestamp")
                if not data_ts:
                    continue
                try:
                    post_time = datetime.fromtimestamp(int(data_ts), tz=timezone.utc)
                except (ValueError, OSError):
                    continue

                if post_time < cutoff:
                    found_old = True
                    continue

                # Author
                author_el = comment.select_one("a.commentthread_author_link")
                author_profile = author_el.get("href", "") if author_el else ""
                author_name = author_el.get_text(strip=True) if author_el else ""

                # Content — handle quoted text separately
                content_el = comment.select_one("div.commentthread_comment_text")
                content = _extract_comment_text(content_el) if content_el else ""

                # Post ID for link
                comment_id = comment.get("id", "")  # e.g. "comment_757304275016774278"
                post_id = comment_id.replace("comment_", "") if comment_id.startswith("comment_") else ""
                post_url = f"{page_url}#c{post_id}" if post_id else page_url

                posts.append({
                    "author_name": author_name,
                    "author_profile": author_profile,
                    "timestamp": int(post_time.timestamp()),
                    "content": content,
                    "url": post_url,
                    "title": None,
                })

            if found_old or current_page == 1:
                break
            current_page -= 1

        return posts

    def _extract_op(self, soup: BeautifulSoup, page_url: str, cutoff: datetime) -> dict | None:
        """Extract the original post (forum_op) from a thread page."""
        op_el = soup.select_one("div.forum_op")
        if not op_el:
            return None

        # Timestamp
        ts_el = op_el.select_one(".authorline span.date[data-timestamp]")
        if not ts_el:
            return None
        data_ts = ts_el.get("data-timestamp")
        if not data_ts:
            return None
        try:
            post_time = datetime.fromtimestamp(int(data_ts), tz=timezone.utc)
        except (ValueError, OSError):
            return None

        if post_time < cutoff:
            return None

        # Title
        title_el = op_el.select_one("div.topic")
        title = title_el.get_text(strip=True) if title_el else None

        # Author
        author_el = op_el.select_one("a.forum_op_author")
        author_name = author_el.get_text(strip=True) if author_el else ""
        author_profile = author_el.get("href", "") if author_el else ""

        # Content
        content_el = op_el.select_one("div.content")
        content = _extract_comment_text(content_el) if content_el else ""

        return {
            "author_name": author_name,
            "author_profile": author_profile,
            "timestamp": int(post_time.timestamp()),
            "content": content,
            "url": page_url,
            "title": title,
        }


def _parse_absolute_timestamp(text: str, now: datetime) -> datetime | None:
    """Parse Steam absolute timestamps like 'Jan 14 @ 4:36pm' or 'Mar 27, 2023 @ 1:55pm'."""
    text = text.strip()
    # Format with year: "Mar 27, 2023 @ 1:55pm"
    for fmt in ("%b %d, %Y @ %I:%M%p", "%b %d, %Y @ %I:%M %p"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # Format without year: "Jan 14 @ 4:36pm" — assume current year
    for fmt in ("%b %d @ %I:%M%p", "%b %d @ %I:%M %p"):
        try:
            dt = datetime.strptime(text, fmt).replace(year=now.year, tzinfo=timezone.utc)
            # If parsed date is in the future, it's probably last year
            if dt > now:
                dt = dt.replace(year=now.year - 1)
            return dt
        except ValueError:
            pass
    # Time-only format: "5:24pm" — means today
    for fmt in ("%I:%M%p", "%I:%M %p"):
        try:
            t = datetime.strptime(text, fmt)
            return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        except ValueError:
            pass
    return None


def _parse_tooltip_timestamp(tooltip_html: str, now: datetime) -> datetime | None:
    """Extract last-post timestamp from a thread tooltip HTML string."""
    decoded = html.unescape(tooltip_html)
    # Look for the "Last post:" row's <span> timestamp (second span in that row)
    # Find the "Last post:" section and get all spans after it
    last_post_idx = decoded.find("Last post:")
    if last_post_idx >= 0:
        after_last_post = decoded[last_post_idx:]
        spans_after = re.findall(r"<span[^>]*>(.*?)</span>", after_last_post)
        for span_text in spans_after:
            result = _parse_absolute_timestamp(span_text, now)
            if result:
                return result
    # Fall back: try the last <span> with a date-like value from entire tooltip
    spans = re.findall(r"<span[^>]*>(.*?)</span>", decoded)
    for span_text in reversed(spans):
        result = _parse_absolute_timestamp(span_text, now)
        if result:
            return result
    return None


def _extract_total_pages(soup: BeautifulSoup) -> int:
    """Extract total comment pages from 'Showing X-Y of Z comments' text."""
    paging = soup.select_one(".forum_paging")
    if not paging:
        return 1
    text = paging.get_text()
    match = re.search(r"of\s+([\d,]+)\s+comment", text)
    if not match:
        return 1
    total_comments = int(match.group(1).replace(",", ""))
    per_page = 15  # Steam default
    # Also check from "Showing X-Y"
    range_match = re.search(r"Showing\s+([\d,]+)-([\d,]+)", text)
    if range_match:
        start = int(range_match.group(1).replace(",", ""))
        end = int(range_match.group(2).replace(",", ""))
        if end >= start:
            per_page = end - start + 1
    if per_page <= 0:
        per_page = 15
    return max(1, (total_comments + per_page - 1) // per_page)


def _extract_comment_text(el: BeautifulSoup) -> str:
    """Extract comment text, stripping quoted sections entirely."""
    el = copy(el)
    for quote in el.select("blockquote.bb_blockquote"):
        quote.decompose()
    return el.get_text(strip=True)
