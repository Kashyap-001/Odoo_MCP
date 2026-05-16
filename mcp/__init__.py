"""
mcp_gateway/mcp/__init__.py

MCP (Model Context Protocol) gateway engine and provider adapters.

Subpackages:
  - providers/ — LLM provider adapters (Anthropic, OpenAI, Gemini, Ollama)
  - tools/ — Tool dispatcher and built-in tool definitions

Key classes:
  - McpGateway — Agentic loop engine
  - AbstractProvider — Provider adapter base class
  - ToolDispatcher — Routes tool calls to Odoo ORM / HTTP / MCP servers
  - OdooMCPServer — MCP server for Odoo (exposes tools via MCP protocol)
  - ExternalMCPServerManager — Manages external MCP server connections
"""

from . import gateway
from . import providers
from . import tools

# MCP protocol server (optional - requires mcp Python package)
try:
    from . import server
    from . import external_servers
    __all__ = ['gateway', 'providers', 'tools', 'server', 'external_servers']
except ImportError:
    __all__ = ['gateway', 'providers', 'tools']
