"""
mcp_gateway/mcp/tools/__init__.py

Tool dispatcher and built-in tool definitions.

Modules:
  - dispatcher.py — Routes tool calls to ORM, HTTP, MCP servers
  - builtin_tools.py — Definitions for 14 pre-configured Odoo tools

Usage:
  dispatcher = ToolDispatcher()
  result = dispatcher.dispatch(tool, arguments, env, user)
"""

from .dispatcher import ToolDispatcher
from .builtin_tools import BUILTIN_TOOLS

__all__ = ['ToolDispatcher', 'BUILTIN_TOOLS']
