"""
Tool declarations and execution registry for MarketSage tool-based mode.

Each scraper and utility function is exposed as a Gemini function-calling
tool.  The ``TOOL_DECLARATIONS`` list contains the JSON schemas sent to
the API;  ``TOOL_REGISTRY`` maps function names to their Python callables.
"""

from __future__ import annotations

import json
import logging
import os
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

_KNOWLEDGE_ROOT = Path(__file__).parent.parent / "knowledge"
_PENDING_DIR = Path(__file__).parent.parent / "pending_updates"
_CUSTOM_SCRAPERS_DIR = Path(__file__).parent / "custom_scrapers"


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
        "usage_note": "One call per ticker — do not duplicate calls for the same ticker.",
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
                        "Series to fetch. Common names: 'cpi', 'gold_price', "
                        "'fed_funds', 'unemployment', 'gdp', 'oil_price', "
                        "'housing_starts', '10y_yield', 'sp500'. "
                        "Or use a FRED series ID directly, e.g. 'CPIAUCSL'."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search query to find relevant FRED series, "
                        "e.g. 'copper price', 'china gdp', 'inflation expectations'."
                    ),
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days of data to fetch. Default: 365.",
                },
            },
        },
        "usage_note": "Batch related series into a single call when possible.",
    },
    {
        "name": "fetch_nfg_news",
        "description": (
            "Fetch NewFoundGold Corp official news releases and press coverage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days_back": {
                    "type": "integer",
                    "description": "How many days back. Default: 60.",
                },
            },
        },
    },
    {
        "name": "fetch_web_news",
        "description": (
            "Fetch news articles from any website URL. "
            "Use for mining.com, kitco, reuters, seekingalpha, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Website URL to scrape.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional search/filter query.",
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days back. Default: 30.",
                },
                "max_articles": {
                    "type": "integer",
                    "description": "Max articles. Default: 30.",
                },
            },
            "required": ["url"],
        },
        "usage_note": "Vary search queries rather than repeating the same one.",
    },

    # ── Knowledge tools ───────────────────────────────────────────────
    {
        "name": "read_knowledge",
        "description": (
            "Read any file from the knowledge tree. Works for sector files, "
            "asset files, agent prompts, and learnings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path within knowledge/, e.g. "
                        "'commodities/precious_metals/gold/sector.md'."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_knowledge",
        "description": (
            "List files and subdirectories in a knowledge directory. "
            "Use this to discover what sectors, agents, and data exist."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path within knowledge/ to list, "
                        "e.g. '' for root, 'commodities/', "
                        "'commodities/precious_metals/gold/agents/'."
                    ),
                },
            },
        },
    },
    {
        "name": "persist_learning",
        "description": (
            "Persist a new piece of knowledge learned during analysis. "
            "Appends a timestamped learning to the specified file in the "
            "knowledge tree. Be selective — only persist genuinely new insights."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The knowledge to persist. Be specific and factual.",
                },
                "target_path": {
                    "type": "string",
                    "description": (
                        "Path within knowledge/ to persist to. Examples: "
                        "'commodities/precious_metals/gold/sector.md' (sector), "
                        "'commodities/precious_metals/gold/juniors/assets/nfgc.md' (asset), "
                        "'commodities/precious_metals/gold/agents/executive/knowledge.md' (agent), "
                        "'agents/geopolitical/knowledge.md' (cross-sector generic)."
                    ),
                },
            },
            "required": ["text", "target_path"],
        },
    },

    # ── Agent & sector tools ─────────────────────────────────────────
    {
        "name": "list_available_scrapers",
        "description": (
            "List all available data scrapers. Returns their names "
            "and brief descriptions."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "load_agent",
        "description": (
            "Load and compose a specialist agent persona. Combines the "
            "base role framework (from agents/) with sector-specific "
            "specialization and cross-sector inherited knowledge. Returns "
            "the full agent prompt with all accumulated knowledge."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": (
                        "Agent role: 'accountant', 'executive', 'trader', "
                        "'auditor', 'geopolitical', 'librarian'."
                    ),
                },
                "sector_path": {
                    "type": "string",
                    "description": (
                        "Optional sector path, e.g. "
                        "'commodities/precious_metals/gold', 'equities/tech'. "
                        "If omitted, loads base role only."
                    ),
                },
            },
            "required": ["role"],
        },
    },
    {
        "name": "create_custom_scraper",
        "description": (
            "Create a new reusable web scraper tool with preset URL/params."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Tool name, must start with 'fetch_'."},
                "description": {"type": "string", "description": "What this scraper does."},
                "base_url": {"type": "string", "description": "The base URL to scrape."},
                "default_query": {"type": "string", "description": "Default search query."},
                "default_days_back": {"type": "integer", "description": "Default days back. Default: 30."},
                "default_max_articles": {"type": "integer", "description": "Default max articles. Default: 20."},
            },
            "required": ["name", "description", "base_url"],
        },
    },
    {
        "name": "propose_new_tool",
        "description": "Propose a new tool that requires custom implementation.",
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Proposed tool name."},
                "description": {"type": "string", "description": "What this tool should do."},
                "data_source": {"type": "string", "description": "URL or API endpoint."},
                "parameters_needed": {"type": "string", "description": "Parameters the tool needs."},
                "rationale": {"type": "string", "description": "Why web scraper isn't sufficient."},
            },
            "required": ["tool_name", "description", "data_source", "rationale"],
        },
    },
    {
        "name": "create_sector_agent",
        "description": (
            "Create a new sector-specialized agent under a sector's agents/ dir. "
            "Example: create_sector_agent(sector_path='equities/fintech', role='accountant', ...)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sector_path": {"type": "string", "description": "Sector path, e.g. 'equities/fintech'."},
                "role": {"type": "string", "description": "Agent role: 'accountant', 'executive', etc."},
                "sector_description": {"type": "string", "description": "Description of the sector."},
                "key_metrics": {"type": "string", "description": "Sector-specific metrics."},
                "inherits_from": {"type": "string", "description": "Optional parent sector, e.g. 'mining'."},
            },
            "required": ["sector_path", "role", "sector_description"],
        },
    },
    {
        "name": "create_sector",
        "description": (
            "Create a new sector or sub-sector directory with sector.md and agents/ dir. "
            "Use this when you discover a new domain that needs its own branch in the knowledge tree. "
            "Example: create_sector(sector_path='equities/biotech', title='Biotechnology', ...)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sector_path": {
                    "type": "string",
                    "description": (
                        "Path for the new sector relative to knowledge/, "
                        "e.g. 'equities/biotech' or 'commodities/precious_metals/platinum'."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Human-readable sector title, e.g. 'Biotechnology'.",
                },
                "description": {
                    "type": "string",
                    "description": "Initial sector intelligence — what makes this sector unique.",
                },
                "inherits": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of sector paths to inherit from. "
                        "E.g. 'commodities/precious_metals,equities' for a mining sub-sector. "
                        "Leave empty if no cross-sector inheritance is needed."
                    ),
                },
            },
            "required": ["sector_path", "title", "description"],
        },
    },
    {
        "name": "propose_prompt_change",
        "description": (
            "Propose a change to an agent's prompt.md file. The proposal is saved "
            "for human review via 'python -m marketsage.admin'. Do NOT modify "
            "prompt.md directly — always use this tool for prompt evolution. "
            "Changes will be applied by an admin after review."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_prompt": {
                    "type": "string",
                    "description": (
                        "Path to the prompt file, e.g. "
                        "'agents/trader/prompt.md' or "
                        "'equities/mining/agents/accountant/prompt.md'."
                    ),
                },
                "change_type": {
                    "type": "string",
                    "description": "One of: ADD, MODIFY, REMOVE.",
                },
                "proposed_content": {
                    "type": "string",
                    "description": "The proposed new/modified prompt content.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why this change would improve the agent.",
                },
                "proposed_by": {
                    "type": "string",
                    "description": "The agent role proposing this change, e.g. 'trader'.",
                },
            },
            "required": ["target_prompt", "change_type", "proposed_content", "reasoning"],
        },
    },
    {
        "name": "read_sector_context",
        "description": (
            "Load ALL available knowledge about a sector in one call. "
            "Returns sector files, asset knowledge, available agent roles, "
            "and relevant scrapers. Use this EARLY in analysis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": "Sector keyword, e.g. 'gold', 'tech', 'copper', 'pharma'.",
                },
            },
            "required": ["sector"],
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


