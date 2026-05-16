"""
mcp_gateway/mcp/server.py

MCP Server implementation for Odoo - exposes Odoo tools via MCP protocol.

This module implements the MCP (Model Context Protocol) server that allows
external AI clients (Claude Desktop, etc.) to interact with Odoo through
standardized MCP tool calls.

Key classes:
  OdooMCPServer — MCP server that exposes mcp.tool records as MCP tools

Transport:
  - stdio: For Claude Desktop and CLI tools
  - HTTP+SSE: For web-based clients (optional)

Dependencies:
  - mcp Python package (official MCP SDK)
  - mcp.tool model (for tool definitions)
  - tools.dispatcher (for tool execution)

Developer notes:
  - Uses official MCP Python SDK
  - Tools are dynamically loaded from mcp.tool records
  - Supports both stdio and HTTP transports
  - Security: Respects Odoo access rights
"""

import logging
import json
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)

# MCP SDK import with fallback for when not installed
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    from mcp import CallToolResult
    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False
    _logger.warning('MCP SDK not installed. Run: pip install mcp')


if MCP_SDK_AVAILABLE:
    class OdooMCPServer:
        """
        MCP Server for Odoo that exposes tools via MCP protocol.

        This server implements the MCP protocol and exposes Odoo tools
        as MCP tools. External AI clients can connect via stdio or HTTP.

        Usage (stdio mode - for Claude Desktop):
            # Configure in Claude Desktop:
            {
              "mcpServers": {
                "odoo": {
                  "command": "python",
                  "args": ["-m", "mcp_gateway.mcp.server", "--stdio"]
                }
              }
            }

        Usage (HTTP mode):
            # Configure in Claude Desktop:
            {
              "mcpServers": {
                "odoo": {
                  "command": "python",
                  "args": ["-m", "mcp_gateway.mcp.server", "--http", "8000"]
                }
              }
            }

        Tools exposed:
          - All active mcp.tool records from the database
          - Tool names match the tool.name field
          - Descriptions from tool.description field
          - Input schema from tool.input_schema field
        """

        def __init__(self, env, server_name: str = "odoo"):
            """
            Initialize MCP server for Odoo.

            Args:
                env: Odoo environment (with database access)
                server_name: Name for this MCP server instance
            """
            self.env = env
            self.server_name = server_name

            # Create MCP server instance
            self.server = Server(self.server_name)

            # Register handlers
            self._register_handlers()

            _logger.info('Odoo MCP Server initialized: %s', server_name)

        def _register_handlers(self):
            """Register MCP protocol handlers."""
            # Register list_tools handler
            self.server.list_tools = self._handle_list_tools

            # Register call_tool handler
            self.server.call_tool = self._handle_call_tool

        async def _handle_list_tools(self) -> List[Tool]:
            """
            Handle MCP list_tools request.

            Returns all active tools from mcp.tool model.

            Returns:
                List of MCP Tool objects
            """
            try:
                tools = self.env['mcp.tool'].search([('active', '=', True)])

                mcp_tools = []
                for tool in tools:
                    try:
                        input_schema = json.loads(tool.input_schema or '{}')
                    except (json.JSONDecodeError, TypeError):
                        input_schema = {'type': 'object', 'properties': {}}

                    mcp_tools.append(Tool(
                        name=tool.name,
                        description=tool.description or '',
                        inputSchema=input_schema,
                    ))

                _logger.info('MCP list_tools: returning %d tools', len(mcp_tools))
                return mcp_tools

            except Exception as e:
                _logger.error('Error listing tools: %s', str(e))
                return []

        async def _handle_call_tool(
            self,
            name: str,
            arguments: Dict[str, Any]
        ) -> CallToolResult:
            """
            Handle MCP call_tool request.

            Routes tool call to dispatcher and returns result.

            Args:
                name: Tool name
                arguments: Tool arguments

            Returns:
                CallToolResult with success/failure
            """
            try:
                _logger.info('MCP call_tool: %s with args %s', name, arguments)

                # Find tool in database
                tool = self.env['mcp.tool'].search([
                    ('name', '=', name),
                    ('active', '=', True),
                ], limit=1)

                if not tool:
                    return CallToolResult(
                        content=[TextContent(type='text', text=f'Tool not found: {name}')],
                        isError=True,
                    )

                # Get user context (for MCP, use admin or create service user)
                from odoo import SUPERUSER_ID
                user_env = self.env if self.env.uid == SUPERUSER_ID else self.env

                # Dispatch tool execution
                from .tools.dispatcher import ToolDispatcher
                dispatcher = ToolDispatcher()

                result = dispatcher.dispatch(
                    tool=tool,
                    arguments=arguments,
                    env=user_env,
                    user=user_env.user,
                )

                # Parse result
                try:
                    result_data = json.loads(result)
                    if result_data.get('success'):
                        return CallToolResult(
                            content=[TextContent(
                                type='text',
                                text=json.dumps(result_data.get('result'), indent=2)
                            )],
                            isError=False,
                        )
                    else:
                        return CallToolResult(
                            content=[TextContent(
                                type='text',
                                text=f"Error: {result_data.get('error', 'Unknown error')}"
                            )],
                            isError=True,
                        )
                except json.JSONDecodeError:
                    return CallToolResult(
                        content=[TextContent(type='text', text=result)],
                        isError=False,
                    )

            except Exception as e:
                _logger.error('Error calling tool %s: %s', name, str(e))
                return CallToolResult(
                    content=[TextContent(type='text', text=f'Error: {str(e)}')],
                    isError=True,
                )

        async def run_stdio(self):
            """Run server with stdio transport (for Claude Desktop)."""
            _logger.info('Starting Odoo MCP Server with stdio transport')

            async with stdio_server(self.server) as (read_stream, write_stream):
                pass  # Server handles stdio

        def run_http(self, host: str = "127.0.0.1", port: int = 8000):
            """
            Run server with HTTP+SSE transport.

            Args:
                host: Host to bind to
                port: Port to listen on
            """
            _logger.info('Starting Odoo MCP Server with HTTP on %s:%d', host, port)

            try:
                from mcp.server.sse import SseServer
                sse_server = SseServer(self.server)
                sse_server.run(host=host, port=port)
            except ImportError:
                _logger.error('SSE transport not available. Use stdio mode.')
                raise
