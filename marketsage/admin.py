"""
Admin CLI — LLM-powered review and application of pending prompt/knowledge updates.

The admin uses an LLM to intelligently merge proposed changes into existing
prompt files, rather than mechanically appending text.

Usage::

    python -m marketsage.admin list                 # list pending proposals
    python -m marketsage.admin show <filename>      # preview a proposal
    python -m marketsage.admin apply <filename>     # LLM-merge one proposal
    python -m marketsage.admin apply-all            # LLM-merge all proposals
    python -m marketsage.admin preview <filename>   # dry-run: show LLM result without writing
    python -m marketsage.admin reject <filename>    # delete a proposal
    python -m marketsage.admin reject-all           # delete all proposals
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from marketsage.versioning import (
    read_frontmatter,
    stamp_and_commit,
    write_with_frontmatter,
)

logger = logging.getLogger("marketsage.admin")

_PENDING_DIR = Path(__file__).parent.parent / "pending_updates"
_PROJECT_ROOT = Path(__file__).parent.parent

# ───────────────────────────────────────────────────────────────────────
# LLM-powered merge
# ───────────────────────────────────────────────────────────────────────

_MERGE_SYSTEM_PROMPT = """\
You are a **Prompt Engineer** for the MarketSage investment analysis system.

Your job is to take an existing agent prompt and a proposed change, then
produce the UPDATED prompt that cleanly incorporates the proposal.

## Rules

1. **ADD**: Insert the proposed content into the most logical location
   in the prompt. Maintain consistent formatting, heading levels, and style.

2. **MODIFY**: Find the section referenced in the proposal and rewrite it
   according to the suggestion. Keep all other sections untouched.

3. **REMOVE**: Delete the referenced section or content. Ensure the
   surrounding text still flows properly.

4. **Quality control**: If the proposal would make the prompt worse
   (redundant, contradictory, or lower quality), reject it by returning
   the original prompt unchanged, prefixed with the line:
   `REJECTED: <brief reason>`

5. **Output ONLY the final prompt content** — no frontmatter, no
   explanations, no markdown code fences. Just the clean prompt body
   that will replace the current content.

6. **Preserve all existing content** that is NOT affected by the change.
   Do not rewrite sections that don't need changes.

