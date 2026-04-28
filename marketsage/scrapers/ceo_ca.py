"""
Scraper: CEO.CA Spiels
Fetches discussion posts (spiels) from CEO.CA channels.

CEO.CA API (reverse-engineered):
  GET https://new-api.ceo.ca/api/get_spiels?channel=<slug>
  Pagination: &load_more=top&until=<earliest_timestamp_ms>
  Returns 50 spiels per call, newest first.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger("marketsage.scrapers.ceo_ca")

API_BASE = "https://new-api.ceo.ca"
RATE_LIMIT_DELAY = 0.5
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _fetch_spiels_batch(channel: str, until: int | None = None) -> dict:
    """
    Single API call to fetch a batch of spiels.

    Parameters
    ----------
    channel : str
        Channel slug (e.g. "nfg").
    until : int, optional
        Timestamp (ms) for pagination — fetch spiels older than this.
    """
    params: dict[str, str] = {"channel": channel}
    if until is not None:
        params["load_more"] = "top"
        params["until"] = str(until)

    url = f"{API_BASE}/api/get_spiels"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF ** attempt
            logger.warning("  Retry %d/%d after error: %s (waiting %.1fs)",
                           attempt, MAX_RETRIES, exc, wait)
            time.sleep(wait)
    return {}


def _scrape_channel(channel: str, days_back: int) -> list[dict]:
    """
    Paginate through the CEO.CA API collecting all spiels
    within the lookback window.

    Returns list of spiel dicts sorted oldest-first.
    """
    cutoff_dt = datetime.now() - timedelta(days=days_back)
    cutoff_ts = int(cutoff_dt.timestamp() * 1000)
    logger.info("  🌐 Live scraping CEO.CA #%s — back to %s",
                channel, cutoff_dt.strftime("%Y-%m-%d"))

    all_spiels: dict[str, dict] = {}
    until = None
    batch = 0

    while True:
        batch += 1
        data = _fetch_spiels_batch(channel, until=until)
        spiels = data.get("spiels", [])

        if not spiels:
            logger.info("  Batch %d: no spiels returned — done.", batch)
            break

        oldest_ts = spiels[0]["timestamp"]
        newest_ts = spiels[-1]["timestamp"]
        oldest_dt = datetime.fromtimestamp(oldest_ts / 1000)
        newest_dt = datetime.fromtimestamp(newest_ts / 1000)
        logger.info("  Batch %d: %d spiels [%s → %s] total: %d",
                    batch, len(spiels),
                    oldest_dt.strftime("%Y-%m-%d %H:%M"),
                    newest_dt.strftime("%Y-%m-%d %H:%M"),
                    len(all_spiels) + len(spiels))

        for s in spiels:
            all_spiels[s["spiel_id"]] = s

        if oldest_ts <= cutoff_ts:
            logger.info("  Reached cutoff date — stopping.")
            break

        until = oldest_ts
        time.sleep(RATE_LIMIT_DELAY)

    result = [s for s in all_spiels.values() if s["timestamp"] >= cutoff_ts]
    result.sort(key=lambda s: s["timestamp"])
    logger.info("  🌐 Fetched %d spiels from #%s", len(result), channel)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch(*, channel: str = "nfg", days_back: int = 60,
          **_kwargs: Any) -> list[dict]:
    """
    Fetch CEO.CA spiels from the live API.

    Parameters
    ----------
    channel : str
        Channel name (e.g. "nfg").
    days_back : int
        How many days back to scrape.
    """
    return _scrape_channel(channel, days_back)
