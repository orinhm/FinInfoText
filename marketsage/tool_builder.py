"""
Tool Builder — Auto-generates Python scraper modules from tool proposals.

After each MarketSage run, the orchestrator calls ``build_all_pending()``
to scan ``pending_updates/`` for tool proposals and use the LLM to
generate working Python code.  Generated tools are saved to
``marketsage/generated_tools/`` and become available in subsequent runs.

Generated tool structure
------------------------
For each built tool, two files are created:

    generated_tools/
    ├── fetch_something.py    ← Python module with fetch(**kw) → list[dict]
    └── fetch_something.json  ← Tool declaration + metadata

The dynamic loader in ``tools.py`` imports these at startup via
``importlib`` and registers them into the tool system.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("marketsage.tool_builder")

_PENDING_DIR = Path(__file__).parent.parent / "pending_updates"
_GENERATED_DIR = Path(__file__).parent / "generated_tools"
_SCRAPERS_DIR = Path(__file__).parent / "scrapers"


# ---------------------------------------------------------------------------
# Reference scraper code (loaded lazily for the code-generation prompt)
# ---------------------------------------------------------------------------

_REFERENCE_CACHE: dict[str, str] = {}


def _load_reference(name: str) -> str:
    """Load an existing scraper file as a reference example."""
    if name not in _REFERENCE_CACHE:
        path = _SCRAPERS_DIR / f"{name}.py"
        if path.exists():
            _REFERENCE_CACHE[name] = path.read_text(encoding="utf-8")
        else:
            _REFERENCE_CACHE[name] = f"(reference file {name}.py not found)"
    return _REFERENCE_CACHE[name]


# ---------------------------------------------------------------------------
# Proposal parsing
# ---------------------------------------------------------------------------

def _parse_proposal(path: Path) -> dict[str, Any] | None:
    """
    Parse a tool proposal ``.md`` file.

    Returns a dict with keys: tool_name, description, data_source,
    parameters_needed, rationale, status, path.
    Returns None if the file is not a valid proposal or already built.
    """
    text = path.read_text(encoding="utf-8")

    # Extract frontmatter
    fm: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            for line in text[3:end].strip().split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    fm[key.strip()] = val.strip()

    status = fm.get("status", "pending")
    if status != "pending":
        return None

    # Extract sections from the body
    body = text[text.find("---", 3) + 3:].strip() if text.startswith("---") else text

    def _section(heading: str) -> str:
        pattern = rf"## {re.escape(heading)}\s*\n+(.*?)(?=\n## |\Z)"
        m = re.search(pattern, body, re.DOTALL)
        return m.group(1).strip() if m else ""

    return {
        "tool_name": fm.get("tool_name", ""),
        "description": _section("Description"),
        "data_source": fm.get("data_source", "") or _section("Data Source"),
        "parameters_needed": _section("Parameters Needed"),
        "rationale": _section("Rationale"),
        "status": status,
        "path": path,
    }


def scan_pending_proposals() -> list[dict[str, Any]]:
    """Find all pending tool proposals in ``pending_updates/``."""
    proposals: list[dict[str, Any]] = []
    if not _PENDING_DIR.is_dir():
        return proposals

    for md_file in sorted(_PENDING_DIR.glob("*_tool_proposal_*.md")):
        proposal = _parse_proposal(md_file)
        if proposal:
            proposals.append(proposal)

    return proposals


# ---------------------------------------------------------------------------
# Code generation prompt
# ---------------------------------------------------------------------------

_CODE_GEN_SYSTEM = """\
You are an expert Python developer building a data scraper module for
the MarketSage investment intelligence system.

## Strict Requirements

1. The module MUST expose a public function:
   ``fetch(**kwargs) -> list[dict]``
   This is the ONLY function that the system calls.

2. Use ``requests`` for HTTP calls.  If the target is an HTML page,
   use ``BeautifulSoup`` from ``bs4`` for parsing.