def _exec_read_knowledge(path: str, **_kw) -> str:
    """Read any file from the knowledge tree."""
    target = _KNOWLEDGE_ROOT / path.lstrip("/")
    logger.info("  \U0001f527 Tool: read_knowledge(%s)", path)
    if not target.exists():
        return f"(File not found: knowledge/{path})"
    if not target.is_file():
        return f"(Not a file: knowledge/{path} \u2014 use list_knowledge to browse)"
    try:
        target.resolve().relative_to(_KNOWLEDGE_ROOT.resolve())
    except ValueError:
        return "(Error: path escapes the knowledge directory)"
    content = target.read_text(encoding="utf-8")
    logger.info("  \u2713 read_knowledge: %d chars", len(content))
    return content


def _exec_list_knowledge(path: str = "", **_kw) -> str:
    """List knowledge directory contents."""
    target = _KNOWLEDGE_ROOT / path.lstrip("/")
    logger.info("  \U0001f527 Tool: list_knowledge(%s)", path)
    if not target.exists():
        return f"(Directory not found: knowledge/{path})"
    if not target.is_dir():
        return f"(Not a directory: knowledge/{path})"
    entries: list[str] = []
    for item in sorted(target.iterdir()):
        if item.name.startswith(".") or item.name == "__pycache__":
            continue
        kind = "\U0001f4c1" if item.is_dir() else "\U0001f4c4"
        size = f" ({item.stat().st_size} bytes)" if item.is_file() else ""
        entries.append(f"  {kind} {item.name}{size}")
    return f"Contents of knowledge/{path}:\n" + "\n".join(entries) if entries else f"(Empty: knowledge/{path})"


