"""
MCP (Model Context Protocol) server for MarketSage tools.

Exposes all MarketSage tools (built-in, custom, generated) as MCP tools
so they can be used by the Gemini CLI, Claude Desktop, or any MCP client.

Usage:
    # Direct (stdio transport — used by Gemini CLI):
    python -m marketsage.mcp_server

    # Or via the Gemini CLI settings.json:
    {
      "mcpServers": {
        "marketsage": {
          "command": "/home/ori/projects/FinInfoText/venv/bin/python",
          "args": ["-m", "marketsage.mcp_server"]
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from marketsage.tools import (
    get_all_tool_declarations,
    execute_tool,
    TOOL_DECLARATIONS,
)

logger = logging.getLogger("marketsage.mcp")

# Create the MCP server
mcp = FastMCP(
    "MarketSage",
    instructions="MarketSage investment intelligence tools — scrapers, "
                 "knowledge management, and agent loading.",
)


def _register_tool(decl: dict[str, Any]) -> None:
    """Dynamically register a single MarketSage tool as an MCP tool."""
    name = decl["name"]
    description = decl.get("description", "")
    props = decl.get("parameters", {}).get("properties", {})
    required = decl.get("parameters", {}).get("required", [])

    # Build the parameter annotations for the wrapper function
    param_info = {}
    for pname, pschema in props.items():
        ptype = pschema.get("type", "string")
        pdesc = pschema.get("description", "")
        is_required = pname in required
        param_info[pname] = {
            "type": ptype,
            "description": pdesc,
            "required": is_required,
        }

    # Create a closure that calls execute_tool
    def _make_handler(tool_name: str, params: dict):
        async def handler(**kwargs) -> str:
            # Convert empty strings to missing for optional params
            clean_args = {
                k: v for k, v in kwargs.items()
                if v is not None and v != ""
            }
            logger.info("MCP tool call: %s(%s)", tool_name, clean_args)
            result = execute_tool(tool_name, clean_args)
            return result
        handler.__name__ = tool_name
        handler.__doc__ = description
        return handler

    handler = _make_handler(name, param_info)

    # Register with FastMCP using the decorator-free API
    mcp.tool(name=name, description=description)(handler)


def _register_all_tools() -> None:
    """Register all MarketSage tools as MCP tools."""
    all_decls = get_all_tool_declarations()
    for decl in all_decls:
        try:
            _register_tool(decl)
        except Exception as exc:
            logger.warning("Failed to register MCP tool '%s': %s",
                           decl.get("name"), exc)

    logger.info("Registered %d MarketSage tools as MCP tools", len(all_decls))


# Register all tools at import time
_register_all_tools()


if __name__ == "__main__":
    mcp.run(transport="stdio")