3. Include proper logging via:
   ``logger = logging.getLogger("marketsage.scrapers.<name>")``

4. Include rate limiting: ``time.sleep(1.0)`` between HTTP requests.

5. Include error handling with retries (3 attempts, exponential backoff).

6. Return a **list of dicts** with consistent keys.  Each dict should
   represent one data record (article, data point, filing, etc.) with
   at minimum: a "title" or "name" key, a "date" key, and either
   "body" / "value" / "url" for the content.

7. Accept ``days_back: int`` as a keyword argument to control the
   lookback window (default 60).

8. Only use these imports (all pre-installed):
   ``requests``, ``bs4`` (BeautifulSoup), ``json``, ``logging``,
   ``time``, ``datetime``, ``re``, ``os``, ``html``,
   ``urllib.parse``, ``pathlib``, ``typing``.
   Do NOT use any other third-party libraries.

9. Include a module docstring explaining what the scraper does.

10. Do NOT include ``if __name__`` blocks, unit tests, or example usage.

## Reference: Existing API Scraper (CEO.CA)

```python
{ref_api}
```

## Reference: Existing HTML Scraper (web_news)

```python
{ref_html}
```

## Output Format

Output ONLY the Python source code.  No markdown fences, no
explanations, no commentary — just raw Python starting with the
module docstring.
"""


def _build_code_gen_prompt(proposal: dict[str, Any]) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for code generation."""
    ref_api = _load_reference("ceo_ca")
    ref_html = _load_reference("web_news")

    system = _CODE_GEN_SYSTEM.format(ref_api=ref_api, ref_html=ref_html)

    user = textwrap.dedent(f"""\
        Build a scraper module for the following tool proposal.

        **Tool name**: {proposal['tool_name']}
        **Description**: {proposal['description']}
        **Data source**: {proposal['data_source']}
        **Parameters needed**: {proposal['parameters_needed'] or '(not specified — infer reasonable parameters)'}
        **Rationale**: {proposal['rationale']}

        Generate the complete Python module now.
    """)

    return system, user


# ---------------------------------------------------------------------------
# Declaration generation prompt
# ---------------------------------------------------------------------------

_DECL_GEN_SYSTEM = """\
You are generating a Gemini function-calling tool declaration (JSON)
for a Python scraper module.

Output a JSON object with these exact keys:
{
  "name": "<tool_name>",
  "description": "<when to use this tool, what data it provides>",
  "parameters": {
    "type": "object",
    "properties": {
      "<param_name>": {"type": "<string|integer|boolean>", "description": "..."},
      ...
    },
    "required": ["<required_params>"]
  }
}

Output ONLY valid JSON.  No markdown fences, no commentary.
"""


def _build_decl_gen_prompt(proposal: dict[str, Any],
                           generated_code: str) -> tuple[str, str]:
    """Build prompts for generating the tool declaration JSON."""
    user = textwrap.dedent(f"""\
        Generate a tool declaration for this scraper.

        **Tool name**: {proposal['tool_name']}
        **Description**: {proposal['description']}
        **Parameters needed**: {proposal['parameters_needed'] or '(infer from the code)'}

        Here is the generated Python code (look at the fetch() signature):

        ```python
        {generated_code[:3000]}
        ```
    """)
    return _DECL_GEN_SYSTEM, user


# ---------------------------------------------------------------------------
# Code validation
# ---------------------------------------------------------------------------

def _validate_code(code: str, tool_name: str) -> tuple[bool, str]:
    """
    Validate generated Python code.

    Returns (success, error_message).
    """
    # 1. Compile check
    try:
        compile(code, f"{tool_name}.py", "exec")
    except SyntaxError as exc:
        return False, f"SyntaxError at line {exc.lineno}: {exc.msg}"

    # 2. Check for required `fetch` function
    if "def fetch(" not in code:
        return False, "Missing required `fetch(**kwargs)` function"

    # 3. Check for dangerous patterns
    dangerous = ["os.system(", "subprocess.", "eval(", "exec(", "__import__(",
                 "shutil.rmtree", "os.remove(", "os.unlink("]
    for pattern in dangerous:
        if pattern in code:
            return False, f"Dangerous pattern detected: {pattern}"

    return True, ""