def _exec_persist_learning(text: str, target_path: str = "",
                           level: str = "", vault_path: str = "",
                           agent_path: str = "", **_kw) -> str:
    """Persist a learning to the knowledge tree with dedup guard."""
    from marketsage.curator import jaccard_similarity

    now = datetime.now(timezone.utc).isoformat()
    # Legacy compat
    if not target_path:
        if vault_path:
            target_path = vault_path
        elif agent_path and level == "generic":
            role = agent_path.strip("/").split("/")[0]
            target_path = f"agents/{role}/knowledge.md"
        else:
            return "(Error: target_path is required)"

    # Block attempts to write directly to prompt.md — route to propose_prompt_change
    if target_path.endswith("prompt.md"):
        return (
            "(Error: Do not persist learnings to prompt.md files directly. "
            "Use the propose_prompt_change tool instead to propose prompt modifications.)"
        )

    logger.info("  \U0001f527 Tool: persist_learning(target=%s, text=%s...)",
                target_path, text[:60])
    target = _KNOWLEDGE_ROOT / target_path.lstrip("/")
    try:
        target.resolve().relative_to(_KNOWLEDGE_ROOT.resolve())
    except ValueError:
        return "(Error: path escapes the knowledge directory)"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        fm, body = read_frontmatter(target)
    else:
        fm = {"type": "knowledge", "revision": 0,
              "last_modified": now, "summary": "Auto-generated learnings"}
        body = "# Learnings\n\n"

    # Dedup guard — check Jaccard similarity against existing bullets
    existing_bullets = [
        ln.strip()
        for ln in body.splitlines()
        if ln.strip().startswith("- [") and len(ln.strip()) > 10
    ]
    for existing in existing_bullets:
        if jaccard_similarity(text, existing) >= 0.60:
            logger.info("  \u26a0 Duplicate detected, skipping persist")
            return (
                f"\u26a0 Skipped — this learning is too similar to an existing entry "
                f"in knowledge/{target_path}. The knowledge tree already contains "
                f"this insight."
            )

    body += f"\n- [{now[:10]}] {text}\n"
    write_with_frontmatter(target, fm, body)
    stamp_and_commit(target, f"Learning: {text[:60]}")

    # ── Learning logs ─────────────────────────────────────────────
    run_dir = os.environ.get("MARKETSAGE_RUN_DIR", "")
    log_entry = {
        "timestamp": now,
        "target": target_path,
        "text": text,
        "run": run_dir,
    }
    entry_line = json.dumps(log_entry, ensure_ascii=False) + "\n"

    # Global log
    try:
        with open(_KNOWLEDGE_ROOT / "_learning_log.jsonl", "a", encoding="utf-8") as f:
            f.write(entry_line)
    except OSError:
        pass

    # Per-run log
    if run_dir:
        try:
            with open(Path(run_dir) / "learnings.jsonl", "a", encoding="utf-8") as f:
                f.write(entry_line)
        except OSError:
            pass

    logger.info("  \u2713 persist_learning \u2192 %s", target)
    return f"\u2713 Learning persisted to knowledge/{target_path}"


def _exec_list_available_scrapers(**_kw) -> str:
    """List all available scrapers, including custom and generated tools."""
    logger.info("  \U0001f527 Tool: list_available_scrapers()")
    from marketsage.scrapers import list_scrapers
    scrapers = list_scrapers()
    lines = [
        "## Built-in Data Scrapers", "",
        "- **ceo_ca**: CEO.CA forum discussions",
        "- **yahoo_finance**: Stock data, financials, news",
        "- **fred**: FRED macroeconomic indicators (800K+ series)",
        "- **nfg_news**: NewFoundGold Corp press releases",
        "- **web_news**: Generic web/news article scraper", "",
        f"Scraper registry: {scrapers}",
    ]
    if _CUSTOM_SCRAPERS_DIR.is_dir():
        custom_files = sorted(_CUSTOM_SCRAPERS_DIR.glob("*.json"))
        if custom_files:
            lines.extend(["", "## Custom Scrapers", ""])
            for cf in custom_files:
                try:
                    with open(cf, encoding="utf-8") as f:
                        cfg = json.load(f)
                    lines.append(f"- **{cf.stem}**: {cfg.get('description', '?')}")
                except Exception:
                    lines.append(f"- **{cf.stem}**: (config error)")
    _gen_dir = Path(__file__).parent / "generated_tools"
    if _gen_dir.is_dir():
        gen_files = sorted(_gen_dir.glob("*.json"))
        if gen_files:
            lines.extend(["", "## Generated Tools", ""])
            for gf in gen_files:
                try:
                    with open(gf, encoding="utf-8") as f:
                        m = json.load(f)
                    lines.append(f"- **{gf.stem}**: {m.get('declaration',{}).get('description','?')}")
                except Exception:
                    lines.append(f"- **{gf.stem}**: (error)")
    return "\n".join(lines)


def _exec_load_agent(role: str, sector_path: str = "", **_kw) -> str:
    """Load and compose a specialist agent persona."""
    logger.info("  \U0001f527 Tool: load_agent(role=%s, sector=%s)", role, sector_path)
    try:
        from marketsage.agent import Agent, discover_sector_agents
        agent = Agent(role=role, sector_path=sector_path or None)
        return f"# Agent: {agent}\n\n{agent.system_prompt}"
    except Exception as exc:
        from marketsage.agent import discover_all_sectors
        available = discover_all_sectors()
        return f"(Error loading agent: {exc}. Available sectors: {available})"


