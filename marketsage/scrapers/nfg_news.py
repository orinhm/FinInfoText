"""
Scraper: NewFoundGold News Releases
Fetches official press releases from https://newfoundgold.ca/news-releases/.

WordPress site with paginated listing. Articles have full content
in class="entry-content".
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger("marketsage.scrapers.nfg_news")

NFG_BASE = "https://newfoundgold.ca"
NFG_NEWS_URL = f"{NFG_BASE}/news-releases/"
RATE_LIMIT_DELAY = 0.5
_SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
}


def _strip_html(text: str) -> str:
    """Convert HTML content to plain text."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    return text.strip()


# ---------------------------------------------------------------------------
# Live scraping
# ---------------------------------------------------------------------------

def _scrape_listing_page(page_num: int = 1) -> list[dict]:
    """
    Scrape one page of the news-releases listing.

    Returns list of dicts with keys: url, title, date_str, date.
    """
    url = NFG_NEWS_URL if page_num <= 1 else f"{NFG_NEWS_URL}page/{page_num}/"
    resp = requests.get(url, headers=_SESSION_HEADERS, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    page = resp.text

    entries: list[dict] = []
    date_re = re.compile(
        r"((?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},\s+\d{4})"
    )
    link_re = re.compile(r'href="(https://newfoundgold\.ca/[a-z0-9][\w-]+/)"')

    h3_iter = list(re.finditer(r"<h3[^>]*>(.*?)</h3>", page, re.DOTALL))
    for i, h3 in enumerate(h3_iter):
        end = h3_iter[i + 1].start() if i + 1 < len(h3_iter) else h3.end() + 5000
        block = page[h3.start():end]

        title = _strip_html(h3.group(1))
        link_m = link_re.search(block)
        date_m = date_re.search(block)

        if not link_m:
            continue

        pub_date = None
        date_str = ""
        if date_m:
            date_str = date_m.group(1)
            try:
                pub_date = datetime.strptime(date_str, "%B %d, %Y")
            except ValueError:
                pass

        entries.append({
            "url": link_m.group(1),
            "title": title,
            "date_str": date_str,
            "date": pub_date,
        })

    return entries


def _fetch_article(url: str) -> tuple[str, str, datetime | None]:
    """
    Fetch a single newfoundgold.ca article page.

    Returns (body_text, title, published_datetime).
    """
    try:
        resp = requests.get(url, headers=_SESSION_HEADERS, timeout=30)
        resp.raise_for_status()
        page = resp.text
    except Exception as exc:
        return f"[Could not fetch: {exc}]", "", None

    # Title from <h1>
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.DOTALL)
    title = _strip_html(h1.group(1)) if h1 else ""

    # Date from JSON-LD
    pub_date = None
    date_match = re.search(r'"datePublished":\s*"([^"]+)"', page)
    if date_match:
        raw = date_match.group(1)
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%d"):
            try:
                pub_date = datetime.strptime(raw.replace("+00:00", "+0000"), fmt)
                pub_date = pub_date.replace(tzinfo=None)
                break
            except ValueError:
                continue

    # Body from entry-content
    page_clean = re.sub(r"<script[^>]*>.*?</script>", "", page, flags=re.DOTALL)
    page_clean = re.sub(r"<style[^>]*>.*?</style>", "", page_clean, flags=re.DOTALL)
    page_clean = re.sub(r"<!--.*?-->", "", page_clean, flags=re.DOTALL)

    body_match = re.search(
        r'class="entry-content[^"]*"[^>]*>(.*)',
        page_clean, re.DOTALL,
    )
    if body_match:
        raw_html = body_match.group(1)
        for end_marker in (
            '<div class="post-navigation', '<div class="related',
            '<div id="comments', '<footer', '</main>',
        ):
            idx = raw_html.find(end_marker)
            if idx > 0:
                raw_html = raw_html[:idx]
                break
        body = _strip_html(raw_html)
    else:
        body = "[Could not extract article content]"

    lines = [ln.strip() for ln in body.splitlines()]
    body = "\n".join(ln for ln in lines if ln)

    return body, title, pub_date


def _scrape_news(days_back: int) -> list[dict]:
    """
    Scrape newfoundgold.ca/news-releases/ listing pages and fetch
    full article text for releases within the lookback window.

    Returns list of dicts sorted oldest-first: {url, title, date, body}
    """
    cutoff = datetime.now() - timedelta(days=days_back)
    logger.info("  🌐 Live scraping newfoundgold.ca news — back to %s",
                cutoff.strftime("%Y-%m-%d"))

    all_entries: list[dict] = []
    page_num = 0
    stop = False

    while not stop:
        page_num += 1
        entries = _scrape_listing_page(page_num)
        if not entries:
            logger.info("  Page %d: no entries — done.", page_num)
            break

        logger.info("  Page %d: %d articles", page_num, len(entries))
        for e in entries:
            if e["date"] and e["date"] < cutoff:
                stop = True
                break
            all_entries.append(e)
        time.sleep(RATE_LIMIT_DELAY)

    logger.info("  Found %d articles in window, fetching content...",
                len(all_entries))

    for i, entry in enumerate(all_entries):
        body, title, pub_date = _fetch_article(entry["url"])
        entry["body"] = body
        if title:
            entry["title"] = title
        if pub_date:
            entry["date"] = pub_date
        logger.info("    [%d/%d] %s", i + 1, len(all_entries),
                    entry["title"][:80])
        time.sleep(RATE_LIMIT_DELAY)

    all_entries.sort(key=lambda e: e["date"] or datetime.min)
    logger.info("  🌐 Fetched %d articles", len(all_entries))
    return all_entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch(*, days_back: int = 60, **_kwargs: Any) -> list[dict]:
    """
    Fetch NewFoundGold news releases from the live site.

    Parameters
    ----------
    days_back : int
        How many days back to scrape.
    """
    return _scrape_news(days_back)