7. **Maintain the voice and style** of the existing prompt.
"""


def _get_llm_client():
    """Lazy-load the LLM client to avoid import overhead for simple commands."""
    from marketsage.llm_client import LLMClient
    return LLMClient()


def _llm_merge(current_prompt: str, proposals: list[dict],
               target_path: str) -> tuple[str, bool]:
    """
    Use the LLM to intelligently merge one or more proposals into the current prompt.

    Parameters
    ----------
    current_prompt : str
        The current prompt body text.
    proposals : list[dict]
        Each dict has keys: 'change_type', 'body', 'proposed_by'.
    target_path : str
        Path to the target file (for context).

    Returns (merged_content, was_rejected).
    If the LLM decides ALL proposals are bad, was_rejected is True.
    """
    llm = _get_llm_client()

    # Format proposals
    proposals_text = ""
    for i, p in enumerate(proposals, 1):
        proposals_text += (
            f"### Proposal {i} (Type: {p['change_type']}, by: {p['proposed_by']})\n\n"
            f"{p['body']}\n\n"
        )

    user_message = (
        f"## Current Prompt\n"
        f"File: {target_path}\n\n"
        f"```\n{current_prompt}\n```\n\n"
        f"## Proposed Changes ({len(proposals)} proposal(s))\n\n"
        f"{proposals_text}"
        f"## Task\n\n"
        f"Produce the updated prompt that incorporates ALL valid proposals. "
        f"If a specific proposal would degrade the prompt, skip it and note "
        f"which you skipped. Return ONLY the final prompt body."
    )

    result = llm.simple_call(
        system=_MERGE_SYSTEM_PROMPT,
        user=user_message,
        label="prompt-merge",
        agent_name="admin",
    )

    result = result.strip()

    # Check if LLM rejected all proposals
    if result.startswith("REJECTED:"):
        return result, True

    return result, False


# ───────────────────────────────────────────────────────────────────────
# Core operations
# ───────────────────────────────────────────────────────────────────────

def list_pending() -> list[Path]:
    """List all pending update files."""
    if not _PENDING_DIR.exists():
        return []
    return sorted(_PENDING_DIR.glob("*.md"))


def show_pending(filename: str) -> str:
    """Show the content of a pending update."""
    path = _PENDING_DIR / filename
    if not path.exists():
        return f"Not found: {path}"
    return path.read_text(encoding="utf-8")


def preview_pending(filename: str) -> str:
    """
    Dry-run: show what the LLM would produce without writing anything.
    """
    path = _PENDING_DIR / filename
    if not path.exists():
        return f"Not found: {path}"

    fm, body = read_frontmatter(path)
    target_path = fm.get("target_file", "")
    change_type = fm.get("change_type", "MODIFY")
    proposed_by = fm.get("proposed_by", "unknown")

    if not target_path:
        return f"(Error: no target_file in frontmatter of {filename})"

    target = _PROJECT_ROOT / target_path
    if not target.exists():
        return f"(Error: target file does not exist: {target_path})"

    tfm, current_body = read_frontmatter(target)

    print(f"  🤖 Calling LLM to preview merge...")
    proposal = {"change_type": change_type, "body": body, "proposed_by": proposed_by}
    merged, rejected = _llm_merge(current_body, [proposal], target_path)

    if rejected:
        return (
            f"❌ LLM REJECTED this proposal:\n"
            f"  {merged}\n\n"
            f"  Use 'reject {filename}' to remove it."
        )

    return (
        f"✅ LLM Preview — proposed merge for {target_path}:\n"
        f"{'─' * 60}\n"
        f"{merged}\n"
        f"{'─' * 60}\n\n"
        f"To apply: python -m marketsage.admin apply {filename}"
    )


def apply_pending(filename: str) -> str:
    """Apply a single pending update using LLM-powered merge."""
    path = _PENDING_DIR / filename
    if not path.exists():
        return f"Not found: {path}"

    fm, body = read_frontmatter(path)
    target_path = fm.get("target_file", "")
    change_type = fm.get("change_type", "MODIFY")
    proposed_by = fm.get("proposed_by", "unknown")

    if not target_path:
        return f"(Error: no target_file in frontmatter of {filename})"

    target = _PROJECT_ROOT / target_path
    if not target.exists():
        # For new files, just write the proposal content directly
        target.parent.mkdir(parents=True, exist_ok=True)
        tfm = {"type": "prompt", "revision": 1,
               "last_modified": "", "summary": f"Created from proposal by {proposed_by}"}
        write_with_frontmatter(target, tfm, body)
        stamp_and_commit(target, f"Admin applied (new file): {proposed_by}")
        path.unlink()
        return f"✓ Created new file: {target_path} (from {filename})"

    # Load current content
    tfm, current_body = read_frontmatter(target)

    # LLM merge
    proposal = {"change_type": change_type, "body": body, "proposed_by": proposed_by}
    merged, rejected = _llm_merge(current_body, [proposal], target_path)

    if rejected:
        path.unlink()
        return (
            f"❌ LLM rejected proposal {filename}:\n"
            f"  {merged}\n"
            f"  Proposal deleted."
        )

    # Write merged content
    write_with_frontmatter(target, tfm, merged)
    stamp_and_commit(target, f"Admin approved ({change_type}): {proposed_by}")

    # Delete the proposal
    path.unlink()
    return f"✓ Applied {change_type} to {target_path} (proposed by {proposed_by})"


def reject_pending(filename: str) -> str:
    """Reject and delete a pending update."""
    path = _PENDING_DIR / filename
    if not path.exists():
        return f"Not found: {path}"
    path.unlink()
    return f"Rejected: {filename}"


def apply_all() -> list[str]:
    """
    Apply all pending prompt updates using LLM-powered merge.

    Batches proposals by target file so multiple proposals for the same
    prompt.md are merged in a single LLM call.
    """
    pending = list_pending()
    if not pending:
        return ["No pending updates."]

    # Separate prompt proposals from tool proposals
    prompt_proposals = []
    tool_proposals = []
    for p in pending:
        if "tool_proposal" in p.name:
            tool_proposals.append(p)
        else:
            prompt_proposals.append(p)

    results = []

    if tool_proposals:
        results.append(
            f"⏭ Skipping {len(tool_proposals)} tool proposal(s) "
            f"(these require manual implementation)."
        )

    if not prompt_proposals:
        return results or ["No prompt proposals to apply."]

    # Group proposals by target file
    from collections import defaultdict
    grouped: dict[str, list[tuple[Path, dict, str]]] = defaultdict(list)
    for p in prompt_proposals:
        fm, body = read_frontmatter(p)
        target_path = fm.get("target_file", "")
        if target_path:
            grouped[target_path].append((p, fm, body))
        else:
            p.unlink()
            results.append(f"⚠ Deleted {p.name} (no target_file in frontmatter)")

    logger.info("📝 Prompt Evolution: %d proposal(s) for %d file(s)",
                len(prompt_proposals), len(grouped))

    # Process each target file with all its proposals in one LLM call
    for target_path, proposals_list in grouped.items():
        target = _PROJECT_ROOT / target_path

        if not target.exists():
            for p, fm, body in proposals_list:
                p.unlink()
            results.append(f"⚠ Target missing: {target_path} ({len(proposals_list)} proposals deleted)")
            continue

        # Load current content
        tfm, current_body = read_frontmatter(target)

        # Build batch of proposals
        batch = [
            {
                "change_type": fm.get("change_type", "MODIFY"),
                "body": body,
                "proposed_by": fm.get("proposed_by", "unknown"),
            }
            for _, fm, body in proposals_list
        ]

        # Single LLM call for all proposals targeting this file
        try:
            merged, rejected = _llm_merge(current_body, batch, target_path)
        except Exception as exc:
            results.append(f"⚠ LLM error for {target_path}: {exc}")
            continue

        if rejected:
            for p, _, _ in proposals_list:
                p.unlink()
            results.append(
                f"❌ LLM rejected all {len(batch)} proposal(s) for {target_path}: "
                f"{merged.split(chr(10))[0]}"
            )
            continue

        # Write merged content
        write_with_frontmatter(target, tfm, merged)
        proposers = set(b["proposed_by"] for b in batch)
        stamp_and_commit(
            target,
            f"Admin applied {len(batch)} proposal(s): {', '.join(proposers)}"
        )

        # Delete all processed proposals
        for p, _, _ in proposals_list:
            p.unlink()

        types = [b["change_type"] for b in batch]
        results.append(
            f"✓ Applied {len(batch)} proposal(s) to {target_path} "
            f"[{', '.join(types)}]"
        )

    return results


def reject_all() -> list[str]:
    """Reject and delete all pending updates."""
    pending = list_pending()
    if not pending:
        return ["No pending updates."]
    results = []
    for p in pending:
        p.unlink()
        results.append(f"  Rejected: {p.name}")
    return results


# ───────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────

HELP_TEXT = """\
MarketSage Admin — LLM-powered prompt evolution manager.