def _exec_read_sector_context(sector: str, **_kw) -> str:
    """Load all available knowledge about a sector from the unified tree."""
    sector = sector.strip().lower()
    logger.info("  \U0001f527 Tool: read_sector_context(%s)", sector)
    sections: list[str] = [f"# Sector Context: {sector.title()}\n"]

    # 1. Knowledge files (sector.md, assets, macro)
    knowledge_files: list[tuple[str, str]] = []
    for md_file in sorted(_KNOWLEDGE_ROOT.rglob("*.md")):
        rel = str(md_file.relative_to(_KNOWLEDGE_ROOT))
        # Skip agent files in this section
        if "/agents/" in rel:
            continue
        if sector in rel.lower() or sector in md_file.stem.lower():
            try:
                content = md_file.read_text(encoding="utf-8")
                if len(content) > 2000:
                    content = content[:2000] + f"\n\n... (truncated, {len(content)} total chars)"
                knowledge_files.append((rel, content))
            except Exception:
                knowledge_files.append((rel, "(read error)"))

    if knowledge_files:
        sections.append("## Sector Knowledge\n")
        for rel, content in knowledge_files:
            sections.extend([f"### {rel}\n", content, ""])
    else:
        sections.append(f"## Sector Knowledge\n\n(No files matching '{sector}')\n")

    # 2. Agent specializations
    from marketsage.agent import discover_all_sectors
    all_sectors = discover_all_sectors()
    matching_sectors: list[tuple[str, list[str]]] = []
    for sp, roles in sorted(all_sectors.items()):
        if sector in sp.lower():
            matching_sectors.append((sp, roles))

    if matching_sectors:
        sections.append("## Agent Specializations\n")
        for sp, roles in matching_sectors:
            sections.append(f"- **{sp}**: {', '.join(roles)}")
            # Show recent learnings
            for role in roles:
                learned = _KNOWLEDGE_ROOT / sp / "agents" / role / "knowledge.md"
                if learned.exists():
                    try:
                        _, body = read_frontmatter(learned)
                        learnings = [l.strip() for l in body.strip().split("\n") if l.strip().startswith("- [")]
                        if learnings:
                            sections.append(f"  *{role} learnings ({len(learnings)}):*")
                            for l in learnings[-3:]:
                                sections.append(f"  {l}")
                    except Exception:
                        pass
            sections.append("")
    else:
        sections.append(f"## Agent Specializations\n\n(No agents matching '{sector}'. "
                        f"Use create_sector_agent to create one.)\n")

    # 3. Scrapers
    matching_scrapers: list[str] = []
    if _CUSTOM_SCRAPERS_DIR.is_dir():
        for cf in sorted(_CUSTOM_SCRAPERS_DIR.glob("*.json")):
            if sector in cf.stem.lower():
                try:
                    with open(cf, encoding="utf-8") as f:
                        cfg = json.load(f)
                    matching_scrapers.append(f"- Custom: **{cf.stem}** \u2014 {cfg.get('description','')}")
                except Exception:
                    matching_scrapers.append(f"- Custom: **{cf.stem}**")
    if matching_scrapers:
        sections.append("## Sector-Specific Tools\n")
        sections.extend(matching_scrapers)
        sections.append("")

    result = "\n".join(sections)
    logger.info("  \u2713 read_sector_context('%s') returned %d chars "
                "(%d knowledge files, %d sector agents)",
                sector, len(result), len(knowledge_files), len(matching_sectors))
    return result


def _exec_create_sector_agent(
    sector_path: str, role: str, sector_description: str,
    key_metrics: str = "", inherits_from: str = "", **_kw,
) -> str:
    """Create a new sector-specialized agent."""
    import re as _re
    logger.info("  \U0001f527 Tool: create_sector_agent(%s, %s)", sector_path, role)
    role = _re.sub(r"[^a-z0-9_]", "_", role.strip().lower())
    sector_path = sector_path.strip().strip("/")

    agent_dir = _KNOWLEDGE_ROOT / sector_path / "agents" / role
    if (agent_dir / "prompt.md").exists():
        return f"(Agent '{sector_path}/agents/{role}' already exists. Use load_agent to view it.)"

    # Validate base role exists
    base_dir = _KNOWLEDGE_ROOT / "agents" / role
    if not (base_dir / "prompt.md").exists():
        from marketsage.agent import discover_sector_agents
        available = discover_sector_agents()
        return f"(Base role '{role}' not found. Available: {available})"

    now = datetime.now(timezone.utc)
    title = sector_path.split("/")[-1].replace("_", " ").title()

    metrics_section = ""
    if key_metrics:
        metrics_section = f"\n## Sector-Specific Metrics\n\n- {key_metrics}\n"

    inherits_line = ""
    if inherits_from:
        inherits_line = f"inherits_from: {inherits_from}\n"

    prompt_content = (
        f"---\ntype: prompt\nrevision: 1\n"
        f"last_modified: {now.isoformat()}\n"
        f"{inherits_line}"
        f'summary: "{title} {role} specialist"\n---\n'
        f"# {title} — {role.title()} Specialist\n\n"
        f"## Sector Focus\n\n{sector_description}\n"
        f"{metrics_section}"
    )
    knowledge_content = (
        f"---\ntype: knowledge\nrevision: 0\n"
        f"last_modified: {now.isoformat()}\n"
        f'summary: "Learnings for {sector_path}/{role}"\n---\n'
        f"# Learnings — {title} {role.title()}\n\n"
    )

    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "prompt.md").write_text(prompt_content, encoding="utf-8")
    (agent_dir / "knowledge.md").write_text(knowledge_content, encoding="utf-8")

    try:
        stamp_and_commit(agent_dir / "prompt.md", f"New agent: {sector_path}/{role}")
    except Exception:
        pass

    logger.info("  \u2713 Created sector agent: %s/agents/%s", sector_path, role)
    return (
        f"\u2713 Created sector agent: {sector_path}/agents/{role}\n"
        f"  \U0001f4c4 Prompt: knowledge/{sector_path}/agents/{role}/prompt.md\n"
        f"  \U0001f4c4 Knowledge: knowledge/{sector_path}/agents/{role}/knowledge.md\n\n"
        f"Use load_agent(role='{role}', sector_path='{sector_path}') to load it."
    )

