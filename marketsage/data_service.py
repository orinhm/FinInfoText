"""
Data service — thin registry over scraper plugins + cached JSON loading.

The Librarian agent decides *what* to fetch; this module does the *how*.

Failed scrape attempts are logged to ``scrape_failures.json`` so a
separate process can review them and build the missing scrapers.
"""

from __future__ import annotations

import json
import logging
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from marketsage.scrapers import get_registry, list_scrapers

logger = logging.getLogger("marketsage.data")

_DATA_DIR = Path(__file__).parent.parent  # FinInfo/
FAILURES_FILE = _DATA_DIR / "scrape_failures.json"


def _load_failures() -> list[dict]:
    """Load existing failure log. Resilient to corrupted files."""
    if FAILURES_FILE.exists():
        try:
            with open(FAILURES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("  ⚠ scrape_failures.json is corrupted (%s), resetting", exc)
            # Reset the corrupted file
            with open(FAILURES_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
    return []


def _save_failures(failures: list[dict]) -> None:
    """Persist failure log."""
    with open(FAILURES_FILE, "w", encoding="utf-8") as f:
        json.dump(failures, f, indent=2, ensure_ascii=False)


def log_scrape_failure(
    source_name: str,
    error: str,
    context: str = "",
    url: str = "",
    params: dict | None = None,
) -> None:
    """
    Record a failed scrape attempt for later review.

    Parameters
    ----------
    source_name : str
        Name of the source that failed (e.g. ``"sedar"``, ``"ceo_ca"``).
    error : str
        Error message or traceback summary.
    context : str
        What the system was trying to do when the failure occurred.
    url : str
        URL that was being scraped, if applicable.
    params : dict, optional
        Parameters that were passed to the scraper.
    """
    failures = _load_failures()
    failures.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source_name,
        "error": error,
        "context": context,
        "url": url,
        "params": params or {},
        "resolved": False,
    })
    _save_failures(failures)


def fetch_source(source_name: str, **params: Any) -> list[dict]:
    """
    Fetch data from a named scraper.

    If the source is unknown or fetching fails, the failure is logged
    to ``scrape_failures.json`` for later review.

    Parameters
    ----------
    source_name : str
        Key matching a scraper module name (e.g. ``"ceo_ca"``).
    **params
        Forwarded to the scraper's ``fetch()`` function.

    Returns
    -------
    list[dict]
        Raw records from the scraper. Empty list on failure.
    """
    logger.info("  fetch_source('%s', params=%s)", source_name, params)
    registry = get_registry()
    if source_name not in registry:
        logger.warning("  ⚠ Unknown source '%s' — available: %s",
                       source_name, list_scrapers())
        log_scrape_failure(
            source_name=source_name,
            error=f"No scraper module found for '{source_name}'",
            context=f"Available scrapers: {list_scrapers()}",
            params=params,
        )
        return []

    try:
        result = registry[source_name](**params)
        logger.info("  ✓ '%s' returned %d records", source_name, len(result))
        return result
    except Exception as exc:
        logger.error("  ✗ '%s' raised %s: %s", source_name,
                     type(exc).__name__, exc)
        log_scrape_failure(
            source_name=source_name,
            error=str(exc),
            context=f"Scraper '{source_name}' raised an exception during fetch",
            params=params,
        )
        return []


def load_cached_json(filename: str) -> list[dict]:
    """Load a pre-cached JSON file from the project data directory."""
    path = _DATA_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def format_spiels_for_llm(spiels: list[dict]) -> str:
    """Convert spiels list to a compact text block for LLM consumption."""
    lines: list[str] = []
    for s in spiels:
        text = s.get("spiel", "").strip()
        if not text:
            continue
        author = s.get("name", "anon")
        votes = s.get("votes", 0)
        ts = s.get("timestamp", "")
        # Convert ms timestamp to human-readable date
        date_str = ""
        if ts and isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        line = f"[{date_str} | {author}, votes={votes}] {text}"
        lines.append(line)
    header = f"# CEO.CA Spiels ({len(lines)} posts)\n\n"
    return header + "\n".join(lines)


def format_articles_for_llm(articles: list[dict]) -> str:
    """Convert articles list to a structured text block for LLM consumption."""
    parts: list[str] = []
    for a in articles:
        title = a.get("title", "Untitled")
        date = a.get("date", "")
        body = a.get("body", "")
        source = a.get("source", "")
        source_str = f" [{source}]" if source else ""
        parts.append(f"## {title} ({date}){source_str}\n\n{body}")
    header = f"# News Articles ({len(parts)} articles)\n\n"
    return header + "\n\n---\n\n".join(parts)


