"""
Tool declarations and execution registry for MarketSage tool-based mode.

Each scraper and utility function is exposed as a Gemini function-calling
tool.  The ``TOOL_DECLARATIONS`` list contains the JSON schemas sent to
the API;  ``TOOL_REGISTRY`` maps function names to their Python callables.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from marketsage.data_service import (
    fetch_source,
    format_articles_for_llm,
    format_fred_for_llm,
    format_generic_for_llm,
    format_spiels_for_llm,
    format_yahoo_for_llm,
)
from marketsage.versioning import (
    read_frontmatter,
    stamp_and_commit,
    write_with_frontmatter,
)

logger = logging.getLogger("marketsage.tools")

_VAULT_ROOT = Path(__file__).parent.parent / "vault"
_AGENTS_ROOT = Path(__file__).parent / "agents"
_PENDING_DIR = Path(__file__).parent.parent / "pending_updates"


# ---------------------------------------------------------------------------
# Tool function declarations  (Gemini function-calling schema)
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    # ── Scrapers ──────────────────────────────────────────────────────
    {
        "name": "fetch_ceo_ca",
        "description": (
            "Fetch discussion posts (spiels) from CEO.CA forum channels. "
            "Use this when the user mentions CEO.CA, message boards, "
            "retail sentiment, or community discussions for a specific ticker. "
            "The channel is typically the ticker symbol in lowercase (e.g. 'nfg', 'tsla')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel slug on CEO.CA, e.g. 'nfg', 'tsla', 'gold'.",
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days back to scrape. Default: 60.",
                },
            },
            "required": ["channel"],
        },
    },
    {
        "name": "fetch_yahoo_finance",
        "description": (
            "Fetch comprehensive stock/equity data from Yahoo Finance: "
            "price history, company info, key financials, news headlines, "
            "and analyst recommendations. Works for any ticker supported by "
            "Yahoo Finance globally (e.g. 'NFGC.V', 'TSLA', 'AAPL', 'GOLD')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol, e.g. 'TSLA', 'NFGC.V', 'GOLD'.",
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days of price history. Default: 60.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "fetch_fred",
        "description": (
            "Fetch macroeconomic data from FRED (Federal Reserve Economic Data). "
            "Covers 800,000+ series: GDP, CPI, unemployment, interest rates, "
            "gold price, oil price, housing, and more. "
            "You can specify series by common name (e.g. 'cpi', 'gold_price', "
            "'fed_funds', 'unemployment') or by FRED ID (e.g. 'CPIAUCSL'). "
            "Alternatively, provide a search query to find relevant series."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "series": {
                    "type": "string",
                    "description": (
                        "FRED series ID or comma-separated list. "
                        "Common names: fed_funds, 10y_treasury, cpi, "
                        "unemployment, gdp, gold_price, oil_wti, "
                        "consumer_sentiment, vix, m2, mortgage_30y."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant FRED series (e.g. 'copper price').",
                },
                "days_back": {
                    "type": "integer",
                    "description": "How far back to look. Default: 365.",
                },
            },
        },
    },
    {
        "name": "fetch_nfg_news",
        "description": (
            "Fetch official press releases from NewFoundGold Corp "
            "(https://newfoundgold.ca/news-releases/). Use this specifically "
            "for NewFoundGold / NFGC / NFG news releases and corporate announcements."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days_back": {
                    "type": "integer",
                    "description": "How many days back to scrape. Default: 60.",
                },
            },
        },
    },
    {
        "name": "fetch_web_news",
        "description": (
            "Fetch news articles from any website by scraping article listings "
            "and extracting full text. Works with most news sites: mining.com, "
            "kitco.com, seekingalpha.com, marketwatch.com, northernminer.com, "
            "rigzone.com, oilprice.com, and generic WordPress sites. "
            "Requires a URL and optionally a search query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "Website URL to scrape, e.g. 'https://www.mining.com', "
                        "'https://www.kitco.com/news'."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. a ticker or topic like 'gold mining'.",
                },
                "days_back": {
                    "type": "integer",
                    "description": "How far back to look. Default: 30.",
                },
                "max_articles": {
                    "type": "integer",
                    "description": "Maximum articles to fetch. Default: 30.",
                },
            },
            "required": ["url"],
        },
    },

    # ── Vault / Knowledge tools ───────────────────────────────────────
    {
        "name": "read_vault_file",
        "description": (
            "Read a markdown file from the knowledge vault. "
            "The vault is hierarchically organized: "
            "vault/commodities/, vault/equities/, vault/mining/, etc. "
            "Each level has .md files with accumulated intelligence. "
            "Use this to check existing knowledge before analyzing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path within the vault directory, "
                        "e.g. 'commodities/precious_metals/gold/assets/nfgc.md' "
                        "or '_index.json'."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_vault_contents",
        "description": (
            "List files and subdirectories in a vault directory. "
            "Use this to discover what knowledge exists in the vault."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path within the vault directory to list, "
                        "e.g. '' for root, 'commodities/', 'equities/'."
                    ),
                },
            },
        },
    },
    {
        "name": "persist_learning",
        "description": (
            "Persist a new piece of knowledge learned during analysis. "
            "This appends a timestamped learning to the appropriate vault "
            "or agent knowledge file. Use this when you discover important "
            "facts, patterns, or insights that should be remembered for "
            "future analyses. Be selective — only persist genuinely new "
            "and useful knowledge."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The knowledge to persist. Be specific and factual.",
                },
                "level": {
                    "type": "string",
                    "enum": ["asset", "sector", "generic"],
                    "description": (
                        "Where to store: 'asset' = specific ticker vault file, "
                        "'sector' = sector-level knowledge, "
                        "'generic' = general knowledge applicable broadly."
                    ),
                },
                "vault_path": {
                    "type": "string",
                    "description": (
                        "For asset/sector level: vault path, "
                        "e.g. 'commodities/precious_metals/gold/assets/nfgc.md'."
                    ),
                },
                "agent_path": {
                    "type": "string",
                    "description": (
                        "For generic level: agent path, "
                        "e.g. 'trader' or 'accountant/mining'."
                    ),
                },
            },
            "required": ["text", "level"],
        },
    },

    # ── Agent info tools ──────────────────────────────────────────────
    {
        "name": "list_available_scrapers",
        "description": (
            "List all available data scrapers. Returns their names "
            "and brief descriptions. Call this early if you need to "
            "decide which data sources to use."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "read_agent_knowledge",
        "description": (
            "Read the prompt and accumulated knowledge for a specific "
            "agent persona. Use this to adopt a specialist perspective "
            "when analyzing data (e.g. read the 'trader' agent to think "
            "like a market trader, or 'auditor/geologist' for geological "
            "expertise)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_path": {
                    "type": "string",
                    "description": (
                        "Agent path, e.g. 'trader', 'accountant/mining', "
                        "'executive/gold', 'auditor/geologist', 'librarian'."
                    ),
                },
            },
            "required": ["agent_path"],
        },
    },
    {
        "name": "create_agent_specialization",
        "description": (
            "Create a new specialist sub-agent under an existing base agent. "
            "Use this when you encounter a sector or domain that has no "
            "existing specialization. For example, if you are analyzing a "
            "tech company but only 'executive' exists (no 'executive/tech'), "
            "call this to create 'executive/tech' with a tailored prompt. "
            "The new agent inherits the base agent's prompt and knowledge, "
            "and becomes immediately available for future analyses. "
            "Only create specializations when genuinely needed — i.e., when "
            "sector-specific analytical frameworks would meaningfully improve "
            "the analysis beyond what the base agent provides."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base_agent": {
                    "type": "string",
                    "description": (
                        "The base agent to specialize, e.g. 'executive', "
                        "'trader', 'accountant', 'auditor'. Must be an "
                        "existing top-level agent."
                    ),
                },
                "specialization": {
                    "type": "string",
                    "description": (
                        "Name of the new specialization (lowercase, no spaces), "
                        "e.g. 'tech', 'energy', 'biotech', 'real_estate'."
                    ),
                },
                "sector_description": {
                    "type": "string",
                    "description": (
                        "A description of the sector/domain this specialist "
                        "covers, e.g. 'Technology sector including SaaS, "
                        "semiconductors, consumer electronics, and AI companies'."
                    ),
                },
                "key_metrics": {
                    "type": "string",
                    "description": (
                        "Sector-specific metrics and frameworks this specialist "
                        "should focus on, e.g. 'ARR, DAU/MAU, net retention, "
                        "TAM, rule of 40, burn multiple'."
                    ),
                },
            },
            "required": ["base_agent", "specialization", "sector_description"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _exec_fetch_ceo_ca(channel: str, days_back: int = 60, **_kw) -> str:
    """Fetch CEO.CA spiels and format for LLM."""
    logger.info("  🔧 Tool: fetch_ceo_ca(channel=%s, days_back=%d)", channel, days_back)
    records = fetch_source("ceo_ca", channel=channel, days_back=days_back)
    if not records:
        return f"(⚠ No data from CEO.CA #{channel} — 0 posts returned.)"
    text = format_spiels_for_llm(records)
    logger.info("  ✓ fetch_ceo_ca returned %d records, %d chars", len(records), len(text))
    return text


def _exec_fetch_yahoo_finance(ticker: str, days_back: int = 60, **_kw) -> str:
    """Fetch Yahoo Finance data and format for LLM."""
    logger.info("  🔧 Tool: fetch_yahoo_finance(ticker=%s, days_back=%d)", ticker, days_back)
    records = fetch_source("yahoo_finance", ticker=ticker.upper(), days_back=days_back)
    if not records:
        return f"(⚠ No data from Yahoo Finance for {ticker} — 0 records returned.)"
    text = format_yahoo_for_llm(records)
    logger.info("  ✓ fetch_yahoo_finance returned %d chars", len(text))
    return text


def _exec_fetch_fred(series: str = "", query: str = "",
                     days_back: int = 365, **_kw) -> str:
    """Fetch FRED data and format for LLM."""
    logger.info("  🔧 Tool: fetch_fred(series=%s, query=%s, days_back=%d)",
                series, query, days_back)
    kwargs: dict[str, Any] = {"days_back": days_back}
    if series:
        kwargs["series"] = series
    if query:
        kwargs["query"] = query
    records = fetch_source("fred", **kwargs)
    if not records:
        return "(⚠ No data from FRED — 0 series returned.)"
    text = format_fred_for_llm(records)
    logger.info("  ✓ fetch_fred returned %d series, %d chars", len(records), len(text))
    return text


def _exec_fetch_nfg_news(days_back: int = 60, **_kw) -> str:
    """Fetch NewFoundGold news and format for LLM."""
    logger.info("  🔧 Tool: fetch_nfg_news(days_back=%d)", days_back)
    records = fetch_source("nfg_news", days_back=days_back)
    if not records:
        return "(⚠ No NewFoundGold news articles found.)"
    text = format_articles_for_llm(records)
    logger.info("  ✓ fetch_nfg_news returned %d articles, %d chars", len(records), len(text))
    return text


def _exec_fetch_web_news(url: str, query: str = "",
                         days_back: int = 30, max_articles: int = 30,
                         **_kw) -> str:
    """Fetch web news and format for LLM."""
    logger.info("  🔧 Tool: fetch_web_news(url=%s, query=%s, days_back=%d)",
                url, query, days_back)
    records = fetch_source("web_news", url=url, query=query,
                           days_back=days_back, max_articles=max_articles)
    if not records:
        return f"(⚠ No articles found from {url}.)"
    text = format_articles_for_llm(records)
    logger.info("  ✓ fetch_web_news returned %d articles, %d chars", len(records), len(text))
    return text


def _exec_read_vault_file(path: str, **_kw) -> str:
    """Read a vault file."""
    target = _VAULT_ROOT / path.lstrip("/")
    logger.info("  🔧 Tool: read_vault_file(%s)", target)
    if not target.exists():
        return f"(File not found: vault/{path})"
    if not target.is_file():
        return f"(Not a file: vault/{path} — use list_vault_contents to browse directories)"
    # Security: ensure path stays within vault
    try:
        target.resolve().relative_to(_VAULT_ROOT.resolve())
    except ValueError:
        return "(Error: path escapes the vault directory)"
    content = target.read_text(encoding="utf-8")
    logger.info("  ✓ read_vault_file: %d chars", len(content))
    return content


def _exec_list_vault_contents(path: str = "", **_kw) -> str:
    """List vault directory contents."""
    target = _VAULT_ROOT / path.lstrip("/")
    logger.info("  🔧 Tool: list_vault_contents(%s)", target)
    if not target.exists():
        return f"(Directory not found: vault/{path})"
    if not target.is_dir():
        return f"(Not a directory: vault/{path})"

    entries: list[str] = []
    for item in sorted(target.iterdir()):
        if item.name.startswith(".") or item.name == "__pycache__":
            continue
        kind = "📁" if item.is_dir() else "📄"
        size = f" ({item.stat().st_size} bytes)" if item.is_file() else ""
        entries.append(f"  {kind} {item.name}{size}")

    result = f"Contents of vault/{path}:\n" + "\n".join(entries) if entries else f"(Empty directory: vault/{path})"
    return result


def _exec_persist_learning(text: str, level: str,
                           vault_path: str = "", agent_path: str = "",
                           **_kw) -> str:
    """Persist a learning to vault or agent knowledge."""
    now = datetime.now(timezone.utc).isoformat()
    logger.info("  🔧 Tool: persist_learning(level=%s, text=%s...)", level, text[:60])

    if level == "asset" or level == "sector":
        if not vault_path:
            return "(Error: vault_path is required for asset/sector level learnings)"
        target = _VAULT_ROOT / vault_path.lstrip("/")
    elif level == "generic":
        if not agent_path:
            return "(Error: agent_path is required for generic level learnings)"
        parts = agent_path.strip("/").split("/")
        target = _AGENTS_ROOT / parts[0] / "knowledge" / "learned.md"
    else:
        return f"(Error: unknown level '{level}'. Use: asset, sector, generic)"

    # Read or create file
    if target.exists():
        fm, body = read_frontmatter(target)
    else:
        fm = {
            "type": "knowledge",
            "revision": 0,
            "last_modified": now,
            "summary": "Auto-generated learnings",
        }
        body = "# Learnings\n\n"

    # Append learning
    body += f"\n- [{now[:10]}] {text}\n"
    write_with_frontmatter(target, fm, body)
    stamp_and_commit(target, f"Learning: {text[:60]}")
    logger.info("  ✓ persist_learning → %s", target)
    return f"✓ Learning persisted to {target.relative_to(Path(__file__).parent.parent)}"


def _exec_list_available_scrapers(**_kw) -> str:
    """List all available scrapers."""
    logger.info("  🔧 Tool: list_available_scrapers()")
    from marketsage.scrapers import list_scrapers
    scrapers = list_scrapers()
    lines = [
        "Available data scrapers:",
        "",
        "- **ceo_ca**: CEO.CA forum discussions (spiels) — retail sentiment, message boards",
        "- **yahoo_finance**: Stock data, financials, news, analyst recommendations",
        "- **fred**: Federal Reserve Economic Data — macroeconomic indicators (800K+ series)",
        "- **nfg_news**: NewFoundGold Corp official press releases",
        "- **web_news**: Generic web/news article scraper (mining.com, kitco, etc.)",
        "",
        f"Registry contains: {scrapers}",
    ]
    return "\n".join(lines)


def _exec_read_agent_knowledge(agent_path: str, **_kw) -> str:
    """Read an agent's prompt and knowledge."""
    logger.info("  🔧 Tool: read_agent_knowledge(%s)", agent_path)
    try:
        from marketsage.agent import Agent
        agent = Agent(agent_path)
        prompt = agent.system_prompt
        return f"# Agent: {agent_path}\n\n{prompt}"
    except FileNotFoundError:
        # List available agents
        from marketsage.agent import discover_agents
        available = sorted(discover_agents().keys())
        return (
            f"(Agent '{agent_path}' not found. "
            f"Available agents: {available})"
        )


