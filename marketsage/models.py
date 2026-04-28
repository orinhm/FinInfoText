"""
MarketSage — domain models.

Dataclasses that flow between agents, the aggregator, and the curator.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── Enumerations ───────────────────────────────────────────────────────

class Sentiment(Enum):
    """Market sentiment polarity."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class InsightLevel(Enum):
    """Which Knowledge Vault level an insight targets."""
    ASSET = "asset"       # Level 1 — single ticker
    SECTOR = "sector"     # Level 2 — e.g. gold mining
    INDUSTRY = "industry" # Level 3 — e.g. commodities macro


class ConflictStatus(Enum):
    """Lifecycle of a detected contradiction."""
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"


class LassondeStage(Enum):
    """Position on the Lassonde Curve."""
    CONCEPT = "Concept / Discovery"
    SPECULATION = "Speculative Enthusiasm"
    ORPHAN = "Orphan Period / PEA"
    DEVELOPMENT = "Development / Feasibility"
    CONSTRUCTION = "Construction"
    PRODUCTION = "Production"
    MATURE = "Mature Operation"


class ExpertRole(Enum):
    """Functional archetype of an expert agent."""
    LIBRARIAN = "Librarian"
    AUDITOR = "Scientific Auditor"
    EXECUTIVE = "Industry Executive"
    TRADER = "Market Strategist"
    ACCOUNTANT = "Accountant"


# ── Core data objects ──────────────────────────────────────────────────

@dataclass
class Insight:
    """A single piece of distilled knowledge produced by an expert."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=datetime.now)
    source_agent: str = ""
    ticker: str = ""
    level: InsightLevel = InsightLevel.ASSET
    sentiment: Sentiment = Sentiment.NEUTRAL
    headline: str = ""
    body: str = ""
    confidence: float = 0.5  # 0.0 – 1.0
    tags: list[str] = field(default_factory=list)
    raw_source: str = ""  # e.g. "ceo_ca/nfg", "newfoundgold.ca"

    @property
    def summary_line(self) -> str:
        """One-line representation for log entries."""
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M")
        conf = f"{self.confidence:.0%}"
        return (
            f"[{ts}] ({self.source_agent}, {self.sentiment.value}, "
            f"conf={conf}) {self.headline}"
        )


@dataclass
class ExpertOpinion:
    """Structured output from an Expert Committee agent."""
    agent_role: ExpertRole
    agent_name: str = ""
    ticker: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    insights: list[Insight] = field(default_factory=list)
    raw_analysis: str = ""  # free-form text
    metrics: dict = field(default_factory=dict)  # e.g. {"aisc": 2429, ...}


@dataclass
class ConflictReport:
    """Record of a detected contradiction in the Knowledge Vault."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    detected_at: datetime = field(default_factory=datetime.now)
    file_path: str = ""
    existing_sentiment: Sentiment = Sentiment.NEUTRAL
    incoming_sentiment: Sentiment = Sentiment.NEUTRAL
    existing_text: str = ""
    incoming_text: str = ""
    resolution: str = ""
    resolved_by: list[str] = field(default_factory=list)
    status: ConflictStatus = ConflictStatus.OPEN

    def resolve(self, resolution: str, resolved_by: list[str]) -> None:
        self.resolution = resolution
        self.resolved_by = resolved_by
        self.status = ConflictStatus.RESOLVED


@dataclass
class AssetProfile:
    """Snapshot of asset-level knowledge held in the vault."""
    ticker: str = ""
    name: str = ""
    sector: str = ""
    sub_sector: str = ""
    vault_path: str = ""
    lassonde_stage: LassondeStage = LassondeStage.CONCEPT
    current_sentiment: Sentiment = Sentiment.NEUTRAL
    key_metrics: dict = field(default_factory=dict)
    heuristic_count: int = 0
    last_updated: Optional[datetime] = None


@dataclass
class SectorProfile:
    """Snapshot of sector-level knowledge held in the vault."""
    name: str = ""
    vault_path: str = ""
    asset_count: int = 0
    current_sentiment: Sentiment = Sentiment.NEUTRAL
    key_themes: list[str] = field(default_factory=list)
    last_updated: Optional[datetime] = None


@dataclass
class StateReport:
    """Aggregated 'Current State Report' produced by the pipeline."""
    ticker: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    opinions: list[ExpertOpinion] = field(default_factory=list)
    composite_sentiment: Sentiment = Sentiment.NEUTRAL
    composite_confidence: float = 0.5
    executive_summary: str = ""
    new_insights: list[Insight] = field(default_factory=list)
    conflicts: list[ConflictReport] = field(default_factory=list)