def _exec_create_sector(sector_path: str, title: str, description: str,
                        inherits: str = "", **_kw) -> str:
    """Create a new sector directory with sector.md and agents/ dir."""
    logger.info("  🔧 Tool: create_sector(%s, title=%s)", sector_path, title)

    sector_dir = _KNOWLEDGE_ROOT / sector_path.strip("/")

    # Safety: prevent path escape
    try:
        sector_dir.resolve().relative_to(_KNOWLEDGE_ROOT.resolve())
    except ValueError:
        return "(Error: path escapes the knowledge directory)"

    sector_file = sector_dir / "sector.md"
    if sector_file.exists():
        return f"(Sector '{sector_path}' already exists — sector.md found.)"

    # Parse inherits
    inherits_list = [
        s.strip() for s in inherits.split(",") if s.strip()
    ] if inherits else []

    # Validate inherited sectors exist
    for inh in inherits_list:
        inh_dir = _KNOWLEDGE_ROOT / inh
        if not inh_dir.is_dir():
            return f"(Error: inherited sector '{inh}' does not exist.)"

    # Build frontmatter
    now = datetime.now(timezone.utc)
    fm_lines = [
        "---",
        f"last_modified: '{now.isoformat()}'",
        "revision: 1",
        f"summary: '{title} sector knowledge'",
        "type: knowledge",
    ]
    if inherits_list:
        fm_lines.append("inherits:")
        for inh in inherits_list:
            fm_lines.append(f"  - {inh}")
    fm_lines.append("---")

    body = f"\n# {title}\n\n{description}\n\n## Learnings\n\n*(none yet)*\n"

    content = "\n".join(fm_lines) + body

    # Create directory structure
    sector_dir.mkdir(parents=True, exist_ok=True)
    (sector_dir / "agents").mkdir(exist_ok=True)
    (sector_dir / "assets").mkdir(exist_ok=True)
    sector_file.write_text(content, encoding="utf-8")

    try:
        stamp_and_commit(sector_file, f"New sector: {sector_path}")
    except Exception:
        pass

    inherits_msg = ""
    if inherits_list:
        inherits_msg = f"\n  🔗 Inherits: {', '.join(inherits_list)}"

    logger.info("  ✓ Created sector: %s", sector_path)
    return (
        f"✓ Created sector: {sector_path}\n"
        f"  📂 Directory: knowledge/{sector_path}/\n"
        f"  📄 Sector knowledge: knowledge/{sector_path}/sector.md\n"
        f"  📂 Agents dir: knowledge/{sector_path}/agents/\n"
        f"  📂 Assets dir: knowledge/{sector_path}/assets/"
        f"{inherits_msg}\n\n"
        f"Use create_sector_agent to add specialized agents to this sector."
    )


def _exec_propose_prompt_change(target_prompt: str, change_type: str,
                                proposed_content: str, reasoning: str,
                                proposed_by: str = "system", **_kw) -> str:
    """Save a prompt change proposal for admin review."""
    logger.info("  🔧 Tool: propose_prompt_change(target=%s, type=%s, by=%s)",
                target_prompt, change_type, proposed_by)

    # Validate change_type
    change_type = change_type.upper().strip()
    if change_type not in ("ADD", "MODIFY", "REMOVE"):
        return "(Error: change_type must be one of: ADD, MODIFY, REMOVE)"

    # Validate target exists
    target_file = _KNOWLEDGE_ROOT / target_prompt.lstrip("/")
    if not target_file.exists():
        return f"(Error: target prompt '{target_prompt}' does not exist.)"

    # Build proposal filename
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    # Extract role and sector from path for readable filename
    parts = target_prompt.replace("/agents/", "/").replace("/prompt.md", "").strip("/")
    safe_name = parts.replace("/", "_")
    filename = f"{safe_name}_{change_type.lower()}_{ts}.md"

    # Build proposal content
    pending_dir = Path(__file__).parent.parent / "pending_updates"
    pending_dir.mkdir(exist_ok=True)

    proposal = (
        f"---\n"
        f"target_file: knowledge/{target_prompt.lstrip('/')}\n"
        f"change_type: {change_type}\n"
        f"proposed_by: {proposed_by}\n"
        f"proposed_at: {now.isoformat()}\n"
        f"status: pending\n"
        f"---\n"
        f"## Proposed Prompt Change: **{change_type}**\n\n"
        f"{proposed_content}\n\n"
        f"## Reasoning\n\n"
        f"{reasoning}\n"
    )

    proposal_path = pending_dir / filename
    proposal_path.write_text(proposal, encoding="utf-8")

    logger.info("  ✓ Proposal saved: %s", filename)
    return (
        f"✓ Prompt change proposal saved for admin review.\n"
        f"  📄 Proposal: pending_updates/{filename}\n"
        f"  🎯 Target: knowledge/{target_prompt}\n"
        f"  📝 Type: {change_type}\n"
        f"  👤 Proposed by: {proposed_by}\n\n"
        f"An admin will review this via: python -m marketsage.admin list"
    )


