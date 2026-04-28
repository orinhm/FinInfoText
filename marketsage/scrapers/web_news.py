"""
Scraper: Generic Web/News Article Scraper

Fetches articles from any news website by:
1. Fetching listing/index pages
2. Extracting article links
3. Fetching each article and extracting text content

Works with most news sites (Mining.com, Kitco, Rigzone, MarketWatch, etc.)
by using BeautifulSoup for HTML parsing and generic heuristics for
article content extraction.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("marketsage.scrapers.web_news")

RATE_LIMIT_DELAY = 1.0
_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Well-known news sites and their listing URL patterns
KNOWN_SITES: dict[str, dict[str, str]] = {
    "mining.com": {
        "listing_url": "https://www.mining.com/tag/{query}/",
        "search_url": "https://www.mining.com/?s={query}",
    },
    "kitco.com": {
        "search_url": "https://www.kitco.com/search/?q={query}",
    },
    "seekingalpha.com": {
        "search_url": "https://seekingalpha.com/search?q={query}",
    },
    "marketwatch.com": {
        "search_url": "https://www.marketwatch.com/search?q={query}",
    },
    "northernminer.com": {
        "search_url": "https://www.northernminer.com/?s={query}",
    },
    "rigzone.com": {
        "search_url": "https://www.rigzone.com/search/?q={query}",
    },
    "oilprice.com": {
        "search_url": "https://oilprice.com/search?q={query}",
    },
}


def _fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch a URL and return parsed HTML."""
    try:
        resp = requests.get(url, headers=_SESSION_HEADERS, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.warning("  ✗ Failed to fetch %s: %s", url, exc)
        return None


def _extract_article_links(soup: BeautifulSoup, base_url: str,
                           max_links: int = 50) -> list[dict]:
    """
    Extract article links from a listing page.
    Uses heuristics: looks for <a> tags inside <h2>, <h3>, or <article> elements.
    """
    articles: list[dict] = []
    seen_urls: set[str] = set()

    # Strategy 1: Links inside article/h2/h3 elements
    selectors = [
        "article a[href]",
        "h2 a[href]",
        "h3 a[href]",
        ".post-title a[href]",
        ".entry-title a[href]",
        ".article-title a[href]",
        ".story-title a[href]",
        ".headline a[href]",
    ]

    for selector in selectors:
        for link in soup.select(selector):
            href = link.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            url = urljoin(base_url, href)
            # Filter: must be same domain, look like an article path
            parsed = urlparse(url)
            base_parsed = urlparse(base_url)
            if parsed.netloc != base_parsed.netloc:
                continue
            # Skip category/tag/page links
            if any(skip in url for skip in ["/tag/", "/category/", "/page/",
                                            "/author/", "/about", "/contact",
                                            "/login", "/register"]):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = link.get_text(strip=True)
            if len(title) < 10:  # Too short to be a real title
                continue

            articles.append({"url": url, "title": title})

            if len(articles) >= max_links:
                break
        if len(articles) >= max_links:
            break

    return articles


def _extract_article_content(soup: BeautifulSoup) -> tuple[str, str, datetime | None]:
    """
    Extract article content from a fetched article page.
    Returns (body_text, title, published_date).
    """
    # Title: <h1> or og:title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "")

    # Date: look for JSON-LD, <time>, or meta tags
    pub_date = None
    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            ld = json.loads(script.string or "")
            if isinstance(ld, list):
                ld = ld[0] if ld else {}
            date_str = ld.get("datePublished", "")
            if date_str:
                pub_date = _parse_date(date_str)
                break
        except Exception:
            pass
    # <time> element
    if not pub_date:
        time_el = soup.find("time", datetime=True)
        if time_el:
            pub_date = _parse_date(time_el["datetime"])
    # meta tags
    if not pub_date:
        for name in ("article:published_time", "datePublished",
                      "publication_date", "date"):
            meta = soup.find("meta", attrs={"property": name}) or \
                   soup.find("meta", attrs={"name": name})
            if meta and meta.get("content"):
                pub_date = _parse_date(meta["content"])
                if pub_date:
                    break

    # Body: look for article content containers
    body = ""
    content_selectors = [
        "article .entry-content",
        "article .post-content",
        "article .article-body",
        "article .story-body",
        ".entry-content",
        ".post-content",
        ".article-body",
        ".article-content",
        ".story-content",
        "article",
        "[itemprop='articleBody']",
        ".content-body",
        "main",
    ]

    for selector in content_selectors:
        container = soup.select_one(selector)
        if container:
            # Remove scripts, styles, nav, ads
            for tag in container.find_all(["script", "style", "nav", "aside",
                                           "footer", "iframe", "form"]):
                tag.decompose()

            paragraphs = container.find_all(["p", "h2", "h3", "li"])
            if paragraphs:
                body = "\n\n".join(p.get_text(strip=True) for p in paragraphs
                                  if p.get_text(strip=True))
            else:
                body = container.get_text(separator="\n", strip=True)

            if len(body) > 100:  # Reasonable article length
                break

    if not body:
        body = "[Could not extract article content]"

    return body, title, pub_date


def _parse_date(date_str: str) -> datetime | None:
    """Try multiple date formats."""
    date_str = date_str.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%m/%d/%Y",
    ):
        try:
            dt = datetime.strptime(date_str.replace("+00:00", "+0000"), fmt)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            continue
    return None