def _try_import(tool_name: str) -> tuple[bool, str]:
    """
    Try to import a generated tool module.

    Returns (success, error_message).
    """
    module_path = _GENERATED_DIR / f"{tool_name}.py"
    module_name = f"marketsage.generated_tools.{tool_name}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return False, f"Could not create module spec for {module_path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if not hasattr(mod, "fetch"):
            return False, "Module loaded but has no `fetch` function"
        if not callable(mod.fetch):
            return False, "`fetch` exists but is not callable"

        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _smoke_test(tool_name: str, timeout: int = 15) -> tuple[bool, str]:
    """
    Run a quick smoke test by calling ``fetch()`` with a timeout.

    This catches runtime errors that compile/import wouldn't find
    (e.g., missing URL construction, bad API endpoints, etc.).

    Returns (success, message).
    """
    import importlib.util
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    module_path = _GENERATED_DIR / f"{tool_name}.py"
    module_name = f"marketsage.generated_tools.{tool_name}_smoketest"

    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return False, "Could not create module spec"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Call fetch() with minimal parameters and a timeout
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(mod.fetch, days_back=7)
            try:
                result = future.result(timeout=timeout)
            except TimeoutError:
                return True, f"Timed out after {timeout}s (network call may be slow — OK)"
            except Exception as exc:
                return False, f"fetch() raised {type(exc).__name__}: {exc}"

        if not isinstance(result, list):
            return False, f"fetch() returned {type(result).__name__}, expected list"

        return True, f"fetch() returned {len(result)} records"

    except Exception as exc:
        return False, f"Smoke test setup failed: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Proposal status update
# ---------------------------------------------------------------------------

def _update_proposal_status(proposal: dict[str, Any], status: str,
                            notes: str = "") -> None:
    """Update the status in the original proposal .md file."""
    path: Path = proposal["path"]
    text = path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()

    # Replace status in frontmatter
    text = re.sub(r"^status:\s*\w+", f"status: {status}", text, flags=re.MULTILINE)

    # Append build notes
    if notes:
        text += f"\n\n## Build Result ({now})\n\n{notes}\n"

    path.write_text(text, encoding="utf-8")
    logger.info("  Updated proposal status: %s → %s", path.name, status)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def _extract_code(llm_response: str) -> str:
    """Extract Python code from LLM response, stripping markdown fences."""
    # Try to find fenced code block
    fence_pattern = r"```(?:python)?\s*\n(.*?)```"
    m = re.search(fence_pattern, llm_response, re.DOTALL)
    if m:
        return m.group(1).strip()

    # If no fences, assume the entire response is code
    code = llm_response.strip()

    # Strip leading/trailing non-code lines
    lines = code.split("\n")
    # Find first line that looks like Python (docstring, import, comment)
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith('"""') or stripped.startswith("'''") or
            stripped.startswith("import ") or stripped.startswith("from ") or
            stripped.startswith("#") or stripped.startswith("def ") or
            stripped == ""):
            start = i
            break

    return "\n".join(lines[start:]).strip()


def _extract_json(llm_response: str) -> dict | None:
    """Extract JSON from LLM response, stripping markdown fences."""
    # Try fenced block first
    fence_pattern = r"```(?:json)?\s*\n(.*?)```"
    m = re.search(fence_pattern, llm_response, re.DOTALL)
    text = m.group(1).strip() if m else llm_response.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                return None
    return None