def _exec_create_custom_scraper(
    name: str,
    description: str,
    base_url: str,
    default_query: str = "",
    default_days_back: int = 30,
    default_max_articles: int = 20,
    **_kw,
) -> str:
    """Create a reusable custom scraper config."""
    import re
    logger.info("  🔧 Tool: create_custom_scraper(%s)", name)

    # Sanitize name
    name = re.sub(r'[^a-z0-9_]', '_', name.strip().lower())
    if not name.startswith("fetch_"):
        name = f"fetch_{name}"

    # Check for conflicts with built-in tools
    builtin_names = {t["name"] for t in TOOL_DECLARATIONS}
    if name in builtin_names:
        return f"(Error: '{name}' conflicts with a built-in tool. Choose a different name.)"

    # Check if custom scraper already exists
    config_file = _CUSTOM_SCRAPERS_DIR / f"{name}.json"
    if config_file.exists():
        return (
            f"(Custom scraper '{name}' already exists. "
            f"It will be loaded automatically on the next run.)"
        )

    # Validate URL
    if not base_url.startswith(("http://", "https://")):
        return f"(Error: base_url must start with http:// or https://. Got: {base_url})"

    # Create the config
    now = datetime.now(timezone.utc).isoformat()
    config = {
        "name": name,
        "description": description,
        "base_url": base_url,
        "default_query": default_query,
        "default_days_back": default_days_back,
        "default_max_articles": default_max_articles,
        "created_at": now,
        "created_by": "marketsage_auto",
    }

    _CUSTOM_SCRAPERS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # Dynamically register it so it's usable in THIS session
    _register_custom_scraper(config)

    logger.info("  ✓ Created custom scraper: %s → %s", name, base_url)
    return (
        f"✓ Created custom scraper: {name}\n"
        f"  🌐 URL: {base_url}\n"
        f"  📝 Description: {description}\n"
        f"  🔍 Default query: {default_query or '(none)'}\n"
        f"  📅 Default days back: {default_days_back}\n"
        f"  📰 Default max articles: {default_max_articles}\n\n"
        f"The tool '{name}' is now available in this session and all "
        f"future runs. Call it like any other scraper tool."
    )


def _exec_propose_new_tool(
    tool_name: str,
    description: str,
    data_source: str,
    rationale: str,
    parameters_needed: str = "",
    **_kw,
) -> str:
    """Propose a new tool for human implementation."""
    logger.info("  🔧 Tool: propose_new_tool(%s)", tool_name)

    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_tool_proposal_{tool_name}.md"
    proposal_file = _PENDING_DIR / fname

    content = (
        f"---\n"
        f"type: tool_proposal\n"
        f"tool_name: {tool_name}\n"
        f"data_source: {data_source}\n"
        f"proposed_at: {now.isoformat()}\n"
        f"proposed_by: marketsage_auto\n"
        f"status: pending\n"
        f"---\n"
        f"# Tool Proposal: {tool_name}\n\n"
        f"## Description\n\n{description}\n\n"
        f"## Data Source\n\n{data_source}\n\n"
        f"## Parameters Needed\n\n{parameters_needed or '(not specified)'}\n\n"
        f"## Rationale\n\n{rationale}\n\n"
        f"## Implementation Notes\n\n"
        f"This tool was proposed by MarketSage during an analysis session. "
        f"It requires custom Python implementation because the generic "
        f"web scraper is not sufficient.\n\n"
        f"To implement:\n"
        f"1. Create `marketsage/scrapers/{tool_name.replace('fetch_', '')}.py`\n"
        f"2. Add the function to `marketsage/tools.py` (declaration + implementation)\n"
        f"3. Register in TOOL_REGISTRY\n"
    )
    proposal_file.write_text(content, encoding="utf-8")

    logger.info("  ✓ Tool proposal saved: %s", proposal_file)
    return (
        f"✓ Tool proposal saved for human review:\n"
        f"  📄 {proposal_file.relative_to(Path(__file__).parent.parent)}\n\n"
        f"A developer will review this proposal and implement the tool. "
        f"In the meantime, try using fetch_web_news with the data source "
        f"URL directly if possible."
    )


# ---------------------------------------------------------------------------
# Custom scraper loading — reads JSON configs from custom_scrapers/
# ---------------------------------------------------------------------------

