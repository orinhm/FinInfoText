"""
Admin CLI — review and apply pending knowledge/prompt updates.

Usage:
    python -m marketsage.admin list
    python -m marketsage.admin show <filename>
    python -m marketsage.admin apply <filename>
    python -m marketsage.admin reject <filename>
"""

from __future__ import annotations

import sys
from pathlib import Path

from marketsage.versioning import (
    read_frontmatter,
    stamp_and_commit,
    write_with_frontmatter,
)

_PENDING_DIR = Path(__file__).parent.parent / "pending_updates"


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


def apply_pending(filename: str) -> str:
    """Apply a pending update to its target file."""
    path = _PENDING_DIR / filename
    if not path.exists():
        return f"Not found: {path}"

    fm, body = read_frontmatter(path)
    target = Path(fm.get("target_file", ""))
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)

    # Append the proposed content to the target file
    if target.exists():
        tfm, tbody = read_frontmatter(target)
    else:
        tfm = {"type": "knowledge", "revision": 0,
               "last_modified": "", "summary": ""}
        tbody = ""

    tbody += "\n" + body
    write_with_frontmatter(target, tfm, tbody)
    stamp_and_commit(target, f"Admin approved: {fm.get('proposed_by', 'unknown')}")

    path.unlink()
    return f"Applied and committed: {target}"


def reject_pending(filename: str) -> str:
    """Reject and delete a pending update."""
    path = _PENDING_DIR / filename
    if not path.exists():
        return f"Not found: {path}"
    path.unlink()
    return f"Rejected: {filename}"


def apply_all() -> list[str]:
    """Apply all pending updates."""
    pending = list_pending()
    if not pending:
        return ["No pending updates."]
    results = []
    for p in pending:
        result = apply_pending(p.name)
        results.append(result)
    return results


HELP_TEXT = """\
MarketSage Admin — Review and apply pending knowledge/prompt updates.

Usage:
  python -m marketsage.admin <command> [arguments]

Commands:
  list                 List all pending updates
  show <filename>      Show the content of a pending update
  apply <filename>     Apply a single pending update to its target file
  apply-all            Apply ALL pending updates at once
  reject <filename>    Reject and delete a pending update
  help, --help, -h     Show this help message
"""


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        print(HELP_TEXT)
        return

    cmd = sys.argv[1]
    if cmd == "list":
        pending = list_pending()
        if not pending:
            print("No pending updates.")
        for p in pending:
            print(f"  {p.name}")
    elif cmd == "show" and len(sys.argv) > 2:
        print(show_pending(sys.argv[2]))
    elif cmd == "apply" and len(sys.argv) > 2:
        print(apply_pending(sys.argv[2]))
    elif cmd == "apply-all":
        results = apply_all()
        for r in results:
            print(f"  {r}")
        print(f"\nDone — {len(results)} update(s) processed.")
    elif cmd == "reject" and len(sys.argv) > 2:
        print(reject_pending(sys.argv[2]))
    else:
        print("Unknown command. Use: list, show, apply <file>, apply-all, reject <file>")


if __name__ == "__main__":
    main()
