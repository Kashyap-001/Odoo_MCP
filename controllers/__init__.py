"""
mcp_gateway/controllers/__init__.py

HTTP API controllers for AI Gateway.

Modules:
  - chat_controller.py — JSON-RPC and HTTP endpoints for chat, webhooks, etc.

Endpoints:
  POST   /mcp/chat — Chat with agent
  GET    /mcp/agents/available — List accessible agents
  GET    /mcp/tools/available — List accessible tools
  GET    /mcp/session/<id>/transcript — Download session transcript
  POST   /mcp/webhook/<token> — Webhook trigger endpoint
"""

from . import chat_controller

__all__ = ['chat_controller']
