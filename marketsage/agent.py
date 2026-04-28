"""
Generic Agent engine — every agent is an *instance* configured by files.

An agent directory (e.g. ``agents/executive/gold/``) contains:
- ``prompt.md``    — the agent's system-prompt fragment
- ``knowledge/``   — directory of curated ``.md`` knowledge files

**Inheritance**: a child directory (``executive/gold/``) inherits from its
parent (``executive/``).  The resolved prompt is parent-first concatenation;
the resolved knowledge is parent-first concatenation of every ``.md`` file
in each ``knowledge/`` directory.

Usage::

    agent = Agent("agents/executive/gold", vault_chain=[...])
    response = agent.analyze(data_text, user_request, llm_client)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from marketsage.versioning import read_frontmatter

logger = logging.getLogger("marketsage.agent")

if TYPE_CHECKING:
    from marketsage.llm_client import LLMClient

# The ``agents/`` root lives inside the marketsage package.
AGENTS_ROOT = Path(__file__).parent / "agents"


# ---------------------------------------------------------------------------
# Inheritance resolution
# ---------------------------------------------------------------------------

def resolve_chain(agent_dir: Path) -> list[Path]:
    """
    Walk *agent_dir* upward until we reach ``AGENTS_ROOT`` and return
    the chain from **root-first** (ancestor → child).

    Example:
        resolve_chain(".../agents/executive/gold")
        → [".../agents/executive", ".../agents/executive/gold"]
    """
    chain: list[Path] = []
    cur = agent_dir.resolve()
    root = AGENTS_ROOT.resolve()
    while cur != root and cur != cur.parent:
        chain.append(cur)
        cur = cur.parent
    chain.reverse()           # root-first
    return chain


def load_prompt_chain(chain: list[Path]) -> str:
    """Concatenate ``prompt.md`` files along the inheritance chain."""
    parts: list[str] = []
    for d in chain:
        p = d / "prompt.md"
        if p.exists():
            _, body = read_frontmatter(p)
            parts.append(body.strip())
    return "\n\n---\n\n".join(parts)


def load_knowledge_chain(chain: list[Path]) -> str:
    """Concatenate every ``.md`` file in each ``knowledge/`` dir."""
    parts: list[str] = []
    for d in chain:
        kdir = d / "knowledge"
        if kdir.is_dir():
            for md in sorted(kdir.glob("*.md")):
                _, body = read_frontmatter(md)
                content = body.strip()
                if content:
                    parts.append(f"## [{md.stem}]\n\n{content}")
    return "\n\n---\n\n".join(parts)


def load_vault_chain(vault_files: list[Path]) -> str:
    """Concatenate vault ``.md`` files (commodities → sector → asset)."""
    parts: list[str] = []
    for vf in vault_files:
        if vf.exists():
            _, body = read_frontmatter(vf)
            content = body.strip()
            if content:
                parts.append(content)
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class Agent:
    """
    A prompt-driven, LLM-backed agent.

    Parameters
    ----------
    agent_path : str | Path
        Relative (to ``AGENTS_ROOT``) or absolute path to the agent directory.
    vault_chain : list[Path], optional
        Ordered list of vault ``.md`` files to include as context.
    """

    def __init__(
        self,
        agent_path: str | Path,
        vault_chain: list[Path] | None = None,
    ):
        d = Path(agent_path)
        self.agent_dir: Path = d if d.is_absolute() else AGENTS_ROOT / d
        if not self.agent_dir.is_dir():
            raise FileNotFoundError(f"Agent directory not found: {self.agent_dir}")

        self.name: str = self.agent_dir.name
        self._chain = resolve_chain(self.agent_dir)
        self._vault_chain = vault_chain or []

        logger.info("  Agent initialized: %s", self)
        logger.info("    Inheritance chain: %s",
                    " → ".join(d.name for d in self._chain))
        logger.info("    Vault files: %d", len(self._vault_chain))
        for vf in self._vault_chain:
            logger.info("      - %s", vf.name)

    # ── context building ──────────────────────────────────────────────

    @property
    def system_prompt(self) -> str:
        """Full system prompt: inherited prompts + knowledge + vault."""
        prompt = load_prompt_chain(self._chain)
        knowledge = load_knowledge_chain(self._chain)
        vault = load_vault_chain(self._vault_chain)

        sections = [prompt]
        if knowledge:
            sections.append(
                "# Your Curated Knowledge\n\n" + knowledge
            )
        if vault:
            sections.append(
                "# Current Intelligence (Vault)\n\n" + vault
            )
        return "\n\n===\n\n".join(sections)

    # ── LLM interaction ───────────────────────────────────────────────

    def analyze(self, data: str, user_request: str,
                llm: LLMClient, label: str = "analysis") -> str:
        """
        Run the agent: build system prompt, call the LLM.

        Parameters
        ----------
        data : str
            Fetched data (spiels, articles, etc.) as text.
        user_request : str
            The original user query.
        llm : LLMClient
            Configured LLM client instance.
        label : str
            Label for this call in logs.

        Returns
        -------
        str
            The LLM's response.
        """
        user_msg_parts = []
        if data:
            user_msg_parts.append(f"## Data\n\n{data}")
        user_msg_parts.append(f"## Request\n\n{user_request}")
        user_message = "\n\n".join(user_msg_parts)

        chain_str = "/".join(d.name for d in self._chain)
        return llm.call(self.system_prompt, user_message,
                        label=label, agent_name=chain_str)

    def __repr__(self) -> str:
        chain_str = " → ".join(d.name for d in self._chain)
        return f"Agent({chain_str})"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_agents(root: Path | None = None) -> dict[str, Path]:
    """
    Scan the agents directory tree and return a mapping of
    agent path keys (e.g. ``"executive/gold"``) to absolute dirs.
    Only directories containing a ``prompt.md`` are considered agents.
    """
    root = (root or AGENTS_ROOT).resolve()
    agents: dict[str, Path] = {}
    for prompt_file in root.rglob("prompt.md"):
        agent_dir = prompt_file.parent
        key = str(agent_dir.relative_to(root))
        agents[key] = agent_dir
    return agents
