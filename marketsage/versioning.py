"""
Version-stamping helpers for MarketSage .md files.

Every managed .md file carries YAML frontmatter:

    ---
    type: knowledge          # prompt | knowledge | vault
    revision: 3
    last_modified: 2026-04-25T00:00:00+03:00
    summary: "Short description of content"
    ---
    # Actual content…

This module provides read/write helpers and a git-commit wrapper.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ── regex that matches a leading YAML frontmatter block ────────────────────
_FM_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Read / write frontmatter
# ---------------------------------------------------------------------------

def read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body) for a file. Empty dict if no FM."""
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group(1)) or {}
    body = text[m.end():]
    return fm, body


def write_with_frontmatter(path: Path, fm: dict[str, Any], body: str) -> None:
    """Write frontmatter + body to *path*, creating parents if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).rstrip("\n")
    path.write_text(f"---\n{fm_str}\n---\n{body}", encoding="utf-8")


def ensure_frontmatter(path: Path, file_type: str = "knowledge",
                       summary: str = "") -> dict[str, Any]:
    """
    If *path* has no frontmatter, add a default block and return it.
    If it already has frontmatter, return it unchanged.
    """
    fm, body = read_frontmatter(path)
    if fm:
        return fm
    fm = {
        "type": file_type,
        "revision": 0,
        "last_modified": datetime.now(timezone.utc).isoformat(),
        "summary": summary or path.stem.replace("_", " ").title(),
    }
    write_with_frontmatter(path, fm, body)
    return fm


# ---------------------------------------------------------------------------
# Revision bump
# ---------------------------------------------------------------------------

def bump_revision(path: Path, summary: str | None = None) -> dict[str, Any]:
    """
    Increment *revision*, update *last_modified*, optionally update *summary*.
    Returns the updated frontmatter dict.
    """
    fm, body = read_frontmatter(path)
    fm["revision"] = fm.get("revision", 0) + 1
    fm["last_modified"] = datetime.now(timezone.utc).isoformat()
    if summary:
        fm["summary"] = summary
    write_with_frontmatter(path, fm, body)
    return fm


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_commit_file(path: Path, message: str | None = None) -> bool:
    """
    Stage *path* and commit with *message*.
    Returns True on success, False if git is unavailable or nothing to commit.
    """
    repo_root = _find_git_root(path)
    if repo_root is None:
        return False

    msg = message or f"[marketsage] Update {path.relative_to(repo_root)}"
    try:
        subprocess.run(
            ["git", "add", str(path)],
            cwd=repo_root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", msg, "--", str(path)],
            cwd=repo_root, check=True, capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def stamp_and_commit(path: Path, summary: str) -> dict[str, Any]:
    """Convenience: bump revision, then git commit."""
    fm = bump_revision(path, summary)
    git_commit_file(path, f"[marketsage] rev {fm['revision']}: {summary}")
    return fm


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_git_root(path: Path) -> Path | None:
    """Walk up from *path* looking for a .git directory."""
    cur = path.resolve().parent
    while cur != cur.parent:
        if (cur / ".git").is_dir():
            return cur
        cur = cur.parent
    return None