def _exec_create_agent_specialization(
    base_agent: str,
    specialization: str,
    sector_description: str,
    key_metrics: str = "",
    **_kw,
) -> str:
    """Create a new sub-agent specialization under an existing base agent."""
    import re
    logger.info("  🔧 Tool: create_agent_specialization(%s/%s)",
                base_agent, specialization)

    # Sanitize inputs
    base_agent = base_agent.strip().strip("/")
    specialization = re.sub(r'[^a-z0-9_]', '_', specialization.strip().lower())

    # Validate base agent exists
    base_dir = _AGENTS_ROOT / base_agent
    if not base_dir.is_dir():
        from marketsage.agent import discover_agents
        available = [k for k in discover_agents() if '/' not in k]
        return (
            f"(Error: base agent '{base_agent}' not found. "
            f"Available base agents: {available})"
        )

    # Check if specialization already exists
    spec_dir = base_dir / specialization
    if spec_dir.is_dir() and (spec_dir / "prompt.md").exists():
        return (
            f"(Agent '{base_agent}/{specialization}' already exists. "
            f"Use read_agent_knowledge to view it, or persist_learning "
            f"to add knowledge to it.)"
        )

    # Read the base agent's prompt to derive the specialization
    base_prompt_file = base_dir / "prompt.md"
    if base_prompt_file.exists():
        base_content = base_prompt_file.read_text(encoding="utf-8")
        # Strip frontmatter to get the core prompt
        if base_content.startswith("---"):
            end = base_content.find("---", 3)
            if end > 0:
                base_body = base_content[end + 3:].strip()
            else:
                base_body = base_content
        else:
            base_body = base_content
    else:
        base_body = f"(Base prompt for '{base_agent}' not found)"

    # Build the specialized prompt
    now = datetime.now(timezone.utc)
    title = specialization.replace('_', ' ').title()

    metrics_section = ""
    if key_metrics:
        metrics_section = (
            f"\n## Sector-Specific Metrics\n\n"
            f"When analyzing {title} sector assets, prioritize these metrics:\n"
            f"- {key_metrics}\n"
        )

    spec_prompt = (
        f"---\n"
        f"type: prompt\n"
        f"revision: 1\n"
        f"last_modified: {now.isoformat()}\n"
        f"summary: \"{title} specialization of {base_agent}\"\n"
        f"---\n"
        f"# {base_agent.title()} — {title} Specialist\n\n"
        f"You are a **{title} Specialist** — a sector-focused variant of the "
        f"{base_agent.title()} agent, tailored for the {title} sector.\n\n"
        f"## Sector Focus\n\n"
        f"{sector_description}\n"
        f"{metrics_section}\n"
        f"## Base Framework\n\n"
        f"You inherit the following analytical framework from the base "
        f"{base_agent} agent. Apply it through the lens of the {title} sector:\n\n"
        f"{base_body}\n"
    )

    # Create directory structure
    spec_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir = spec_dir / "knowledge"
    knowledge_dir.mkdir(exist_ok=True)

    # Write the prompt
    prompt_file = spec_dir / "prompt.md"
    prompt_file.write_text(spec_prompt, encoding="utf-8")

    # Seed an empty learned.md
    learned_file = knowledge_dir / "learned.md"
    learned_content = (
        f"---\n"
        f"type: knowledge\n"
        f"revision: 0\n"
        f"last_modified: {now.isoformat()}\n"
        f"summary: \"Accumulated learnings for {base_agent}/{specialization}\"\n"
        f"---\n"
        f"# Learnings — {base_agent.title()} / {title}\n\n"
    )
    learned_file.write_text(learned_content, encoding="utf-8")

    # Try to commit via versioning
    try:
        stamp_and_commit(prompt_file,
                         f"New agent: {base_agent}/{specialization}")
    except Exception:
        pass  # Git may not be initialized

    logger.info("  ✓ Created agent specialization: %s/%s",
                base_agent, specialization)
    logger.info("    → %s", prompt_file)
    logger.info("    → %s", learned_file)

    return (
        f"✓ Created new agent specialization: {base_agent}/{specialization}\n"
        f"  📄 Prompt: {prompt_file.relative_to(_AGENTS_ROOT.parent)}\n"
        f"  📁 Knowledge: {knowledge_dir.relative_to(_AGENTS_ROOT.parent)}\n\n"
        f"The agent is now available. You can:\n"
        f"  - Use read_agent_knowledge('{base_agent}/{specialization}') to view it\n"
        f"  - Use persist_learning() to add sector knowledge to it\n"
        f"  - It will appear in future agent listings automatically"
    )


