"""
mcp_gateway/mcp/external_servers.py

External MCP Server integration - connect to and use tools from external MCP servers.

This module allows mcp_gateway to connect to external MCP servers and expose
their tools alongside local Odoo tools. This enables AI agents to use tools
from WhatsApp MCP, YouTube MCP, and other MCP servers.

Key classes:
  ExternalMCPServerManager — Manages connections to external MCP servers
  ExternalMCPServer — Wrapper for a single external MCP server connection

Dependencies:
  - mcp Python package (for MCP protocol)
  - httpx for HTTP transport
  - subprocess for stdio transport

Developer notes:
  - Supports both HTTP+SSE and stdio transports
  - Tools are cached and refreshed periodically
  - Connection errors are handled gracefully
  - Tools maintain namespace prefix to avoid conflicts
"""

import logging
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

# MCP SDK imports
try:
    from mcp.client import Client
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False
    _logger.warning('MCP SDK not installed. External MCP servers require: pip install mcp')


class ExternalMCPServer:
    """
    Connection to a single external MCP server.

    Wraps an MCP client connection and provides tool access.
    """

    def __init__(self, name: str, command: str, args: List[str],
                 env_vars: Dict[str, str] = None, transport: str = 'stdio'):
        """
        Initialize external MCP server connection.

        Args:
            name: Server name for identification
            command: Command to run (e.g., 'npx', 'python')
            args: Command arguments
            env_vars: Environment variables
            transport: 'stdio' or 'sse'
        """
        self.name = name
        self.command = command
        self.args = args
        self.env_vars = env_vars or {}
        self.transport = transport

        self._client = None
        self._tools = []
        self._last_refresh = None

        _logger.info('External MCP server configured: %s (%s)', name, transport)

    async def connect(self) -> bool:
        """
        Connect to the external MCP server.

        Returns:
            True if connected successfully
        """
        if not MCP_SDK_AVAILABLE:
            _logger.error('MCP SDK not available for external server: %s', self.name)
            return False

        try:
            if self.transport == 'stdio':
                self._client = stdio_client(
                    command=self.command,
                    args=self.args,
                    env=self.env_vars,
                )
            else:
                # For SSE, URL should be in args or separate config
                url = self.args[0] if self.args else None
                if not url:
                    raise ValueError('SSE transport requires URL')
                self._client = sse_client(url)

            await self._client.__aenter__()
            _logger.info('Connected to external MCP server: %s', self.name)
            return True

        except Exception as e:
            _logger.error('Failed to connect to %s: %s', self.name, str(e))
            return False

    async def disconnect(self):
        """Disconnect from the external MCP server."""
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as e:
                _logger.warning('Error disconnecting from %s: %s', self.name, str(e))
            self._client = None

    async def list_tools(self) -> List[Dict[str, Any]]:
        """
        List available tools from this MCP server.

        Returns:
            List of tool specifications
        """
        if not self._client:
            await self.connect()

        if not self._client:
            return []

        try:
            async with self._client as client:
                tools = await client.list_tools()
                self._tools = [
                    {
                        'name': f'{self.name}_{t.name}',  # Prefix to avoid conflicts
                        'original_name': t.name,
                        'description': t.description,
                        'input_schema': t.inputSchema,
                        'server_name': self.name,
                    }
                    for t in tools
                ]
                self._last_refresh = asyncio.get_event_loop().time()
                return self._tools

        except Exception as e:
            _logger.error('Error listing tools from %s: %s', self.name, str(e))
            return []

    async def call_tool(self, original_name: str,
                       arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a tool on this MCP server.

        Args:
            original_name: Original tool name (without prefix)
            arguments: Tool arguments

        Returns:
            Tool result
        """
        if not self._client:
            await self.connect()

        if not self._client:
            return {'success': False, 'error': 'Not connected'}

        try:
            async with self._client as client:
                result = await client.call_tool(original_name, arguments)
                return {'success': True, 'result': result}

        except Exception as e:
            _logger.error('Error calling tool %s on %s: %s',
                         original_name, self.name, str(e))
            return {'success': False, 'error': str(e)}


class ExternalMCPServerManager:
    """
    Manager for all external MCP server connections.

    Provides unified access to tools from all configured external servers.
    """

    def __init__(self):
        """Initialize the manager with no connections."""
        self._servers: Dict[str, ExternalMCPServer] = {}
        self._tools_cache: List[Dict[str, Any]] = []
        self._cache_duration = 300  # 5 minutes

    def add_server(self, name: str, command: str, args: List[str],
                  env_vars: Dict[str, str] = None, transport: str = 'stdio'):
        """
        Add an external MCP server configuration.

        Args:
            name: Server name
            command: Command to run
            args: Command arguments
            env_vars: Environment variables
            transport: 'stdio' or 'sse'
        """
        self._servers[name] = ExternalMCPServer(
            name=name,
            command=command,
            args=args,
            env_vars=env_vars,
            transport=transport,
        )
        _logger.info('Added external MCP server: %s', name)

    def remove_server(self, name: str):
        """
        Remove an external MCP server.

        Args:
            name: Server name
        """
        if name in self._servers:
            del self._servers[name]
            _logger.info('Removed external MCP server: %s', name)

    def load_from_config(self, configs: List[Dict[str, Any]]):
        """
        Load server configurations from a list.

        Args:
            configs: List of server config dicts with keys:
                - name: Server name
                - command: Command to run
                - args: Command arguments
                - env_vars: Optional environment variables
                - transport: 'stdio' or 'sse'
        """
        for config in configs:
            self.add_server(
                name=config['name'],
                command=config['command'],
                args=config.get('args', []),
                env_vars=config.get('env_vars'),
                transport=config.get('transport', 'stdio'),
            )

    async def refresh_tools(self) -> List[Dict[str, Any]]:
        """
        Refresh tools from all connected servers.

        Returns:
            Combined list of tools from all servers
        """
        all_tools = []

        for name, server in self._servers.items():
            try:
                tools = await server.list_tools()
                all_tools.extend(tools)
            except Exception as e:
                _logger.error('Error refreshing tools from %s: %s', name, str(e))

        self._tools_cache = all_tools
        _logger.info('Refreshed tools: %d from %d servers',
                    len(all_tools), len(self._servers))
        return all_tools

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """
        Get cached list of all tools.

        Returns:
            Combined list of tools from all servers
        """
        return self._tools_cache

    async def call_external_tool(self, namespaced_name: str,
                                 arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a tool on an external server.

        Args:
            namespaced_name: Tool name with namespace prefix
            arguments: Tool arguments

        Returns:
            Tool result
        """
        # Parse namespaced name
        parts = namespaced_name.split('_', 1)
        if len(parts) < 2:
            return {'success': False, 'error': 'Invalid tool name format'}

        server_name = parts[0]
        tool_name = parts[1]

        if server_name not in self._servers:
            return {'success': False, 'error': f'Unknown server: {server_name}'}

        server = self._servers[server_name]
        return await server.call_tool(tool_name, arguments)

    def get_connected_servers(self) -> List[str]:
        """
        Get list of connected server names.

        Returns:
            List of server names
        """
        return list(self._servers.keys())


# Singleton instance for use across the module
_manager = None


def get_manager() -> ExternalMCPServerManager:
    """Get the singleton manager instance."""
    global _manager
    if _manager is None:
        _manager = ExternalMCPServerManager()
    return _manager