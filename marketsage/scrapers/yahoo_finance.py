"""
Scraper: Yahoo Finance

Fetches equity data using the yfinance library:
- Price history (OHLCV)
- Company info (sector, market cap, description)
- Key financials (revenue, earnings, ratios)
- Recent news headlines

Works for any ticker supported by Yahoo Finance globally.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("marketsage.scrapers.yahoo_finance")


def _safe_import():
    """Import yfinance with helpful error."""
    try:
        import yfinance as yf
        return yf
    except ImportError:
        raise ImportError(
            "yfinance not installed. Run: pip install yfinance"
        )


def _fetch_ticker_data(ticker: str, days_back: int = 60) -> list[dict]:
    """
    Fetch comprehensive data for a ticker.

    Returns a list with a single dict containing all data sections,
    formatted for LLM consumption.
    """
    yf = _safe_import()

    logger.info("  🌐 Fetching Yahoo Finance data for %s (days_back=%d)",
                ticker, days_back)

    tk = yf.Ticker(ticker)

    result: dict[str, Any] = {
        "ticker": ticker.upper(),
        "source": "yahoo_finance",
        "fetch_date": datetime.now().isoformat(),
    }

    # 1. Company info
    try:
        info = tk.info or {}
        result["company"] = {
            "name": info.get("longName", info.get("shortName", ticker)),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap", 0),
            "currency": info.get("currency", "USD"),
            "exchange": info.get("exchange", ""),
            "description": info.get("longBusinessSummary", ""),
            "website": info.get("website", ""),
            "country": info.get("country", ""),
            "employees": info.get("fullTimeEmployees", 0),
        }
        logger.info("  ✓ Company info: %s (%s)",
                    result["company"]["name"], result["company"]["sector"])
    except Exception as exc:
        logger.warning("  ✗ Company info failed: %s", exc)
        result["company"] = {"name": ticker, "error": str(exc)}

    # 2. Price history
    try:
        end = datetime.now()
        start = end - timedelta(days=days_back)
        hist = tk.history(start=start.strftime("%Y-%m-%d"),
                          end=end.strftime("%Y-%m-%d"))
        if not hist.empty:
            prices = []
            for date, row in hist.iterrows():
                prices.append({
                    "date": str(date.date()) if hasattr(date, 'date') else str(date)[:10],
                    "open": round(float(row.get("Open", 0)), 2),
                    "high": round(float(row.get("High", 0)), 2),
                    "low": round(float(row.get("Low", 0)), 2),
                    "close": round(float(row.get("Close", 0)), 2),
                    "volume": int(row.get("Volume", 0)),
                })
            result["price_history"] = prices
            result["price_summary"] = {
                "period_start": prices[0]["date"],
                "period_end": prices[-1]["date"],
                "start_price": prices[0]["close"],
                "end_price": prices[-1]["close"],
                "high": max(p["high"] for p in prices),
                "low": min(p["low"] for p in prices),
                "avg_volume": sum(p["volume"] for p in prices) // len(prices),
                "change_pct": round(
                    (prices[-1]["close"] - prices[0]["close"]) /
                    prices[0]["close"] * 100, 2
                ) if prices[0]["close"] > 0 else 0,
                "data_points": len(prices),
            }
            logger.info("  ✓ Price history: %d days, %.2f → %.2f (%+.1f%%)",
                        len(prices), prices[0]["close"], prices[-1]["close"],
                        result["price_summary"]["change_pct"])
        else:
            result["price_history"] = []
            result["price_summary"] = {"error": "No price data available"}
            logger.warning("  ✗ No price history returned")
    except Exception as exc:
        logger.warning("  ✗ Price history failed: %s", exc)
        result["price_history"] = []
        result["price_summary"] = {"error": str(exc)}

    # 3. Key financials
    try:
        info = tk.info or {}
        result["financials"] = {
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "revenue": info.get("totalRevenue"),
            "gross_profit": info.get("grossProfits"),
            "ebitda": info.get("ebitda"),
            "net_income": info.get("netIncomeToCommon"),
            "profit_margin": info.get("profitMargins"),
            "operating_margin": info.get("operatingMargins"),
            "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "book_value": info.get("bookValue"),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "50d_avg": info.get("fiftyDayAverage"),
            "200d_avg": info.get("twoHundredDayAverage"),
            "short_ratio": info.get("shortRatio"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "float_shares": info.get("floatShares"),
            "insider_pct": info.get("heldPercentInsiders"),
            "institution_pct": info.get("heldPercentInstitutions"),
        }
        # Remove None values
        result["financials"] = {k: v for k, v in result["financials"].items()
                                if v is not None}
        logger.info("  ✓ Financials: %d metrics", len(result["financials"]))
    except Exception as exc:
        logger.warning("  ✗ Financials failed: %s", exc)
        result["financials"] = {"error": str(exc)}

    # 4. Recent news (yfinance v1.3+ nests data under 'content')
    try:
        news = tk.news or []
        result["news"] = []
        for n in news[:20]:
            # v1.3+: data nested under 'content' key
            content = n.get("content", n)  # fallback to n itself for older versions
            title = content.get("title", "")
            publisher = content.get("provider", {}).get("displayName", "") if isinstance(content.get("provider"), dict) else content.get("publisher", "")
            pub_date = content.get("pubDate", content.get("displayTime", ""))
            link = content.get("canonicalUrl", {}).get("url", "") if isinstance(content.get("canonicalUrl"), dict) else content.get("link", "")
            if title:
                result["news"].append({
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "date": pub_date[:10] if pub_date else "",
                })
        logger.info("  ✓ News: %d headlines", len(result["news"]))
    except Exception as exc:
        logger.warning("  ✗ News failed: %s", exc)
        result["news"] = []

    # 5. Analyst recommendations (yfinance v1.3+ uses aggregated counts)
    try:
        recs = tk.recommendations
        if recs is not None and not recs.empty:
            # v1.3+: columns are period, strongBuy, buy, hold, sell, strongSell
            if "strongBuy" in recs.columns:
                recent = recs.head(6)  # most recent periods first
                result["recommendations"] = []
                for _, row in recent.iterrows():
                    period = row.get("period", "")
                    result["recommendations"].append({
                        "period": str(period),
                        "strong_buy": int(row.get("strongBuy", 0)),
                        "buy": int(row.get("buy", 0)),
                        "hold": int(row.get("hold", 0)),
                        "sell": int(row.get("sell", 0)),
                        "strong_sell": int(row.get("strongSell", 0)),
                    })
            else:
                # Legacy format: individual firm ratings
                recent = recs.tail(10)
                result["recommendations"] = []
                for date, row in recent.iterrows():
                    result["recommendations"].append({
                        "date": str(date)[:10],
                        "firm": row.get("Firm", ""),
                        "grade": row.get("To Grade", ""),
                        "action": row.get("Action", ""),
                    })
            logger.info("  ✓ Recommendations: %d entries",
                        len(result["recommendations"]))
        else:
            result["recommendations"] = []
    except Exception as exc:
        logger.warning("  ✗ Recommendations failed: %s", exc)
        result["recommendations"] = []

    logger.info("  🌐 Yahoo Finance fetch complete for %s", ticker)
    return [result]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch(*, ticker: str = "", days_back: int = 60,
          **_kwargs: Any) -> list[dict]:
    """
    Fetch equity data from Yahoo Finance.

    Parameters
    ----------
    ticker : str
        Stock ticker (e.g. "NFGC.V", "TSLA", "AAPL").
    days_back : int
        How many days of price history.
    """
    if not ticker:
        logger.warning("  yahoo_finance: no ticker provided")
        return []
    return _fetch_ticker_data(ticker, days_back=days_back)
