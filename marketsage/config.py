"""
MarketSage — global configuration.

Central constants for paths, templates, and system defaults.
"""

from pathlib import Path

# ── Project roots ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT_ROOT = PROJECT_ROOT / "vault"
MARKETSAGE_ROOT = Path(__file__).resolve().parent

# ── Vault paths (relative to VAULT_ROOT) ───────────────────────────────
COMMODITIES_DIR = VAULT_ROOT / "commodities"
PRECIOUS_METALS_DIR = COMMODITIES_DIR / "precious_metals"
GOLD_DIR = PRECIOUS_METALS_DIR / "gold"
GOLD_ASSETS_DIR = GOLD_DIR / "assets"

# Well-known vault files
COMMODITIES_MACRO_FILE = COMMODITIES_DIR / "commodities_macro.md"
GOLD_SECTOR_FILE = GOLD_DIR / "gold_sector.md"
VAULT_INDEX_FILE = VAULT_ROOT / "_index.json"

# ── Tools registry ─────────────────────────────────────────────────────
TOOLS_REGISTRY_FILE = MARKETSAGE_ROOT / "tools_registry.json"

# ── Scraped data directory (existing nfgc.py outputs) ──────────────────
DATA_DIR = PROJECT_ROOT

# ── Markdown section headers (canonical names) ─────────────────────────
SECTION_EXECUTIVE_SUMMARY = "Executive Summary"
SECTION_KEY_HEURISTICS = "Key Heuristics"
SECTION_CHRONOLOGICAL_LOG = "Chronological Log"
SECTION_CONTRADICTIONS = "Contradictions & Resolutions"

VAULT_SECTIONS = [
    SECTION_EXECUTIVE_SUMMARY,
    SECTION_KEY_HEURISTICS,
    SECTION_CHRONOLOGICAL_LOG,
    SECTION_CONTRADICTIONS,
]

# ── Sentiment keywords ─────────────────────────────────────────────────
BULLISH_KEYWORDS = [
    "bullish", "long", "buy", "higher", "breakout", "moon",
    "undervalued", "accumulate", "strong", "upside", "rally",
    "squeeze", "rocket", "explosive", "record",
]
BEARISH_KEYWORDS = [
    "bearish", "short", "sell", "lower", "breakdown", "dump",
    "overvalued", "dilution", "weak", "downside", "crash",
    "bag", "risk", "caution", "warning",
]

# ── Bubble-up heuristics ───────────────────────────────────────────────
# Keywords that indicate an insight should propagate to sector level
SECTOR_BUBBLE_KEYWORDS = [
    "technique", "method", "regulation", "permit", "policy",
    "discovery", "technology", "industry", "sector", "trend",
    "grade control", "recovery rate", "processing",
]

# Keywords that indicate an insight should propagate to macro/industry level
MACRO_BUBBLE_KEYWORDS = [
    "macro", "global", "tariff", "interest rate", "central bank",
    "inflation", "geopolit", "sanction", "supply chain",
    "reserve crisis", "gold price", "commodity cycle",
]

# ── Deduplication ──────────────────────────────────────────────────────
DEDUP_SIMILARITY_THRESHOLD = 0.60  # Jaccard coefficient

# ── Lassonde Curve stages ──────────────────────────────────────────────
LASSONDE_STAGES = [
    "Concept / Discovery",
    "Speculative Enthusiasm",
    "Orphan Period / PEA",
    "Development / Feasibility",
    "Construction",
    "Production",
    "Mature Operation",
]

# ── Mining geology heuristics ──────────────────────────────────────────
GRADE_ANOMALY_THRESHOLDS = {
    "gold_gpt": {
        "low": 0.5,
        "medium": 5.0,
        "high": 20.0,
        "extreme": 100.0,      # g/t; >100 warrants scrutiny
        "bonanza": 500.0,       # g/t; very rare, flag for review
    },
}

# ── Agent names ────────────────────────────────────────────────────────
AGENT_LIBRARIAN = "Librarian"
AGENT_AUDITOR = "Scientific Auditor"
AGENT_EXECUTIVE = "Industry Executive"
AGENT_TRADER = "Market Strategist"
AGENT_ACCOUNTANT = "Accountant"
AGENT_CURATOR = "Knowledge Curator"
