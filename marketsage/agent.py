"""
Sector-first Agent engine — agents are composed from base roles + sector
specializations, with cross-sector inheritance support.

An agent is loaded by building a **resolution chain** from:
  1. The sector's own ``agents/{role}/`` files
  2. Walking up the directory tree (parent sectors)
  3. Cross-sector inherited sectors (declared in ``sector.md`` frontmatter)
  4. The root-level ``agents/{role}/`` (universal base)

Composition rules:
  - **prompt.md** → Override (most-specific wins)
  - **knowledge.md** → Merge (all levels accumulated)
  - **sector.md** → Merge (all levels accumulated)
  - **Supplementary .md files** → Merge (all collected)

Multi-sector assets declare ``sectors:`` in their frontmatter, which
tells the orchestrator to load sector context from multiple branches.

Usage::

    agent = Agent(role="executive", sector_path="commodities/precious_metals/gold")
    response = agent.analyze(data_text, user_request, llm_client)

    # Base-only (no sector specialization)
    agent = Agent(role="geopolitical")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from marketsage.versioning import read_frontmatter

logger = logging.getLogger("marketsage.agent")

if TYPE_CHECKING:
    from marketsage.llm_client import LLMClient

# The ``knowledge/`` root lives at the project root.
KNOWLEDGE_ROOT = Path(__file__).parent.parent / "knowledge"


# ---------------------------------------------------------------------------
# File loading helpers
# ---------------------------------------------------------------------------

def _load_prompt(path: Path) -> str:
    """Load a prompt.md file, stripping frontmatter."""
    if path.exists():
        _, body = read_frontmatter(path)
        return body.strip()
    return ""


def _load_knowledge_file(path: Path) -> str:
    """Load a knowledge.md file, stripping frontmatter."""
    if path.exists():
        _, body = read_frontmatter(path)
        return body.strip()
    return ""


def _load_supplementary_files(agent_dir: Path) -> list[tuple[str, str]]:
    """
    Load all .md files from an agent directory EXCEPT prompt.md and knowledge.md.
    These are curated reference docs (e.g., strategy_patterns.md, sources.md).
    Returns [(name, content)].
    """
    results = []
    if agent_dir.is_dir():
        for md in sorted(agent_dir.glob("*.md")):
            if md.name in ("prompt.md", "knowledge.md"):
                continue
            _, body = read_frontmatter(md)
            content = body.strip()
            if content:
                results.append((md.stem, content))
    return results


def _load_sector_md(sector_path: Path) -> str:
    """Load sector.md from a directory, stripping frontmatter."""
    sector_file = sector_path / "sector.md"
    if sector_file.exists():
        _, body = read_frontmatter(sector_file)
        return body.strip()
    return ""


def _read_inherits(sector_path: Path) -> list[str]:
    """
    Read the ``inherits:`` list from a sector.md frontmatter.
    Returns a list of relative sector paths, e.g. ['commodities/precious_metals'].
    """
    sector_file = sector_path / "sector.md"
    if not sector_file.exists():
        return []
    fm, _ = read_frontmatter(sector_file)
    inherits = fm.get("inherits", [])
    if isinstance(inherits, str):
        inherits = [inherits]
    return inherits or []


# ---------------------------------------------------------------------------
# Resolution chain builder
# ---------------------------------------------------------------------------

def resolve_sector_chain(sector_path: str | None) -> list[str]:
    """
    Build the ordered list of sector paths to compose knowledge from.

    Resolution order:
      1. The sector itself
      2. Walk up parent directories (natural inheritance)
      3. Cross-sector inherits (declared in sector.md frontmatter)
         — including THEIR parents, recursively
      4. Root (always included, deduplicated)

    The empty string '' represents the root level.

    Returns paths relative to KNOWLEDGE_ROOT, e.g.:
        ['equities/mining', 'equities', 'commodities/precious_metals',
         'commodities', '']

    Parameters
    ----------
    sector_path : str or None
        Sector path relative to KNOWLEDGE_ROOT, e.g.
        'equities/mining'. If None, returns just the root.
    """
    if not sector_path:
        return [""]

    seen: set[str] = set()
    chain: list[str] = []

    def _add(path: str) -> None:
        if path not in seen:
            seen.add(path)
            chain.append(path)

    # 1. The sector itself
    _add(sector_path)

    # 2. Walk up parent directories
    parts = sector_path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        parent = "/".join(parts[:i])
        _add(parent)

    # 3. Cross-sector inherits (from the sector itself)
    inherited = _read_inherits(KNOWLEDGE_ROOT / sector_path)
    for inherited_path in inherited:
        _expand_inherited(inherited_path, seen, chain)

    # 4. Root (always last, deduplicated)
    _add("")

    return chain


def _expand_inherited(sector_path: str, seen: set[str], chain: list[str]) -> None:
    """
    Recursively expand an inherited sector and its parents into the chain.
    """
    if sector_path in seen:
        return

    # Add the inherited sector itself
    seen.add(sector_path)
    chain.append(sector_path)

    # Add its parents
    parts = sector_path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        parent = "/".join(parts[:i])
        if parent not in seen:
            seen.add(parent)
            chain.append(parent)

    # Recurse into the inherited sector's own inherits
    nested_inherits = _read_inherits(KNOWLEDGE_ROOT / sector_path)
    for nested_path in nested_inherits:
        _expand_inherited(nested_path, seen, chain)


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class Agent:
    """
    A sector-aware, prompt-driven agent with inheritance support.

    Parameters
    ----------
    role : str
        The agent role: 'accountant', 'executive', 'trader', etc.
    sector_path : str, optional
        Sector path relative to KNOWLEDGE_ROOT, e.g.
        'commodities/precious_metals/gold'. If None, loads base-only.
    """

    def __init__(
        self,
        role: str,
        sector_path: str | None = None,
    ):
        self.role = role
        self.sector_path = sector_path

        # Build the resolution chain
        self._chain = resolve_sector_chain(sector_path)

        # --- Resolve prompt (override — most specific wins) ---
        self._prompt = ""
        for sp in self._chain:
            if sp == "":
                agent_dir = KNOWLEDGE_ROOT / "agents" / role
            else:
                agent_dir = KNOWLEDGE_ROOT / sp / "agents" / role
            prompt = _load_prompt(agent_dir / "prompt.md")
            if prompt:
                self._prompt = prompt
                break  # Most-specific wins

        # --- Resolve knowledge (merge — accumulate all) ---
        # Walk from LEAST specific to MOST specific so newer learnings appear last
        self._knowledge_parts: list[tuple[str, str]] = []
        for sp in reversed(self._chain):
            if sp == "":
                agent_dir = KNOWLEDGE_ROOT / "agents" / role
                label = "Cross-Sector"
            else:
                agent_dir = KNOWLEDGE_ROOT / sp / "agents" / role
                label = sp.split("/")[-1].replace("_", " ").title()

            knowledge = _load_knowledge_file(agent_dir / "knowledge.md")
            if knowledge:
                self._knowledge_parts.append((f"{label} Learnings", knowledge))

            # Also load supplementary files (e.g., strategy_patterns.md)
            for name, content in _load_supplementary_files(agent_dir):
                self._knowledge_parts.append(
                    (f"{label} — {name.replace('_', ' ').title()}", content)
                )

        # --- Resolve sector context (merge — accumulate all) ---
        # Walk from LEAST specific to MOST specific
        self._sector_context_parts: list[tuple[str, str]] = []
        for sp in reversed(self._chain):
            if sp == "":
                ctx = _load_sector_md(KNOWLEDGE_ROOT)
                label = "Universal"
            else:
                ctx = _load_sector_md(KNOWLEDGE_ROOT / sp)
                label = sp.split("/")[-1].replace("_", " ").title()
            if ctx:
                self._sector_context_parts.append((label, ctx))

        # Build chain string for logging
        chain_display = [
            f"agents/{role}" if sp == "" else f"{sp}/agents/{role}"
            for sp in self._chain
        ]
        self._chain_str = " → ".join(chain_display)

        logger.info("  Agent initialized: %s", self)
        logger.info("    Resolution chain: %s", self._chain_str)

    # ── context building ──────────────────────────────────────────────

    @property
    def system_prompt(self) -> str:
        """Full system prompt: prompt + sector context + knowledge."""
        sections = []

        # 1. Agent prompt (most-specific override)
        if self._prompt:
            sections.append(self._prompt)

        # 2. Sector context (merged from all levels)
        if self._sector_context_parts:
            ctx_parts = [
                f"### {label}\n\n{content}"
                for label, content in self._sector_context_parts
            ]
            sections.append(
                "# Current Sector Intelligence\n\n" + "\n\n---\n\n".join(ctx_parts)
            )

        # 3. Knowledge (merged from all levels)
        if self._knowledge_parts:
            kparts = [
                f"### {label}\n\n{content}"
                for label, content in self._knowledge_parts
            ]
            sections.append(
                "# Accumulated Knowledge\n\n" + "\n\n---\n\n".join(kparts)
            )

        return "\n\n===\n\n".join(sections)

    # ── LLM interaction ───────────────────────────────────────────────

    def analyze(self, data: str, user_request: str,
                llm: LLMClient, label: str = "analysis") -> str:
        """
        Run the agent: build system prompt, call the LLM.
        """
        user_msg_parts = []
        if data:
            user_msg_parts.append(f"## Data\n\n{data}")
        user_msg_parts.append(f"## Request\n\n{user_request}")
        user_message = "\n\n".join(user_msg_parts)

        return llm.call(self.system_prompt, user_message,
                        label=label, agent_name=self._chain_str)

    def __repr__(self) -> str:
        return f"Agent({self._chain_str})"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_sector_agents(sector_path: str | None = None) -> list[str]:
    """
    List available agent roles for a given sector.

    If sector_path is None, lists all base roles.

    Returns a list of role names, e.g. ['accountant', 'executive', 'trader'].
    """
    if sector_path:
        agents_dir = (KNOWLEDGE_ROOT / sector_path / "agents").resolve()
    else:
        agents_dir = (KNOWLEDGE_ROOT / "agents").resolve()

    if not agents_dir.is_dir():
        return []

    roles = []
    for d in sorted(agents_dir.iterdir()):
        if d.is_dir() and (d / "prompt.md").exists():
            roles.append(d.name)
    return roles


def discover_all_sectors() -> dict[str, list[str]]:
    """
    Walk the entire knowledge tree and return a mapping of
    sector paths to their available agent roles.

    Returns::

        {
            'commodities/precious_metals/gold': ['accountant', 'executive', 'trader', 'auditor'],
            'equities/tech': ['executive', 'trader'],
            'equities/mining': ['accountant'],
            ...
        }
    """
    results: dict[str, list[str]] = {}
    root = KNOWLEDGE_ROOT.resolve()

    for agents_dir in root.rglob("agents"):
        if not agents_dir.is_dir():
            continue
        try:
            rel = agents_dir.relative_to(root)
        except ValueError:
            continue

        # Skip the root-level agents/ (these are base roles, not sector agents)
        rel_str = str(rel)
        if rel_str == "agents":
            continue

        # Sector path is everything before /agents
        sector_path = str(rel.parent)
        roles = []
        for d in sorted(agents_dir.iterdir()):
            if d.is_dir() and (d / "prompt.md").exists():
                roles.append(d.name)
        if roles:
            results[sector_path] = roles

    return results


def load_asset_sectors(asset_path: str) -> list[str]:
    """
    Read the ``sectors:`` frontmatter from an asset file.

    Returns a list of sector paths, e.g.::

        ['commodities/precious_metals/gold', 'commodities/base_metals/copper']
    """
    full_path = KNOWLEDGE_ROOT / asset_path
    if not full_path.exists():
        return []
    fm, _ = read_frontmatter(full_path)
    return fm.get("sectors", [])