def format_yahoo_for_llm(records: list[dict]) -> str:
    """Format Yahoo Finance data for LLM consumption."""
    if not records:
        return "(no Yahoo Finance data)"
    data = records[0]  # Single record per ticker
    parts: list[str] = []

    ticker = data.get("ticker", "?")
    company = data.get("company", {})
    parts.append(f"# {company.get('name', ticker)} ({ticker})")
    if company.get("sector"):
        parts.append(f"**Sector**: {company['sector']} | "
                     f"**Industry**: {company.get('industry', '')}")
    if company.get("description"):
        parts.append(f"\n{company['description']}")

    # Price summary
    ps = data.get("price_summary", {})
    if ps and "error" not in ps:
        parts.append(f"\n## Price Summary ({ps.get('period_start', '')} → "
                     f"{ps.get('period_end', '')})")
        parts.append(f"- Start: ${ps.get('start_price', 0):.2f}")
        parts.append(f"- End: ${ps.get('end_price', 0):.2f}")
        parts.append(f"- Change: {ps.get('change_pct', 0):+.1f}%")
        parts.append(f"- High: ${ps.get('high', 0):.2f}")
        parts.append(f"- Low: ${ps.get('low', 0):.2f}")
        parts.append(f"- Avg Volume: {ps.get('avg_volume', 0):,}")

    # Key financials
    fin = data.get("financials", {})
    if fin and "error" not in fin:
        parts.append("\n## Key Financials")
        for key, val in fin.items():
            label = key.replace("_", " ").title()
            if isinstance(val, float):
                if val > 1_000_000:
                    parts.append(f"- {label}: ${val/1e6:,.1f}M")
                else:
                    parts.append(f"- {label}: {val:.2f}")
            else:
                parts.append(f"- {label}: {val}")

    # News
    news = data.get("news", [])
    if news:
        parts.append(f"\n## Recent News ({len(news)} headlines)")
        for n in news:
            parts.append(f"- [{n.get('date', '')[:10]}] {n['title']} "
                         f"({n.get('publisher', '')})")

    # Recommendations (handle both aggregated and legacy formats)
    recs = data.get("recommendations", [])
    if recs:
        if recs[0].get("period"):
            # Aggregated format: strongBuy/buy/hold/sell/strongSell
            parts.append(f"\n## Analyst Recommendations ({len(recs)} periods)")
            for r in recs:
                total = (r.get('strong_buy', 0) + r.get('buy', 0) +
                         r.get('hold', 0) + r.get('sell', 0) +
                         r.get('strong_sell', 0))
                parts.append(
                    f"- [{r.get('period', '')}] "
                    f"Strong Buy: {r.get('strong_buy', 0)}, "
                    f"Buy: {r.get('buy', 0)}, "
                    f"Hold: {r.get('hold', 0)}, "
                    f"Sell: {r.get('sell', 0)}, "
                    f"Strong Sell: {r.get('strong_sell', 0)} "
                    f"(Total: {total})"
                )
        else:
            # Legacy format: per-firm ratings
            parts.append(f"\n## Analyst Recommendations ({len(recs)} recent)")
            for r in recs:
                parts.append(f"- [{r.get('date', '')}] {r.get('firm', '')}: "
                             f"{r.get('grade', '')} ({r.get('action', '')})")

    return "\n".join(parts)


def format_fred_for_llm(records: list[dict]) -> str:
    """Format FRED data for LLM consumption."""
    if not records:
        return "(no FRED data)"
    parts: list[str] = [f"# Economic Data ({len(records)} series)\n"]

    for series in records:
        title = series.get("title", series.get("series_id", "?"))
        units = series.get("units", "")
        latest = series.get("latest", {})
        change = series.get("change")

        parts.append(f"\n## {title}")
        parts.append(f"- **Series**: {series.get('series_id', '?')}")
        parts.append(f"- **Units**: {units}")
        parts.append(f"- **Frequency**: {series.get('frequency', '?')}")

        if latest:
            parts.append(f"- **Latest**: {latest['value']} ({latest['date']})")
        if change is not None:
            parts.append(f"- **Change**: {change:+.4f}")

        # Show recent data points (last 10)
        points = series.get("data_points", [])
        if points:
            parts.append(f"- **Data points**: {len(points)} observations")
            parts.append("\n| Date | Value |")
            parts.append("|------|-------|")
            for p in points[:10]:
                parts.append(f"| {p['date']} | {p['value']} |")

    return "\n".join(parts)


def format_generic_for_llm(records: list[dict], source_name: str = "") -> str:
    """Generic formatter — converts any list of dicts to readable text."""
    if not records:
        return f"(no data from {source_name})"
    import json
    header = f"# Data from {source_name} ({len(records)} records)\n\n"
    body = json.dumps(records[:50], indent=2, default=str, ensure_ascii=False)
    return header + body

