"""
mcp_gateway/models/__init__.py

Model registry for AI Gateway. Imports all model classes for Odoo's auto-discovery.

Models defined:
  - mcp.agent — AI agent configuration and management
  - mcp.tool — Tool registry (Odoo ORM, external API, MCP server)
  - mcp.tool.category — Tool categorization
  - mcp.tool.set — Pre-configured tool bundles
  - mcp.access.rule — Group and user access control
  - mcp.session — Conversation session audit log
  - mcp.session.message — Individual message in a session
  - mcp.prompt.template — Reusable prompt fragments
  - mcp.agent.memory — Session memory summaries
  - mcp.webhook.trigger — Automatic agent invocation
  - mcp.cost.entry — Token usage and cost tracking
  - mcp.external.server — External MCP server configurations
  - mcp.dashboard — Named dashboard pages grouping charts in the Chart Gallery
"""

from . import mcp_tool_category
from . import mcp_tool
from . import mcp_tool_set
from . import mcp_agent
from . import mcp_access_rule
from . import mcp_session
from . import mcp_prompt_template
from . import mcp_agent_memory
from . import mcp_webhook_trigger
from . import mcp_cost_entry
from . import mcp_external_server
from . import mcp_dashboard
from . import mcp_echart
from . import mcp_model_option

__all__ = [
    'mcp_tool_category',
    'mcp_tool',
    'mcp_tool_set',
    'mcp_agent',
    'mcp_access_rule',
    'mcp_session',
    'mcp_prompt_template',
    'mcp_agent_memory',
    'mcp_webhook_trigger',
    'mcp_cost_entry',
    'mcp_external_server',
    'mcp_dashboard',
    'mcp_echart',
]