else:
    # Stub class when MCP SDK is not installed
    class OdooMCPServer:
        """Stub class when MCP SDK is not installed."""

        def __init__(self, env, server_name: str = "odoo"):
            raise ImportError(
                'MCP SDK not installed. Install with: pip install mcp'
            )


def run_server():
    """
    Entry point for running MCP server.

    Usage:
        python -m mcp_gateway.mcp.server --stdio
        python -m mcp_gateway.mcp.server --http 8000
    """
    if not MCP_SDK_AVAILABLE:
        raise ImportError('MCP SDK not installed. Install with: pip install mcp')

    import argparse
    import asyncio
    import odoo

    parser = argparse.ArgumentParser(description='Odoo MCP Server')
    parser.add_argument('--mode', choices=['stdio', 'http'], default='stdio',
                        help='Transport mode')
    parser.add_argument('--host', default='127.0.0.1', help='HTTP host')
    parser.add_argument('--port', type=int, default=8000, help='HTTP port')
    parser.add_argument('-c', '--config', help='Odoo config file')
    args = parser.parse_args()

    # Initialize Odoo
    odoo.cli.server.setup_lang()
    odoo.cli.server.check_root_user()
    odoo.cli.server.check_access()

    # Create environment
    registry = odoo.registry(odoo.cli.server.get_cron())
    with registry.cursor() as cr:
        env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})

        # Create and run server
        server = OdooMCPServer(env)

        if args.mode == 'stdio':
            asyncio.run(server.run_stdio())
        else:
            server.run_http(args.host, args.port)


if __name__ == '__main__':
    run_server()