def _register_custom_scraper(config: dict) -> None:
    """Register a custom scraper config as a live tool (declaration + implementation)."""
    name = config["name"]

    # Skip if already registered
    if name in TOOL_REGISTRY:
        return

    # Create a closure for execution
    def _make_exec(cfg: dict):
        def _exec(query: str = "", days_back: int = 0,
                  max_articles: int = 0, **_kw) -> str:
            effective_query = query or cfg.get("default_query", "")
            effective_days = days_back or cfg.get("default_days_back", 30)
            effective_max = max_articles or cfg.get("default_max_articles", 20)
            logger.info("  🔧 Tool: %s(query=%s, days_back=%d)",
                        cfg["name"], effective_query, effective_days)
            return _exec_fetch_web_news(
                url=cfg["base_url"],
                query=effective_query,
                days_back=effective_days,
                max_articles=effective_max,
            )
        return _exec

    TOOL_REGISTRY[name] = _make_exec(config)
    logger.debug("  Registered custom scraper: %s", name)


def _load_custom_scrapers() -> list[dict[str, Any]]:
    """
    Load all custom scraper configs from the custom_scrapers/ directory.

    Returns a list of tool declarations for the custom scrapers,
    and registers their implementations in TOOL_REGISTRY.
    """
    custom_declarations: list[dict[str, Any]] = []

    if not _CUSTOM_SCRAPERS_DIR.is_dir():
        return custom_declarations

    for config_file in sorted(_CUSTOM_SCRAPERS_DIR.glob("*.json")):
        try:
            with open(config_file, encoding="utf-8") as f:
                config = json.load(f)

            name = config["name"]
            description = config.get("description", f"Custom scraper: {name}")

            # Build tool declaration
            declaration = {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                f"Search query to filter articles. "
                                f"Default: '{config.get('default_query', '')}'."
                            ),
                        },
                        "days_back": {
                            "type": "integer",
                            "description": (
                                f"How far back to look. "
                                f"Default: {config.get('default_days_back', 30)}."
                            ),
                        },
                        "max_articles": {
                            "type": "integer",
                            "description": (
                                f"Maximum articles to fetch. "
                                f"Default: {config.get('default_max_articles', 20)}."
                            ),
                        },
                    },
                },
            }
            custom_declarations.append(declaration)

            # Register the implementation
            _register_custom_scraper(config)

            logger.debug("Loaded custom scraper: %s (%s)",
                         name, config.get("base_url", "?"))

        except Exception as exc:
            logger.warning("Failed to load custom scraper %s: %s",
                           config_file.name, exc)

    return custom_declarations


# ---------------------------------------------------------------------------
# Generated tool loading — reads Python modules from generated_tools/
# ---------------------------------------------------------------------------

_GENERATED_TOOLS_DIR = Path(__file__).parent / "generated_tools"


def _load_generated_tools() -> list[dict[str, Any]]:
    """
    Load all generated tool modules from the generated_tools/ directory.

    Each generated tool has:
    - A ``.json`` manifest with the tool declaration and metadata
    - A ``.py`` module exposing a ``fetch(**kwargs) → list[dict]`` function

    Returns a list of tool declarations and registers implementations
    in TOOL_REGISTRY.
    """
    import importlib.util

    generated_declarations: list[dict[str, Any]] = []

    if not _GENERATED_TOOLS_DIR.is_dir():
        return generated_declarations

    for json_file in sorted(_GENERATED_TOOLS_DIR.glob("*.json")):
        tool_name = json_file.stem
        py_file = _GENERATED_TOOLS_DIR / f"{tool_name}.py"

        # Skip if already registered (avoid double-loading)
        if tool_name in TOOL_REGISTRY:
            # Still need to return the declaration
            try:
                with open(json_file, encoding="utf-8") as f:
                    manifest = json.load(f)
                if manifest.get("metadata", {}).get("status") == "active":
                    generated_declarations.append(manifest["declaration"])
            except Exception:
                pass
            continue

        if not py_file.exists():
            logger.warning("Generated tool %s has .json but no .py — skipping",
                           tool_name)
            continue

        try:
            # Load manifest
            with open(json_file, encoding="utf-8") as f:
                manifest = json.load(f)

            # Skip inactive tools
            if manifest.get("metadata", {}).get("status") != "active":
                logger.debug("Skipping inactive generated tool: %s", tool_name)
                continue

            declaration = manifest["declaration"]

            # Import the Python module
            module_name = f"marketsage.generated_tools.{tool_name}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("Could not create spec for %s", py_file)
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if not hasattr(mod, "fetch") or not callable(mod.fetch):
                logger.warning("Generated tool %s has no callable fetch() — skipping",
                               tool_name)
                continue

            # Create a wrapper that calls fetch() and formats the result
            def _make_generated_exec(module, name):
                def _exec(**kwargs) -> str:
                    logger.info("  🔧 Tool: %s(%s)", name, kwargs)
                    try:
                        records = module.fetch(**kwargs)
                    except Exception as exc:
                        logger.error("  ✗ Generated tool %s failed: %s",
                                     name, exc, exc_info=True)
                        return f"(Error: generated tool {name} failed: {exc})"
                    if not records:
                        return f"(⚠ No data returned from {name}.)"
                    # Format records
                    from marketsage.data_service import (
                        format_articles_for_llm,
                        format_generic_for_llm,
                    )
                    # Try article format first (if records have 'body' key)
                    if records and isinstance(records[0], dict):
                        if "body" in records[0]:
                            text = format_articles_for_llm(records)
                        else:
                            text = format_generic_for_llm(records,
                                                         source_name=name)
                    else:
                        text = format_generic_for_llm(records,
                                                     source_name=name)
                    logger.info("  ✓ %s returned %d records, %d chars",
                                name, len(records), len(text))
                    return text
                return _exec

            TOOL_REGISTRY[tool_name] = _make_generated_exec(mod, tool_name)
            generated_declarations.append(declaration)
            logger.debug("Loaded generated tool: %s (%s)",
                         tool_name, py_file.name)

        except Exception as exc:
            logger.warning("Failed to load generated tool %s: %s",
                           json_file.name, exc)

    return generated_declarations