def build_tool(
    proposal: dict[str, Any],
    llm_client: Any,
    run_dir: Path | None = None,
) -> dict[str, Any] | None:
    """
    Build a single tool from a proposal using LLM code generation.

    Parameters
    ----------
    proposal : dict
        Parsed proposal from ``_parse_proposal()``.
    llm_client : LLMClient
        The LLM client for code generation.
    run_dir : Path, optional
        If set, save generation artifacts here.

    Returns
    -------
    dict or None
        Build result with keys: tool_name, py_path, json_path, status.
        None if building failed.
    """
    tool_name = proposal["tool_name"]
    logger.info("")
    logger.info("┌─ Building tool: %s", tool_name)
    logger.info("│  Source: %s", proposal["data_source"])
    logger.info("│  Description: %s", proposal["description"][:100])

    _GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already built
    py_path = _GENERATED_DIR / f"{tool_name}.py"
    json_path = _GENERATED_DIR / f"{tool_name}.json"
    if py_path.exists() and json_path.exists():
        logger.info("│  Already built — skipping")
        _update_proposal_status(proposal, "built",
                                "Tool was already generated.")
        return {"tool_name": tool_name, "status": "already_exists"}

    # ── Step 1: Generate Python code ──────────────────────────────────
    logger.info("│")
    logger.info("│  Step 1: Generating Python code...")
    system_prompt, user_prompt = _build_code_gen_prompt(proposal)

    code_response = llm_client.simple_call(
        system=system_prompt,
        user=user_prompt,
        label="tool_builder_code",
        agent_name="tool_builder",
    )

    code = _extract_code(code_response)
    logger.info("│  Generated %d chars of code", len(code))

    # Save raw response to run dir
    if run_dir:
        raw_file = run_dir / f"tool_build_{tool_name}_raw.py"
        raw_file.write_text(code_response, encoding="utf-8")

    # ── Step 2: Validate code ─────────────────────────────────────────
    logger.info("│  Step 2: Validating code...")
    valid, error = _validate_code(code, tool_name)

    if not valid:
        logger.warning("│  ⚠ Validation failed: %s", error)
        logger.info("│  Retrying code generation...")

        # Retry with error feedback
        retry_prompt = (
            f"{user_prompt}\n\n"
            f"IMPORTANT: Your previous attempt had this error:\n"
            f"{error}\n\n"
            f"Fix the issue and generate the complete module again."
        )
        code_response = llm_client.simple_call(
            system=system_prompt,
            user=retry_prompt,
            label="tool_builder_code_retry",
            agent_name="tool_builder",
        )
        code = _extract_code(code_response)

        valid, error = _validate_code(code, tool_name)
        if not valid:
            logger.error("│  ✗ Validation failed after retry: %s", error)
            _update_proposal_status(
                proposal, "failed",
                f"Code validation failed: {error}"
            )
            return None

    logger.info("│  ✓ Code validation passed")

    # ── Step 3: Save Python module ────────────────────────────────────
    py_path.write_text(code, encoding="utf-8")
    logger.info("│  Saved: %s", py_path.name)

    # ── Step 4: Test import ───────────────────────────────────────────
    logger.info("│  Step 3: Testing import...")
    importable, import_error = _try_import(tool_name)

    if not importable:
        logger.warning("│  ⚠ Import failed: %s", import_error)
        logger.info("│  Retrying code generation with import error feedback...")

        retry_prompt = (
            f"{user_prompt}\n\n"
            f"IMPORTANT: Your previous code compiled but failed to import:\n"
            f"{import_error}\n\n"
            f"Fix the issue and generate the complete module again."
        )
        code_response = llm_client.simple_call(
            system=system_prompt,
            user=retry_prompt,
            label="tool_builder_code_import_retry",
            agent_name="tool_builder",
        )
        code = _extract_code(code_response)

        valid, error = _validate_code(code, tool_name)
        if not valid:
            logger.error("│  ✗ Validation failed on import retry: %s", error)
            _update_proposal_status(
                proposal, "broken",
                f"Code generation retry failed validation: {error}"
            )
            return None

        py_path.write_text(code, encoding="utf-8")

        # Clear cached module if it was partially loaded
        mod_name = f"marketsage.generated_tools.{tool_name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        importable, import_error = _try_import(tool_name)
        if not importable:
            logger.error("│  ✗ Import still failing: %s", import_error)
            _update_proposal_status(
                proposal, "broken",
                f"Import failed after retry: {import_error}"
            )
            # Keep the .py file for manual debugging but rename it
            broken_path = _GENERATED_DIR / f"{tool_name}.py.broken"
            py_path.rename(broken_path)
            return None

    logger.info("│  ✓ Import successful")

    # ── Step 5: Smoke test ───────────────────────────────────────────
    logger.info("│  Step 5: Smoke testing fetch()...")
    smoke_ok, smoke_msg = _smoke_test(tool_name)
    if smoke_ok:
        logger.info("│  ✓ Smoke test: %s", smoke_msg)
    else:
        logger.warning("│  ⚠ Smoke test warning: %s (tool still usable)", smoke_msg)

    # ── Step 6: Generate tool declaration ─────────────────────────────
    logger.info("│  Step 6: Generating tool declaration...")
    decl_system, decl_user = _build_decl_gen_prompt(proposal, code)
    decl_response = llm_client.simple_call(
        system=decl_system,
        user=decl_user,
        label="tool_builder_decl",
        agent_name="tool_builder",
    )

    declaration = _extract_json(decl_response)
    if not declaration:
        logger.warning("│  ⚠ Could not parse declaration JSON — using fallback")
        # Fallback: create a minimal declaration from the proposal
        declaration = {
            "name": tool_name,
            "description": proposal["description"],
            "parameters": {
                "type": "object",
                "properties": {
                    "days_back": {
                        "type": "integer",
                        "description": "How far back to look. Default: 60.",
                    },
                },
            },
        }

    # Ensure the name matches
    declaration["name"] = tool_name

    # ── Step 6: Save declaration + metadata ───────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "declaration": declaration,
        "metadata": {
            "generated_at": now,
            "generated_by": "tool_builder",
            "proposal_file": str(proposal["path"].name),
            "data_source": proposal["data_source"],
            "status": "active",
        },
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    logger.info("│  Saved: %s", json_path.name)

    # ── Step 7: Update proposal status ────────────────────────────────
    _update_proposal_status(
        proposal, "built",
        f"Tool generated successfully.\n"
        f"- Module: `generated_tools/{tool_name}.py`\n"
        f"- Declaration: `generated_tools/{tool_name}.json`\n"
        f"- Built at: {now}\n"
    )

    logger.info("│")
    logger.info("└─ ✓ Tool built: %s", tool_name)
    logger.info("")

    return {
        "tool_name": tool_name,
        "py_path": str(py_path),
        "json_path": str(json_path),
        "status": "built",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_all_pending(
    llm_client: Any,
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Scan ``pending_updates/`` for tool proposals and build them.

    Parameters
    ----------
    llm_client : LLMClient
        The LLM client for code generation.
    run_dir : Path, optional
        If set, save generation artifacts to the run directory.

    Returns
    -------
    list[dict]
        List of build results (one per proposal attempted).
    """
    proposals = scan_pending_proposals()
    if not proposals:
        logger.info("  No pending tool proposals found.")
        return []

    logger.info("")
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║  Tool Builder — %d pending proposal(s)" + " " * 28 + "║",
                len(proposals))
    logger.info("╚" + "═" * 68 + "╝")

    results: list[dict[str, Any]] = []
    for proposal in proposals:
        result = build_tool(proposal, llm_client, run_dir=run_dir)
        if result:
            results.append(result)

    built = [r for r in results if r.get("status") == "built"]
    logger.info("")
    logger.info("  Tool Builder complete: %d/%d proposals built successfully.",
                len(built), len(proposals))
    return results
