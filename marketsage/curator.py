"""
MarketSage — Knowledge Curator.

The "Memory Manager" of the system.  Sits between the Expert Committee
and the physical Markdown vault, converting raw reports into high-level
heuristics via hierarchical summarisation, deduplication, and conflict
resolution.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from marketsage.config import (
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
    DEDUP_SIMILARITY_THRESHOLD,
    MACRO_BUBBLE_KEYWORDS,
    SECTION_CHRONOLOGICAL_LOG,
    SECTION_CONTRADICTIONS,
    SECTION_EXECUTIVE_SUMMARY,
    SECTION_KEY_HEURISTICS,
    SECTOR_BUBBLE_KEYWORDS,
    VAULT_SECTIONS,
)
from marketsage.models import (
    ConflictReport,
    ConflictStatus,
    Insight,
    InsightLevel,
    Sentiment,
)


# ───────────────────────────────────────────────────────────────────────
# Markdown I/O
# ───────────────────────────────────────────────────────────────────────

def read_vault_file(path: Path) -> str:
    """Read a vault Markdown file, returning its full text."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_vault_file(path: Path, content: str) -> None:
    """Write *content* to a vault Markdown file, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_sections(text: str) -> dict[str, str]:
    """
    Split Markdown into ``{section_name: body}`` keyed by ``## Header``.

    Everything before the first ``## `` header is stored under the key
    ``"_preamble"`` (the ``# Title`` line, metadata tables, etc.).
    """
    sections: dict[str, str] = {}
    current_key = "_preamble"
    current_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            sections[current_key] = "".join(current_lines)
            current_key = line.lstrip("# ").strip()
            current_lines = []
        else:
            current_lines.append(line)

    sections[current_key] = "".join(current_lines)
    return sections


def rebuild_markdown(sections: dict[str, str], title_key: str = "_preamble") -> str:
    """
    Re-assemble parsed sections into a single Markdown string.

    Canonical section order is enforced.
    """
    parts: list[str] = []

    # preamble first (title, table, etc.)
    preamble = sections.get(title_key, "").rstrip("\n")
    if preamble:
        parts.append(preamble)
        parts.append("")

    # canonical sections in order
    for sec in VAULT_SECTIONS:
        body = sections.get(sec, "").strip()
        parts.append(f"## {sec}")
        parts.append("")
        if body:
            parts.append(body)
        else:
            parts.append("*(empty)*")
        parts.append("")

    # any extra sections not in the canonical list
    for key, body in sections.items():
        if key == title_key or key in VAULT_SECTIONS:
            continue
        parts.append(f"## {key}")
        parts.append("")
        parts.append(body.strip() if body.strip() else "*(empty)*")
        parts.append("")

    return "\n".join(parts) + "\n"


# ───────────────────────────────────────────────────────────────────────
# Deduplication
# ───────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens, stripping punctuation."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard coefficient between two text snippets."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_duplicate(
    new_text: str,
    existing_lines: list[str],
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
) -> bool:
    """Return True if *new_text* is too similar to any existing line."""
    for line in existing_lines:
        if jaccard_similarity(new_text, line) >= threshold:
            return True
    return False


# ───────────────────────────────────────────────────────────────────────
# Sentiment detection
# ───────────────────────────────────────────────────────────────────────

def detect_sentiment(text: str) -> Sentiment:
    """Quick keyword-based sentiment scoring."""
    lower = text.lower()
    bull = sum(1 for kw in BULLISH_KEYWORDS if kw in lower)
    bear = sum(1 for kw in BEARISH_KEYWORDS if kw in lower)
    if bull and bear:
        return Sentiment.MIXED
    if bull > bear:
        return Sentiment.BULLISH
    if bear > bull:
        return Sentiment.BEARISH
    return Sentiment.NEUTRAL


# ───────────────────────────────────────────────────────────────────────
# Conflict detection & resolution
# ───────────────────────────────────────────────────────────────────────

def detect_conflict(
    incoming: Insight,
    existing_text: str,
    file_path: str,
) -> Optional[ConflictReport]:
    """
    Check if the incoming insight's sentiment contradicts the vault's
    current Executive Summary / Key Heuristics.
    """
    existing_sentiment = detect_sentiment(existing_text)
    incoming_sentiment = incoming.sentiment

    # Only flag direct contradictions (bullish vs bearish)
    contradicts = (
        (existing_sentiment == Sentiment.BULLISH
         and incoming_sentiment == Sentiment.BEARISH)
        or
        (existing_sentiment == Sentiment.BEARISH
         and incoming_sentiment == Sentiment.BULLISH)
    )
    if not contradicts:
        return None

    return ConflictReport(
        file_path=file_path,
        existing_sentiment=existing_sentiment,
        incoming_sentiment=incoming_sentiment,
        existing_text=existing_text[:500],
        incoming_text=incoming.headline,
    )


def resolve_conflict(
    conflict: ConflictReport,
    trader_opinion: str = "",
    executive_opinion: str = "",
) -> ConflictReport:
    """
    Resolve a conflict by combining Trader and Executive opinions.
    In a production system this would invoke the actual agents; here
    we record the resolution.
    """
    resolution_parts = []
    if trader_opinion:
        resolution_parts.append(f"**Trader:** {trader_opinion}")
    if executive_opinion:
        resolution_parts.append(f"**Executive:** {executive_opinion}")

    resolution = " | ".join(resolution_parts) if resolution_parts else (
        "Auto-resolved: newer data supersedes older assessment."
    )
    conflict.resolve(
        resolution=resolution,
        resolved_by=["Market Strategist", "Industry Executive"],
    )
    return conflict


# ───────────────────────────────────────────────────────────────────────
# Bubble-up logic
# ───────────────────────────────────────────────────────────────────────

def should_bubble_to_sector(insight: Insight) -> bool:
    """Does this asset-level insight have sector-wide implications?"""
    combined = f"{insight.headline} {insight.body}".lower()
    return any(kw in combined for kw in SECTOR_BUBBLE_KEYWORDS)


def should_bubble_to_macro(insight: Insight) -> bool:
    """Does this insight have industry / macro implications?"""
    combined = f"{insight.headline} {insight.body}".lower()
    return any(kw in combined for kw in MACRO_BUBBLE_KEYWORDS)


# ───────────────────────────────────────────────────────────────────────
# Section updaters
# ───────────────────────────────────────────────────────────────────────

def _extract_bullet_lines(section_body: str) -> list[str]:
    """Extract lines starting with ``- `` from a section body."""
    return [
        ln.strip()
        for ln in section_body.splitlines()
        if ln.strip().startswith("- ") and "awaiting" not in ln.lower()
    ]


def append_to_log(sections: dict[str, str], entry: str) -> None:
    """Add a timestamped entry to the Chronological Log section."""
    existing = sections.get(SECTION_CHRONOLOGICAL_LOG, "").rstrip()
    if existing in ("", "*(empty)*"):
        existing = ""
    sections[SECTION_CHRONOLOGICAL_LOG] = f"{existing}\n{entry}\n"


def add_heuristic(sections: dict[str, str], bullet: str) -> bool:
    """
    Add a bullet to Key Heuristics if not a duplicate.
    Returns True if added, False if skipped.
    """
    existing = sections.get(SECTION_KEY_HEURISTICS, "")
    existing_bullets = _extract_bullet_lines(existing)

    if is_duplicate(bullet, existing_bullets):
        return False

    if existing.strip() in ("", "*(empty)*", "- *(awaiting first analysis run)*"):
        sections[SECTION_KEY_HEURISTICS] = f"- {bullet}\n"
    else:
        sections[SECTION_KEY_HEURISTICS] = f"{existing.rstrip()}\n- {bullet}\n"
    return True


def record_contradiction(sections: dict[str, str], conflict: ConflictReport) -> None:
    """Append a conflict record to the Contradictions section."""
    existing = sections.get(SECTION_CONTRADICTIONS, "").rstrip()
    if existing in ("", "*(empty)*", "*(none recorded)*"):
        existing = ""

    ts = conflict.detected_at.strftime("%Y-%m-%d %H:%M")
    status = conflict.status.value
    entry = (
        f"- [{ts}] **{conflict.existing_sentiment.value}** vs "
        f"**{conflict.incoming_sentiment.value}** — "
        f"Status: {status}"
    )
    if conflict.resolution:
        entry += f"\n  - Resolution: {conflict.resolution}"

    sections[SECTION_CONTRADICTIONS] = f"{existing}\n{entry}\n"


def regenerate_executive_summary(sections: dict[str, str]) -> None:
    """
    Synthesise the Executive Summary from Key Heuristics.

    Takes the first 5 heuristic bullets and presents them as the
    summary.  In a production system this would invoke an LLM.
    """
    bullets = _extract_bullet_lines(
        sections.get(SECTION_KEY_HEURISTICS, "")
    )
    if not bullets:
        sections[SECTION_EXECUTIVE_SUMMARY] = (
            "*No data yet. This section is auto-generated by the Knowledge Curator.*"
        )
        return

    top = bullets[-5:]  # most recent five
    summary_lines = ["**Auto-generated from latest heuristics:**", ""]
    for b in top:
        summary_lines.append(b)
    sections[SECTION_EXECUTIVE_SUMMARY] = "\n".join(summary_lines)


# ───────────────────────────────────────────────────────────────────────
# Top-level Curator API
# ───────────────────────────────────────────────────────────────────────

class KnowledgeCurator:
    """
    The Memory Manager of the MarketSage system.

    Usage::

        curator = KnowledgeCurator()
        results = curator.curate(
            insight,
            asset_path=Path("vault/.../nfgc.md"),
            sector_path=Path("vault/.../gold_sector.md"),
            macro_path=Path("vault/.../commodities_macro.md"),
        )
    """

    def curate(
        self,
        insight: Insight,
        asset_path: Path,
        sector_path: Optional[Path] = None,
        macro_path: Optional[Path] = None,
    ) -> dict:
        """
        Process a single Insight through the full curation pipeline.

        Returns a dict summarising what happened::

            {
                "asset_updated": bool,
                "sector_updated": bool,
                "macro_updated": bool,
                "duplicate": bool,
                "conflict": ConflictReport | None,
            }
        """
        result = {
            "asset_updated": False,
            "sector_updated": False,
            "macro_updated": False,
            "duplicate": False,
            "conflict": None,
        }

        # ── Level 1: Asset update ──────────────────────────────────
        asset_updated, conflict = self._update_file(
            path=asset_path,
            insight=insight,
        )
        result["asset_updated"] = asset_updated
        result["duplicate"] = not asset_updated
        result["conflict"] = conflict

        if not asset_updated:
            return result

        # ── Level 2: Sector bubble-up ─────────────────────────────
        if sector_path and should_bubble_to_sector(insight):
            sector_insight = Insight(
                source_agent=insight.source_agent,
                ticker=insight.ticker,
                level=InsightLevel.SECTOR,
                sentiment=insight.sentiment,
                headline=f"[Sector] {insight.headline}",
                body=insight.body,
                confidence=insight.confidence * 0.9,
                tags=insight.tags + ["bubble-up"],
                raw_source=insight.raw_source,
            )
            sector_updated, _ = self._update_file(
                path=sector_path,
                insight=sector_insight,
            )
            result["sector_updated"] = sector_updated

        # ── Level 3: Macro / Industry bubble-up ───────────────────
        if macro_path and should_bubble_to_macro(insight):
            macro_insight = Insight(
                source_agent=insight.source_agent,
                ticker=insight.ticker,
                level=InsightLevel.INDUSTRY,
                sentiment=insight.sentiment,
                headline=f"[Macro] {insight.headline}",
                body=insight.body,
                confidence=insight.confidence * 0.8,
                tags=insight.tags + ["bubble-up", "macro"],
                raw_source=insight.raw_source,
            )
            macro_updated, _ = self._update_file(
                path=macro_path,
                insight=macro_insight,
            )
            result["macro_updated"] = macro_updated

        return result

    # ── Private helpers ────────────────────────────────────────────

    def _update_file(
        self,
        path: Path,
        insight: Insight,
    ) -> tuple[bool, Optional[ConflictReport]]:
        """
        Insert an insight into a single vault Markdown file.

        Returns ``(was_updated, conflict_or_none)``.
        """
        text = read_vault_file(path)
        sections = parse_sections(text) if text else self._empty_sections()

        # Dedup check
        existing_bullets = _extract_bullet_lines(
            sections.get(SECTION_KEY_HEURISTICS, "")
        )
        if is_duplicate(insight.headline, existing_bullets):
            return False, None

        # Conflict check
        exec_summary = sections.get(SECTION_EXECUTIVE_SUMMARY, "")
        heuristics = sections.get(SECTION_KEY_HEURISTICS, "")
        conflict = detect_conflict(
            insight,
            existing_text=f"{exec_summary}\n{heuristics}",
            file_path=str(path),
        )
        if conflict:
            conflict = resolve_conflict(conflict)
            record_contradiction(sections, conflict)

        # Add heuristic bullet
        add_heuristic(sections, insight.headline)

        # Append chronological log entry
        append_to_log(sections, insight.summary_line)

        # Re-synthesise executive summary
        regenerate_executive_summary(sections)

        # Write back
        write_vault_file(path, rebuild_markdown(sections))
        return True, conflict

    @staticmethod
    def _empty_sections() -> dict[str, str]:
        return {s: "" for s in VAULT_SECTIONS}
