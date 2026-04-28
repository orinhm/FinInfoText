"""
Scraper auto-discovery and registry.

Every ``.py`` module in this directory (except ``__init__``) is a scraper
plugin.  Each must expose a ``fetch(**params) → list[dict]`` function.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any, Callable

_SCRAPERS: dict[str, Callable[..., list[dict]]] | None = None


def _discover() -> dict[str, Callable[..., list[dict]]]:
    """Import every sibling module and collect its ``fetch`` function."""
    global _SCRAPERS
    if _SCRAPERS is not None:
        return _SCRAPERS

    _SCRAPERS = {}
    pkg_path = str(Path(__file__).parent)
    for _importer, mod_name, _is_pkg in pkgutil.iter_modules([pkg_path]):
        mod = importlib.import_module(f"marketsage.scrapers.{mod_name}")
        if hasattr(mod, "fetch"):
            _SCRAPERS[mod_name] = mod.fetch
    return _SCRAPERS


def get_registry() -> dict[str, Callable[..., list[dict[str, Any]]]]:
    """Return ``{scraper_name: fetch_function}`` mapping."""
    return _discover()


def list_scrapers() -> list[str]:
    """Return names of all available scrapers."""
    return list(_discover().keys())