# ---------------------------------------------------------------------------
# Public API — all declarations (built-in + custom + generated)
# ---------------------------------------------------------------------------

def get_all_tool_declarations() -> list[dict[str, Any]]:
    """
    Return the complete list of tool declarations:
    built-in + custom scrapers + generated tools.

    Custom scrapers are loaded from JSON configs in custom_scrapers/.
    Generated tools are loaded from Python modules in generated_tools/.
    Call this instead of using TOOL_DECLARATIONS directly.
    """
    custom = _load_custom_scrapers()
    generated = _load_generated_tools()
    return TOOL_DECLARATIONS + custom + generated


# ---------------------------------------------------------------------------
# Execution registry — maps tool names to Python callables
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, callable] = {
    "fetch_ceo_ca": _exec_fetch_ceo_ca,
    "fetch_yahoo_finance": _exec_fetch_yahoo_finance,
    "fetch_fred": _exec_fetch_fred,
    "fetch_nfg_news": _exec_fetch_nfg_news,
    "fetch_web_news": _exec_fetch_web_news,
    "read_knowledge": _exec_read_knowledge,
    "list_knowledge": _exec_list_knowledge,
    "persist_learning": _exec_persist_learning,
    "list_available_scrapers": _exec_list_available_scrapers,
    "load_agent": _exec_load_agent,
    "create_sector_agent": _exec_create_sector_agent,
    "create_sector": _exec_create_sector,
    "propose_prompt_change": _exec_propose_prompt_change,
    "create_custom_scraper": _exec_create_custom_scraper,
    "propose_new_tool": _exec_propose_new_tool,
    "read_sector_context": _exec_read_sector_context,
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


# ---------------------------------------------------------------------------
# Tools registry generator
# ---------------------------------------------------------------------------

def refresh_tools_registry() -> Path:
    """
    Generate ``marketsage/tools_registry.yaml`` containing every tool
    currently available in the system (built-in + custom + generated).

    Each entry includes: name, description, type, parameters, and timestamp.
    Returns the path to the written file.
    """
    import yaml
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    # Gather all declarations
    all_decls = get_all_tool_declarations()
    builtin_names = {t["name"] for t in TOOL_DECLARATIONS}
    custom_names = {t["name"] for t in _load_custom_scrapers()}
    # Everything else is generated

    entries = []
    for decl in all_decls:
        name = decl["name"]

        # Determine source type
        if name in builtin_names:
            tool_type = "built-in"
        elif name in custom_names:
            tool_type = "custom_scraper"
        else:
            tool_type = "generated"

        # Get parameter names
        props = decl.get("parameters", {}).get("properties", {})
        params = list(props.keys()) if props else []
        required = decl.get("parameters", {}).get("required", [])

        # Timestamp: for generated tools, read from the JSON manifest
        timestamp = None
        if tool_type == "generated":
            json_path = Path(__file__).parent / "generated_tools" / f"{name}.json"
            if json_path.exists():
                try:
                    manifest = json.loads(json_path.read_text(encoding="utf-8"))
                    timestamp = manifest.get("metadata", {}).get("generated_at")
                except Exception:
                    pass
        elif tool_type == "custom_scraper":
            cfg_path = _CUSTOM_SCRAPERS_DIR / f"{name}.json"
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    timestamp = cfg.get("created_at")
                except Exception:
                    pass

        entry: dict[str, Any] = {
            "name": name,
            "type": tool_type,
            "description": decl.get("description", "(no description)"),
            "parameters": params,
            "required_params": required,
        }
        if timestamp:
            entry["created_at"] = timestamp

        entries.append(entry)

    output = {
        "last_updated": now,
        "tool_count": len(entries),
        "by_type": {
            "built-in": sum(1 for e in entries if e["type"] == "built-in"),
            "custom_scraper": sum(1 for e in entries if e["type"] == "custom_scraper"),
            "generated": sum(1 for e in entries if e["type"] == "generated"),
        },
        "tools": entries,
    }

    out_path = Path(__file__).parent / "tools_registry.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True, width=120)

    return out_path


if __name__ == "__main__":
    path = refresh_tools_registry()
    print(f"✓ Written to {path}")

