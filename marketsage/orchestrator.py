"""
MarketSage Orchestrator — Tool-Based Mode.

Instead of a rigid pipeline (plan → fetch → dispatch → synthesize → review),
the LLM autonomously decides which tools to call and produces the final
analysis in a single multi-turn chat session.

Flow:
  1. Build system prompt (identity + agent knowledge + vault description)
  2. Start chat with user request + tool declarations
  3. LLM calls tools as needed → Python executes → results fed back
  4. LLM produces final analysis
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from marketsage.agent import AGENTS_ROOT, discover_agents
from marketsage.llm_client import LLMClient

logger = logging.getLogger("marketsage.orchestrator")

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"
_VAULT_ROOT = Path(__file__).parent.parent / "vault"


def _load_settings() -> dict[str, Any]:
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_system_prompt() -> str:
    """
    Build the system prompt that gives the LLM its identity,
    available tools context, agent personas overview, and
    vault architecture understanding.
    """
    # Discover available agents
    agents = discover_agents()
    agent_list = "\n".join(f"  - `{k}`" for k in sorted(agents.keys()))

    # Read vault index for structure overview
    vault_index = ""
    index_file = _VAULT_ROOT / "_index.json"
    if index_file.exists():
        try:
            vault_index = index_file.read_text(encoding="utf-8")
        except Exception:
            vault_index = "(could not read vault index)"

    # Load agent prompt summaries for persona context
    agent_summaries: list[str] = []
    for agent_path in sorted(agents.keys()):
        prompt_file = agents[agent_path] / "prompt.md"
        if prompt_file.exists():
            # Read first ~500 chars to get the gist
            content = prompt_file.read_text(encoding="utf-8")
            # Strip frontmatter
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    content = content[end + 3:].strip()
            # Take first paragraph
            lines = content.split("\n\n")
            summary = lines[0].strip() if lines else ""
            if summary:
                agent_summaries.append(f"### {agent_path}\n{summary}")

    agent_personas = "\n\n".join(agent_summaries) if agent_summaries else "(no agents found)"

    system_prompt = f"""\
# MarketSage — Self-Evolving Investment Intelligence System

You are **MarketSage**, a sophisticated multi-agent investment analysis system.
You have access to several data-fetching tools (scrapers) and a knowledge vault
that accumulates intelligence over time.

## How You Work

1. **Understand** the user's request — what asset, sector, time period, and
   type of analysis they want.
2. **Gather data** by calling the appropriate scraper tools. You decide which
   tools to call based on the request. You can call multiple tools.
3. **Consult the vault** by reading existing knowledge files to see what has
   been accumulated from prior analyses.
4. **Analyze** the data from multiple specialist perspectives (trader,
   executive, auditor, accountant, librarian). Read agent knowledge files
   to adopt their analytical frameworks.
5. **Synthesize** all perspectives into a coherent, well-structured report.
6. **Persist learnings** — if you discover important new facts or patterns,
   use the persist_learning tool to save them for future analyses.

## Your Available Tools

You have tools for:
- **Data fetching**: CEO.CA forum posts, Yahoo Finance stock data, FRED
  macroeconomic data, NewFoundGold press releases, generic web news scraping
- **Knowledge vault**: Read and browse the hierarchical knowledge vault,
  persist new learnings
- **Agent knowledge**: Read specialist agent prompts and accumulated expertise
- **Agent creation**: Create new specialist sub-agents when a sector has
  no existing specialization (e.g., create `executive/tech` when analyzing
  tech companies for the first time)

## Agent Specialization

The agent system is hierarchical — e.g., `executive/gold` inherits from
`executive` but adds gold-sector expertise. If you encounter a sector with
no existing specialization, use `create_agent_specialization` to create one.
**Only do this when sector-specific frameworks would genuinely improve the
analysis** — don't create specializations for one-off queries.

## Agent Personas Available

When analyzing data, adopt the perspective of these specialist agents:

{agent_personas}

**How to use agents**: Call `read_agent_knowledge` for any agent whose
perspective is relevant to the analysis. Then apply their analytical framework
to the gathered data.

## Available Agents (by path)

{agent_list}

## Knowledge Vault Structure

The vault is a hierarchical directory of markdown files containing accumulated
intelligence from prior analyses. Structure:

```
vault/
├── _index.json          — Master index of sectors, assets, tickers
├── commodities/         — Commodity sector knowledge
│   └── precious_metals/
│       └── gold/
│           ├── gold_sector.md
│           └── assets/
│               └── nfgc.md
├── equities/            — Equity-specific knowledge
└── mining/              — Mining industry knowledge
```

Each vault `.md` file has these sections:
- **Executive Summary** — auto-generated from latest heuristics
- **Key Heuristics** — distilled facts and patterns (deduplicated)
- **Chronological Log** — timestamped record of observations
- **Contradictions & Resolutions** — tracked disagreements

