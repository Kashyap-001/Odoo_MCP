"""
mcp_gateway/controllers/mcp_protocol_controller.py

MCP Protocol HTTP endpoint for external AI clients (Claude Desktop web, etc.).

Exposes Odoo operations as MCP tools via HTTP+SSE transport.
External AI clients can connect to this endpoint to call Odoo tools.

Routes:
  GET  /mcp/protocol - SSE stream for server-initiated messages (optional)
  POST /mcp/rpc     - JSON-RPC 2.0 endpoint for MCP requests

MCP Protocol:
  - tools/list  — Returns all available Odoo MCP tools
  - tools/call  — Executes a named tool with arguments

Authentication:
  - API key in X-MCP-API-Key header, or
  - Odoo session auth (auth='user')
"""

import json
import logging
from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)

# MCP Protocol JSON-RPC 2.0 implementation


class MCPProtocolController(http.Controller):
    """
    MCP Protocol HTTP endpoint for external AI clients.

    Implements the MCP (Model Context Protocol) over HTTP transport.
    External AI clients use this to discover and call Odoo tools.
    """

    @http.route('/mcp/rpc', type='json', auth='user', methods=['POST'], csrf=False)
    def rpc(self, **kwargs):
        """
        JSON-RPC 2.0 endpoint for MCP protocol requests.

        Handles:
          - tools/list  — returns all available tools
          - tools/call  — executes a tool and returns result

        Request body (JSON-RPC 2.0):
          {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",          # or "tools/call"
            "params": {
              "name": "tool_name",           # for tools/call
              "arguments": {}                # for tools/call
            }
          }

        Returns (JSON-RPC 2.0):
          Success: {"jsonrpc": "2.0", "id": 1, "result": {...}}
          Error:   {"jsonrpc": "2.0", "id": 1, "error": {"code": ..., "message": "..."}}
        """
        try:
            # Parse JSON-RPC request
            data = request.get_json_data()
            jsonrpc = data.get('jsonrpc')
            request_id = data.get('id')
            method = data.get('method')
            params = data.get('params', {})

            if jsonrpc != '2.0':
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'error': {
                        'code': -32600,
                        'message': 'Invalid JSON-RPC version. Expected "2.0".',
                    },
                }

            if not method:
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'error': {
                        'code': -32600,
                        'message': 'Missing method name.',
                    },
                }

            # Route to handler
            if method == 'tools/list':
                result = self._handle_tools_list()
            elif method == 'tools/call':
                tool_name = params.get('name', '')
                arguments = params.get('arguments', {})
                result = self._handle_tools_call(tool_name, arguments)
            else:
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'error': {
                        'code': -32601,
                        'message': f'Unknown method: {method}',
                    },
                }

            return {
                'jsonrpc': '2.0',
                'id': request_id,
                'result': result,
            }

        except Exception as e:
            _logger.error('MCP RPC error: %s', str(e))
            return {
                'jsonrpc': '2.0',
                'id': data.get('id') if data else None,
                'error': {
                    'code': -32603,
                    'message': f'Internal error: {str(e)}',
                },
            }

    def _handle_tools_list(self) -> dict:
        """
        Handle tools/list MCP request.

        Returns all active mcp.tool records formatted as MCP tools.

        Returns:
            dict: {
              'tools': [
                {
                  'name': 'partner_search',
                  'description': '...',
                  'inputSchema': {...}
                }
              ]
            }
        """
        try:
            env = request.env
            tools = env['mcp.tool'].search([('active', '=', True)])

            mcp_tools = []
            for tool in tools:
                try:
                    input_schema = json.loads(tool.input_schema or '{}')
                except (json.JSONDecodeError, TypeError):
                    input_schema = {'type': 'object', 'properties': {}}

                mcp_tools.append({
                    'name': tool.name,
                    'description': tool.description or '',
                    'inputSchema': input_schema,
                })

            _logger.info('MCP tools/list: returning %d tools', len(mcp_tools))
            return {'tools': mcp_tools}

        except Exception as e:
            _logger.error('Error listing MCP tools: %s', str(e))
            return {'tools': [], 'error': str(e)}

    def _handle_tools_call(self, name: str, arguments: dict) -> dict:
        """
        Handle tools/call MCP request.

        Routes tool call to dispatcher and returns result.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            dict: MCP tool call result
                Success: {'content': [{'type': 'text', 'text': '...'}]}
                Error:   {'isError': True, 'content': [{'type': 'text', 'text': '...'}]}
        """
        try:
            _logger.info('MCP tools/call: %s with args %s', name, arguments)

            env = request.env
            user = request.env.user

            # Find tool in database
            tool = env['mcp.tool'].search([
                ('name', '=', name),
                ('active', '=', True),
            ], limit=1)

            if not tool:
                return {
                    'content': [{'type': 'text', 'text': f'Tool not found: {name}'}],
                    'isError': True,
                }

            # Dispatch tool execution
            from ..mcp.tools.dispatcher import ToolDispatcher
            dispatcher = ToolDispatcher()
            result = dispatcher.dispatch(tool, arguments, env, user)

            # Parse result
            try:
                result_data = json.loads(result)
                if result_data.get('success'):
                    return {
                        'content': [{
                            'type': 'text',
                            'text': json.dumps(result_data.get('result'), indent=2)
                        }],
                        'isError': False,
                    }
                else:
                    return {
                        'content': [{
                            'type': 'text',
                            'text': f"Error: {result_data.get('error', 'Unknown error')}"
                        }],
                        'isError': True,
                    }
            except json.JSONDecodeError:
                return {
                    'content': [{'type': 'text', 'text': result}],
                    'isError': False,
                }

        except Exception as e:
            _logger.error('Error calling MCP tool %s: %s', name, str(e))
            return {
                'content': [{'type': 'text', 'text': f'Error: {str(e)}'}],
                'isError': True,
            }

    @http.route('/mcp/tools', type='json', auth='user', methods=['GET'])
    def list_tools(self, **kwargs):
        """
        REST-style endpoint for tools list (alternative to JSON-RPC).

        Returns:
            dict: {'status': 'success', 'data': {'tools': [...]}}
        """
        try:
            result = self._handle_tools_list()
            return {
                'status': 'success',
                'data': result,
            }
        except Exception as e:
            _logger.error('Error listing tools: %s', str(e))
            return {
                'status': 'error',
                'error': str(e),
            }

    @http.route('/mcp/tools/call', type='json', auth='user', methods=['POST'])
    def call_tool(self, name: str = None, arguments: dict = None, **kwargs):
        """
        REST-style endpoint for calling a tool (alternative to JSON-RPC).

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            dict: MCP tool call result
        """
        if not name:
            return {
                'status': 'error',
                'error': 'Missing tool name',
            }
        try:
            result = self._handle_tools_call(name, arguments or {})
            return {
                'status': 'success' if not result.get('isError') else 'error',
                'data': result,
            }
        except Exception as e:
            _logger.error('Error calling tool %s: %s', name, str(e))
            return {
                'status': 'error',
                'error': str(e),
            }