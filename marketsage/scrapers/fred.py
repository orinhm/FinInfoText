"""
Scraper: FRED (Federal Reserve Economic Data)

Fetches macroeconomic indicators from the FRED API (St. Louis Fed).
Covers 800,000+ data series including GDP, CPI, unemployment, interest
rates, housing, and more.

Requires a free API key from https://fred.stlouisfed.org/docs/api/api_key.html
Set via settings.yaml or env var FRED_API_KEY.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("marketsage.scrapers.fred")

FRED_API_BASE = "https://api.stlouisfed.org/fred"

# Common series IDs for quick reference
COMMON_SERIES = {
    # Interest rates
    "fed_funds": "FEDFUNDS",
    "10y_treasury": "DGS10",
    "2y_treasury": "DGS2",
    "yield_curve": "T10Y2Y",
    # Inflation
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "breakeven_5y": "T5YIE",
    "breakeven_10y": "T10YIE",
    # Employment
    "unemployment": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "initial_claims": "ICSA",
    "labor_force_participation": "CIVPART",
    # GDP & Output
    "gdp": "GDP",
    "real_gdp": "GDPC1",
    "gdp_growth": "A191RL1Q225SBEA",
    "industrial_production": "INDPRO",
    # Money supply
    "m2": "M2SL",
    "monetary_base": "BOGMBASE",
    # Housing
    "case_shiller": "CSUSHPINSA",
    "housing_starts": "HOUST",
    "building_permits": "PERMIT",
    "mortgage_30y": "MORTGAGE30US",
    # Dollar
    "dxy": "DTWEXBGS",
    # Commodities
    "gold_price": "GOLDAMGBD228NLBM",
    "oil_wti": "DCOILWTICO",
    "oil_brent": "DCOILBRENTEU",
    # Sentiment
    "consumer_sentiment": "UMCSENT",
    "vix": "VIXCLS",
}


def _get_api_key() -> str:
    """Get FRED API key from settings or environment."""
    # Try settings.yaml
    settings_path = Path(__file__).parent.parent / "settings.yaml"
    if settings_path.exists():
        try:
            import yaml
            with open(settings_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            key = cfg.get("fred", {}).get("api_key", "")
            if key:
                return key
        except Exception:
            pass

    # Try environment variable
    return os.environ.get("FRED_API_KEY", "")


def _fred_request(endpoint: str, params: dict) -> dict:
    """Make a FRED API request."""
    url = f"{FRED_API_BASE}/{endpoint}"
    params["file_type"] = "json"
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _fetch_series(series_id: str, api_key: str,
                  days_back: int = 365) -> dict:
    """Fetch a single FRED data series."""
    end = datetime.now()
    start = end - timedelta(days=days_back)

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "observation_start": start.strftime("%Y-%m-%d"),
        "observation_end": end.strftime("%Y-%m-%d"),
        "sort_order": "desc",
    }

    # Get series metadata
    try:
        meta = _fred_request("series", {
            "series_id": series_id,
            "api_key": api_key,
        })
        series_info = meta.get("seriess", [{}])[0]
    except Exception:
        series_info = {}

    # Get observations
    data = _fred_request("series/observations", params)
    observations = data.get("observations", [])

    # Parse observations
    data_points = []
    for obs in observations:
        value = obs.get("value", ".")
        if value == ".":  # Missing data marker
            continue
        try:
            data_points.append({
                "date": obs["date"],
                "value": float(value),
            })
        except (ValueError, KeyError):
            continue

    result = {
        "series_id": series_id,
        "title": series_info.get("title", series_id),
        "units": series_info.get("units", ""),
        "frequency": series_info.get("frequency", ""),
        "seasonal_adjustment": series_info.get("seasonal_adjustment", ""),
        "last_updated": series_info.get("last_updated", ""),
        "notes": series_info.get("notes", "")[:500],
        "data_points": data_points,
        "source": "FRED",
    }

    if data_points:
        result["latest"] = {
            "date": data_points[0]["date"],
            "value": data_points[0]["value"],
        }
        if len(data_points) > 1:
            result["previous"] = {
                "date": data_points[1]["date"],
                "value": data_points[1]["value"],
            }
            change = data_points[0]["value"] - data_points[1]["value"]
            result["change"] = round(change, 4)

    logger.info("  ✓ %s: %s = %s (%s, %d points)",
                series_id, result["title"],
                result.get("latest", {}).get("value", "N/A"),
                result.get("units", ""), len(data_points))

    return result


def _search_series(query: str, api_key: str, limit: int = 10) -> list[dict]:
    """Search FRED for series matching a query."""
    data = _fred_request("series/search", {
        "search_text": query,
        "api_key": api_key,
        "limit": limit,
        "order_by": "popularity",
        "sort_order": "desc",
    })
    results = []
    for s in data.get("seriess", []):
        results.append({
            "series_id": s["id"],
            "title": s.get("title", ""),
            "frequency": s.get("frequency", ""),
            "units": s.get("units", ""),
            "popularity": s.get("popularity", 0),
        })
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch(*, series: str | list[str] = "", query: str = "",
          days_back: int = 365, **_kwargs: Any) -> list[dict]:
    """
    Fetch economic data from FRED.

    Parameters
    ----------
    series : str or list[str]
        FRED series ID(s) to fetch. Can use common names like "cpi",
        "unemployment", "gold_price" (mapped to FRED IDs).
    query : str
        Search FRED for series matching this query.
    days_back : int
        How far back to look.

    Examples
    --------
    fetch(series="cpi")
    fetch(series=["fed_funds", "unemployment", "cpi"])
    fetch(query="gold price")
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning(
            "  FRED API key not configured. Set fred.api_key in settings.yaml "
            "or FRED_API_KEY environment variable. "
            "Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html"
        )
        return []

    results: list[dict] = []

    # Handle series parameter
    if series:
        if isinstance(series, str):
            series_list = [s.strip() for s in series.split(",")]
        else:
            series_list = list(series)

        logger.info("  🌐 Fetching %d FRED series (days_back=%d)",
                    len(series_list), days_back)

        for s in series_list:
            # Resolve common names
            series_id = COMMON_SERIES.get(s.lower(), s.upper())
            try:
                result = _fetch_series(series_id, api_key, days_back=days_back)
                results.append(result)
            except Exception as exc:
                logger.warning("  ✗ Failed to fetch %s: %s", series_id, exc)

    # Handle search query
    elif query:
        logger.info("  🌐 Searching FRED for: %s", query)
        search_results = _search_series(query, api_key)
        logger.info("  Found %d matching series", len(search_results))

        # Fetch top results
        for sr in search_results[:5]:
            try:
                result = _fetch_series(sr["series_id"], api_key,
                                       days_back=days_back)
                results.append(result)
            except Exception as exc:
                logger.warning("  ✗ Failed to fetch %s: %s",
                               sr["series_id"], exc)
    else:
        logger.warning("  fred: no series or query provided")

    logger.info("  🌐 FRED fetch complete: %d series", len(results))
    return results
