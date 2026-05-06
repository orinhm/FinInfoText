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
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from marketsage.agent import KNOWLEDGE_ROOT, discover_all_sectors
from marketsage.llm_client import LLMClient

logger = logging.getLogger("marketsage.orchestrator")

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"


def _load_settings() -> dict[str, Any]:
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_tool_catalog() -> str:
    """
    Auto-generate the tool catalog from the live tool registry.

    Includes built-in tools, custom scrapers, and generated tools —
    so newly created tools appear automatically in the next run.
    """
    from marketsage.tools import get_all_tool_declarations

    tools = get_all_tool_declarations()
    lines = ["## Your Available Tools\n"]
    for t in tools:
        name = t["name"]
        desc = t.get("description", "")
        note = t.get("usage_note", "")
        params = t.get("parameters", {}).get("properties", {})
        if params:
            param_parts = []
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "")
                param_parts.append(f"{pname}: {ptype}" if ptype else pname)
            sig = ", ".join(param_parts)
        else:
            sig = ""
        entry = f"- **`{name}({sig})`** — {desc}"
        if note:
            entry += f" ⚠ {note}"
        lines.append(entry)
    return "\n".join(lines)


def _generate_output_format(llm: LLMClient, user_request: str) -> str:
    """
    Use a lightweight LLM call to generate an output format
    tailored to the user's specific request.

    Different requests need different structures — a comparison
    needs tables, a single-stock analysis needs multi-perspective
    sections, a macro question needs supply/demand frameworks, etc.
    """
    system = (
        "You are designing the output format for an investment analysis system. "
        "Given the user's request, generate the optimal report structure.\n\n"
        "Return ONLY a markdown section starting with '## Output Format' "
        "containing section headings with brief descriptions.\n\n"
        "Adapt the depth and number of sections to the request — a quick "
        "overview needs few sections, a detailed deep-dive needs many. "
        "Be specific to the request type — don't use a generic template.\n"
    )
    return llm.simple_call(
        system, user_request,
        label="output-format", agent_name="orchestrator",
    )