# ---------------------------------------------------------------------------
# Execution registry — maps tool names to Python callables
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, callable] = {
    "fetch_ceo_ca": _exec_fetch_ceo_ca,
    "fetch_yahoo_finance": _exec_fetch_yahoo_finance,
    "fetch_fred": _exec_fetch_fred,
    "fetch_nfg_news": _exec_fetch_nfg_news,
    "fetch_web_news": _exec_fetch_web_news,
    "read_vault_file": _exec_read_vault_file,
    "list_vault_contents": _exec_list_vault_contents,
    "persist_learning": _exec_persist_learning,
    "list_available_scrapers": _exec_list_available_scrapers,
    "read_agent_knowledge": _exec_read_agent_knowledge,
    "create_agent_specialization": _exec_create_agent_specialization,
}


def execute_tool(name: str, args: dict[str, Any]) -> str:
    """
    Execute a tool by name with given arguments.

    Returns the tool's output as a string, or an error message.
    """
    if name not in TOOL_REGISTRY:
        return f"(Error: unknown tool '{name}'. Available: {list(TOOL_REGISTRY)})"
    try:
        result = TOOL_REGISTRY[name](**args)
        return result
    except Exception as exc:
        logger.error("  ✗ Tool '%s' failed: %s", name, exc, exc_info=True)
        return f"(Error executing {name}: {type(exc).__name__}: {exc})"
