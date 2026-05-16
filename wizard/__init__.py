"""
mcp_gateway/wizard/__init__.py

Wizard models for in-Odoo interactions.

Wizards:
  - mcp.chat.wizard — In-Odoo chat interface
  - mcp.tool.scan.wizard — Auto-discover tools from Odoo models
"""

from . import mcp_chat_wizard
from . import mcp_tool_scan_wizard
from . import mcp_connection_test_wizard

__all__ = ['mcp_chat_wizard', 'mcp_tool_scan_wizard', 'mcp_connection_test_wizard']