Usage:
  python -m marketsage.admin <command> [arguments]

Commands:
  list                 List all pending proposals
  show <filename>      Show the raw content of a proposal
  preview <filename>   Dry-run: show what the LLM merge would produce
  apply <filename>     Apply a single proposal using LLM merge
  apply-all            Apply ALL prompt proposals (skips tool proposals)
  reject <filename>    Reject and delete a single proposal
  reject-all           Reject and delete ALL proposals
  help, --help, -h     Show this help message

How It Works:
  Agents propose prompt changes via the propose_prompt_change tool.
  Proposals are saved to pending_updates/ for review.

  When you run 'apply', the admin sends the current prompt + proposal
  to the LLM, which intelligently merges them (ADD, MODIFY, or REMOVE).
  If the LLM determines the proposal would degrade the prompt, it
  auto-rejects it.

  Tool proposals (create new scrapers) are skipped by apply-all since
  they require manual Python implementation.
"""


def main() -> None:
    # Set up basic logging for LLM calls
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        print(HELP_TEXT)
        return

    cmd = sys.argv[1]
    if cmd == "list":
        pending = list_pending()
        if not pending:
            print("No pending updates.")
            return

        # Categorize
        prompt_proposals = []
        tool_proposals = []
        for p in pending:
            if "tool_proposal" in p.name:
                tool_proposals.append(p)
            else:
                prompt_proposals.append(p)

        if prompt_proposals:
            print(f"\n📝 Prompt Proposals ({len(prompt_proposals)}):")
            for p in prompt_proposals:
                fm, _ = read_frontmatter(p)
                target = fm.get("target_file", "?")
                change = fm.get("change_type", "?")
                by = fm.get("proposed_by", "?")
                print(f"  [{change:6s}] {p.name}")
                print(f"           → {target} (by {by})")

        if tool_proposals:
            print(f"\n🔧 Tool Proposals ({len(tool_proposals)}):")
            for p in tool_proposals:
                print(f"  {p.name}")

        print(f"\nTotal: {len(pending)} pending")

    elif cmd == "show" and len(sys.argv) > 2:
        print(show_pending(sys.argv[2]))

    elif cmd == "preview" and len(sys.argv) > 2:
        print(preview_pending(sys.argv[2]))

    elif cmd == "apply" and len(sys.argv) > 2:
        print(apply_pending(sys.argv[2]))

    elif cmd == "apply-all":
        results = apply_all()
        for r in results:
            print(f"  {r}")
        print(f"\nDone — {len(results)} update(s) processed.")

    elif cmd == "reject" and len(sys.argv) > 2:
        print(reject_pending(sys.argv[2]))

    elif cmd == "reject-all":
        results = reject_all()
        for r in results:
            print(r)
        print(f"\nDone — {len(results)} proposal(s) rejected.")

    else:
        print("Unknown command. Run with --help for usage.")


if __name__ == "__main__":
    main()