**Vault Index:**
```json
{vault_index}
```

## Analysis Guidelines

1. **Never hallucinate data** — if a scraper returns no data, say so clearly.
   Do NOT fabricate numbers, quotes, or events.
2. **Be sector-agnostic** — work with any asset type (mining, tech, energy, etc.)
3. **Ticker aliasing guard** — verify that Yahoo Finance data matches the
   company the user asked about. E.g., ticker GOLD is Barrick Gold, not
   "gold the commodity".
4. **Multi-perspective analysis** — always consider at least 2-3 agent
   perspectives (e.g., trader + executive + accountant).
5. **Structured output** — use clear sections, bullet points, and confidence
   levels in your final analysis.
6. **Persist genuinely new knowledge** — if you learn something that would
   help future analyses, save it using the persist_learning tool.
7. **Data-missing protocol** — if critical data is unavailable, clearly
   flag it rather than working around it silently.

## Output Format

Your final analysis should include:
- **Executive Summary** — 2-3 sentence overview
- **Data Sources Used** — what tools you called and what data you got
- **Multi-Perspective Analysis** — organized by perspective
  (trader view, executive view, etc.)
- **Key Findings** — most important takeaways
- **Risk Factors** — things to watch out for
- **Overall Assessment** — with confidence level (LOW/MEDIUM/HIGH)
"""

    return system_prompt


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Tool-based MarketSage orchestrator."""

    def __init__(self, settings: dict[str, Any] | None = None,
                 run_dir: Path | None = None):
        self.settings = settings or _load_settings()
        self.llm = LLMClient(self.settings)
        self.run_dir = run_dir

    def run(self, user_request: str) -> str:
        """
        Execute a full analysis cycle using tool-based LLM interaction.

        Parameters
        ----------
        user_request : str
            Free-form user request, e.g.
            "Summarize NFGC over the last 2 months, use ceo.ca"

        Returns
        -------
        str
            Final analysis produced by the LLM.
        """
        import time as _time
        t_start = _time.time()

        logger.info("")
        logger.info("╔" + "═" * 68 + "╗")
        logger.info("║  MarketSage (Tool Mode) — %s" + " " * 25 + "║",
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("╚" + "═" * 68 + "╝")
        logger.info("")
        logger.info("USER REQUEST:")
        logger.info("%s", user_request)
        logger.info("")
        logger.info("CONFIGURATION:")
        logger.info("  LLM provider:      %s", self.llm.provider)
        logger.info("  LLM model:         %s", self.llm.model)
        logger.info("  Temperature:       %s", self.llm.temperature)
        logger.info("  Max tokens:        %s", self.llm.max_tokens)
        logger.info("  Run directory:     %s", self.run_dir or "(none)")
        logger.info("")

        # Build system prompt
        system_prompt = _build_system_prompt()
        logger.info("System prompt: %d chars", len(system_prompt))

        # Save system prompt to run dir
        if self.run_dir:
            prompt_file = self.run_dir / "system_prompt.md"
            prompt_file.write_text(system_prompt, encoding="utf-8")

        # Run the multi-turn chat with tools
        logger.info("")
        logger.info("═" * 70)
        logger.info("▸ Starting tool-based analysis...")
        logger.info("═" * 70)

        result = self.llm.chat_with_tools(
            system_prompt=system_prompt,
            user_message=user_request,
            run_dir=self.run_dir,
        )

        elapsed = _time.time() - t_start
        logger.info("")
        logger.info("╔" + "═" * 68 + "╗")
        logger.info("║  Run complete — %.1fs, %d LLM calls, %d tool calls" + " " * 15 + "║",
                     elapsed, self.llm._call_count, self.llm._tool_call_count)
        logger.info("╚" + "═" * 68 + "╝")
        logger.info("")

        return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run MarketSage (Tool Mode) from the command line."""
    import sys

    # Create per-run directory: runs/run_YYYYMMDD_HHMMSS/
    runs_root = Path(__file__).parent.parent / "runs"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = runs_root / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    log_file = run_dir / "run.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),                          # terminal
            logging.FileHandler(log_file, encoding="utf-8"),  # file
        ],
    )
    logger.info("Run directory: %s", run_dir)
    logger.info("Log file: %s", log_file)

    if len(sys.argv) > 1:
        request = " ".join(sys.argv[1:])
    else:
        request = "Summarize NFGC over the last 2 months"

    orchestrator = Orchestrator(run_dir=run_dir)
    result = orchestrator.run(request)

    # Save final analysis to run directory
    analysis_file = run_dir / "final_analysis.md"
    analysis_file.write_text(result, encoding="utf-8")
    logger.info("Final analysis saved to: %s", analysis_file)

    print("\n" + "=" * 60)
    print("  FINAL ANALYSIS")
    print("=" * 60)
    print(result)
    print(f"\n📁 Run directory: {run_dir}")


if __name__ == "__main__":
    main()