def _scrape_site(url: str, query: str = "", days_back: int = 30,
                 max_articles: int = 30) -> list[dict]:
    """
    Scrape articles from a website.

    Parameters
    ----------
    url : str
        Base URL to scrape (e.g. "https://mining.com" or a search URL).
    query : str
        Search query to use (e.g. ticker or topic).
    days_back : int
        How far back to look.
    max_articles : int
        Max number of articles to fetch.
    """
    cutoff = datetime.now() - timedelta(days=days_back)

    # Determine listing URL
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")

    # If it's a known site and query is provided, use search URL
    if domain in KNOWN_SITES and query:
        site_cfg = KNOWN_SITES[domain]
        if "search_url" in site_cfg:
            listing_url = site_cfg["search_url"].format(query=query)
        elif "listing_url" in site_cfg:
            listing_url = site_cfg["listing_url"].format(query=query)
        else:
            listing_url = url
    elif query and "?" not in url:
        # Generic: try WordPress-style search
        listing_url = f"{url.rstrip('/')}/?s={query}"
    else:
        listing_url = url

    logger.info("  🌐 Scraping %s (query=%s, days_back=%d)",
                domain, query or "(none)", days_back)
    logger.info("  Listing URL: %s", listing_url)

    # Fetch listing page
    soup = _fetch_page(listing_url)
    if not soup:
        logger.warning("  Could not fetch listing page")
        return []

    # Extract article links
    article_links = _extract_article_links(soup, listing_url, max_links=max_articles)
    logger.info("  Found %d article links", len(article_links))

    if not article_links:
        return []

    # Fetch each article
    articles: list[dict] = []
    for i, link_info in enumerate(article_links):
        time.sleep(RATE_LIMIT_DELAY)

        article_soup = _fetch_page(link_info["url"])
        if not article_soup:
            continue

        body, title, pub_date = _extract_article_content(article_soup)

        # Use extracted title, fall back to link title
        if not title:
            title = link_info["title"]

        # Filter by date if available
        if pub_date and pub_date < cutoff:
            logger.info("    [%d/%d] Skipped (too old: %s): %s",
                        i + 1, len(article_links),
                        pub_date.strftime("%Y-%m-%d"), title[:60])
            continue

        articles.append({
            "url": link_info["url"],
            "title": title,
            "date": pub_date.isoformat() if pub_date else "",
            "body": body,
            "source": domain,
        })
        logger.info("    [%d/%d] %s — %s",
                    i + 1, len(article_links),
                    pub_date.strftime("%Y-%m-%d") if pub_date else "no date",
                    title[:60])

    articles.sort(key=lambda a: a.get("date", ""), reverse=True)
    logger.info("  🌐 Fetched %d articles from %s", len(articles), domain)
    return articles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch(*, url: str = "", query: str = "", days_back: int = 30,
          max_articles: int = 30, **_kwargs: Any) -> list[dict]:
    """
    Fetch news articles from any website.

    Parameters
    ----------
    url : str
        Website URL (e.g. "https://mining.com").
    query : str
        Search query (e.g. "NFGC" or "gold mining").
    days_back : int
        How far back to look.
    max_articles : int
        Max articles to fetch.
    """
    if not url:
        logger.warning("  web_news: no URL provided")
        return []
    return _scrape_site(url, query=query, days_back=days_back,
                        max_articles=max_articles)