def _build_system_prompt(llm: LLMClient, user_request: str) -> str:
    """
    Build the system prompt that gives the LLM its identity,
    available tools context, and knowledge tree overview.

    Dynamic sections:
    - Tool catalog: auto-generated from the live tool registry
    - Output format: LLM-tailored per user request
    """
    # Discover available sectors and their agents
    all_sectors = discover_all_sectors()
    sector_list = "\n".join(
        f"  - `{sp}`: {', '.join(roles)}"
        for sp, roles in sorted(all_sectors.items())
    )

    # Load base role summaries from knowledge/agents/
    base_roles: list[str] = []
    base_dir = KNOWLEDGE_ROOT / "agents"
    if base_dir.is_dir():
        for role_dir in sorted(base_dir.iterdir()):
            if not role_dir.is_dir():
                continue
            prompt_file = role_dir / "prompt.md"
            if prompt_file.exists():
                content = prompt_file.read_text(encoding="utf-8")
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end > 0:
                        content = content[end + 3:].strip()
                lines = content.split("\n\n")
                summary = lines[0].strip() if lines else ""
                if summary:
                    base_roles.append(f"### {role_dir.name}\n{summary}")

    roles_text = "\n\n".join(base_roles) if base_roles else "(no base roles found)"

    # Dynamic sections
    tool_catalog = _build_tool_catalog()
    output_format = _generate_output_format(llm, user_request)

    system_prompt = f"""\
# MarketSage — Self-Evolving Investment Intelligence System

You are **MarketSage**, a sophisticated multi-agent investment analysis system.
You have access to data-fetching tools (scrapers) and a unified knowledge tree
that accumulates intelligence over time.

## Architecture: Sector-First Knowledge Tree with Inheritance

All knowledge — sector data, asset intelligence, AND agent specializations —
lives in a single `knowledge/` tree organized by sector. See ARCHITECTURE.md
for the full specification.

```
knowledge/
├── ARCHITECTURE.md          — System specification (read this for full details)
├── sector.md                — Universal market knowledge
├── agents/                  — Generic role frameworks (HOW to think)
│   ├── accountant/          — Financial analysis framework
│   ├── executive/           — Strategy & M&A framework
│   ├── trader/              — Sentiment & positioning framework
│   ├── auditor/             — Data verification framework
│   └── geopolitical/        — Geopolitical risk framework
│
├── commodities/
│   ├── sector.md            — Commodity macro intelligence
│   ├── precious_metals/gold/
│   │   ├── sector.md        — Gold sector intelligence
│   │   ├── agents/          — Gold-specialized agents
│   │   ├── juniors/assets/  — Junior gold miner data
│   │   └── majors/assets/   — Major gold miner data
│   └── base_metals/copper/  — Copper sector + agents
│
├── equities/
│   ├── sector.md            — Equities macro intelligence
│   ├── tech/                — Tech sector + agents + assets
│   ├── mining/              — Mining equities (inherits: precious_metals)
│   │   ├── sector.md        — Mining sector + inheritance declaration
│   │   └── assets/          — Mining company data (e.g., barrick.md)
│   └── pharma/              — Pharma sector + agents
```

### Cross-Sector Inheritance

Sectors can inherit from other sectors via `inherits:` in sector.md frontmatter.
For example, `equities/mining` inherits from `commodities/precious_metals`,
so a mining accountant gets BOTH equity analysis AND commodity domain knowledge.

## How You Work

1. **Load sector context**: Call `read_sector_context(sector)` FIRST to get
   ALL knowledge + available agents for that sector in one call.
2. **Load agents**: Call `load_agent(role, sector_path)` for each specialist
   perspective needed. This composes agents/role + sector specialization +
   inherited sector knowledge automatically.
3. **Fetch fresh data** from external sources (Yahoo, CEO.CA, FRED, web_news).
4. **Analyze** from multiple specialist perspectives.
5. **Synthesize** all perspectives into a coherent report.
6. **Persist learnings** to the correct location in the knowledge tree.

{tool_catalog}

## Multi-Sector Assets

Some assets belong to multiple sectors (e.g., Barrick mines gold AND copper).
These have `sectors:` in their frontmatter. When analyzing them, load context
from ALL listed sectors.

## Self-Expansion

### New Sectors
Use `create_sector` to create new domains (e.g., `equities/biotech`).
This creates the directory with `sector.md`, `agents/`, and `assets/`.
Supports cross-sector inheritance via the `inherits` parameter.

### Agent Specialization
If a sector has no existing agent for a needed role, use `create_sector_agent`
to create one. The agent is created under {{sector}}/agents/{{role}}/ and
inherits the base role framework. Only create when genuinely needed.

### Prompt Evolution
When you identify improvements to an agent's analytical framework, use
`propose_prompt_change` to submit the proposal for admin review.
**NEVER write directly to prompt.md files** — persist_learning will reject it.
Proposals are reviewed via `python -m marketsage.admin`.

### Custom Scrapers
Use `create_custom_scraper` to save useful news sources as named tools.

## Base Agent Roles

{roles_text}

## Sectors with Specialized Agents

{sector_list}

## Learning Persistence Paths

When persisting learnings, use the correct target path:
- **Asset**: `{{sector}}/assets/{{ticker}}.md`
- **Sector**: `{{sector}}/sector.md`
- **Agent (sector)**: `{{sector}}/agents/{{role}}/knowledge.md`
- **Agent (cross-sector)**: `agents/{{role}}/knowledge.md`
- **Universal**: `sector.md` (root)

## Analysis Guidelines

1. **Never hallucinate data** — if a scraper returns no data, say so clearly.
2. **Be sector-agnostic** — work with any asset type.
3. **Ticker aliasing guard** — verify Yahoo Finance data matches the intended asset.
4. **Multi-perspective analysis** — always use 2-3 agent perspectives.
5. **Structured output** — clear sections, bullet points, confidence levels.
6. **Data-missing protocol** — flag unavailable data clearly.

## Learning Persistence

After every analysis, persist genuinely new knowledge for future use.
Call `persist_learning` for each distinct insight — do NOT batch them into one call.

When the user explicitly asks to "learn" or "extract knowledge," be **aggressive**:
persist every useful pattern, benchmark, heuristic, or factual insight you find.
Categorize learnings by type and persist to the right path:

- **Industry patterns** → `{{sector}}/sector.md` (e.g., Lassonde Curve stages, permitting timelines)
- **Financial benchmarks** → `{{sector}}/agents/accountant/knowledge.md` (e.g., EV/oz norms, AISC ranges)
- **M&A patterns** → `{{sector}}/agents/executive/knowledge.md` (e.g., takeover premiums, deal structures)
- **Sentiment patterns** → `{{sector}}/agents/trader/knowledge.md` (e.g., board behavior at cycle bottoms)
- **Asset-specific facts** → `{{sector}}/assets/{{ticker}}.md` (e.g., key zones, management changes)
- **Cross-sector insights** → `agents/{{role}}/knowledge.md` or root `sector.md`

{output_format}
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
        if run_dir:
            os.environ["MARKETSAGE_RUN_DIR"] = str(run_dir)

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

        # Build system prompt (with dynamic tool catalog + LLM-tailored output format)
        system_prompt = _build_system_prompt(self.llm, user_request)
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
        logger.info("║  Analysis complete — %.1fs, %d LLM calls, %d tool calls" + " " * 12 + "║",
                     elapsed, self.llm._call_count, self.llm._tool_call_count)
        logger.info("╚" + "═" * 68 + "╝")
        logger.info("")

        # ── Save run summary JSON (early — before tool builder) ─────────
        total_elapsed = _time.time() - t_start
        summary: dict[str, Any] = {}
        if self.run_dir:
            summary = {
                "timestamp": datetime.now().isoformat(),
                "user_request": user_request[:500],
                "elapsed_seconds": round(total_elapsed, 1),
                "llm_calls": self.llm._call_count,
                "tool_calls": self.llm._tool_call_count,
                "tokens": {
                    "input": self.llm._total_input_tokens,
                    "output": self.llm._total_output_tokens,
                    "total": self.llm._total_tokens,
                },
                "model": f"{self.llm.provider}/{self.llm.model}",
                "tools_built": [],
                "response_length": len(result),
            }
            summary_file = self.run_dir / "run_summary.json"
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)

        # ── Post-run: build any proposed tools ────────────────────────
        tools_built = []
        try:
            from marketsage.tool_builder import build_all_pending
            built = build_all_pending(self.llm, run_dir=self.run_dir)
            if built:
                built_ok = [r for r in built if r.get("status") == "built"]
                tools_built = built_ok
                logger.info("")
                logger.info("🔧 Tool Builder: %d new tool(s) generated",
                            len(built_ok))
                for r in built_ok:
                    logger.info("   ✓ %s", r["tool_name"])
        except Exception as exc:
            logger.warning("Tool builder phase failed: %s", exc,
                           exc_info=True)

        # ── Post-run: apply pending prompt proposals ─────────────────
        prompts_applied = []
        try:
            from marketsage.admin import apply_all as admin_apply_all
            results = admin_apply_all()
            # Filter out the "no pending" and "skipping tool" messages
            prompts_applied = [
                r for r in results
                if r.startswith("✓") or r.startswith("❌")
            ]
            if prompts_applied:
                logger.info("")
                logger.info("📝 Prompt Evolution: %d proposal(s) processed",
                            len(prompts_applied))
                for r in prompts_applied:
                    logger.info("   %s", r.split("\n")[0])
        except Exception as exc:
            logger.warning("Prompt evolution phase failed: %s", exc,
                           exc_info=True)

        # ── Update run summary with tool builder + prompt results ──────
        if self.run_dir and summary:
            total_elapsed = _time.time() - t_start
            summary["elapsed_seconds"] = round(total_elapsed, 1)
            summary["tools_built"] = [
                r.get("tool_name", "") for r in tools_built
            ]
            summary["prompts_evolved"] = len(prompts_applied)
            summary["llm_calls"] = self.llm._call_count
            summary["tokens"] = {
                "input": self.llm._total_input_tokens,
                "output": self.llm._total_output_tokens,
                "total": self.llm._total_tokens,
            }
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info("📊 Run summary → %s", summary_file.name)
            logger.info("   Tokens: %d in / %d out / %d total",
                        self.llm._total_input_tokens,
                        self.llm._total_output_tokens,
                        self.llm._total_tokens)

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

        #request = "Analyze The Silver Market over the past six months, analyze the silver price movement, highlight leading stocks, do historical analysis of silver and how the price movement looks in historical perspective"
        request = """give me a detailed overview of NFGC public sentiment (look at CEO.CA, download the data and analyze it) and how it evolved over the past 6 years. 
                    I want a detailed summarry of every month including price movement, news releases and ceo.ca sentiment alongside with major concerns and issues raised in the message board
                    give a time line of major events, 
                    along side stock price along with public sentiment. and summary of the different sentiments at that time
                    """
        request = """give me a detailed overview of NFGC public sentiment (look at CEO.CA, download the data and analyze it) and how it evolved over the past 6 years. 
                I want a detailed summary of every month including price movement, news releases and ceo.ca sentiment alongside with major concerns and issues raised in the message board.
                give also for each month a summary of major disagreements amongst the message board participants
                            """
        request = """
                    look at gold mining stock message boards in ceo.ca (NFGC, Kinross, Alamos... whatever other stocks) over the last 2 years.
                    try to learn everything you can about the mining business, pitfalls processes in mining and finance and whatever category 
                    you can learn
                    """
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
