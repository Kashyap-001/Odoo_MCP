"""
mcp_gateway/mcp/tools/dispatcher.py

Tool call dispatcher — routes calls to Odoo ORM, HTTP APIs, or MCP servers.

Key classes:
  ToolDispatcher — Routes tool execution to the correct backend

Dependencies:
  - mcp.tool model (for tool configuration)
  - requests for HTTP API calls
  - json for response parsing

Developer notes:
  - dispatch() routes to three paths: Odoo ORM, External HTTP, MCP server
  - Never raises exceptions — catches all errors and returns error JSON
  - All results returned as JSON strings for AI provider consumption
  - Respects user context (uses env for RLS and access control)
"""

import logging
import json
import re
import requests
from datetime import datetime
from odoo.fields import Command

_logger = logging.getLogger(__name__)


class ToolDispatcher:
    """
    Routes tool calls to the correct backend execution environment.

    Supports three execution paths:
      1. Odoo ORM — search_read, create, write, custom methods on Odoo models
      2. External HTTP API — GET/POST/PUT/DELETE to external endpoints
      3. Custom MCP server — POST to MCP server with tool name and args

    All results are returned as JSON strings for LLM provider consumption.
    """

    def dispatch(self, tool, arguments: dict, env, user) -> str:
        """
        Execute a tool call and return result as JSON string.

        Routes to the correct backend based on tool.tool_type.
        Never raises — catches exceptions and returns error JSON.

        Args:
            tool: mcp.tool record with configuration
            arguments (dict): Tool parameters from AI
            env: Odoo environment for database access
            user: res.users executing the tool

        Returns:
            str: JSON string with result or error
                Success: {"success": true, "result": ...}
                Error: {"success": false, "error": "..."}

        Example:
            tool = env['mcp.tool'].search([('name','=','partner_search')], limit=1)
            result = ToolDispatcher().dispatch(tool, {'name': 'John'}, env, user)
            # Output: '{"success": true, "result": [{"id": 1, "name": "John"}]}'
        """
        try:
            if tool.tool_type == 'odoo':
                return self._dispatch_odoo(tool, arguments, env, user)
            elif tool.tool_type == 'external':
                return self._dispatch_http(tool, arguments)
            elif tool.tool_type == 'mcp_server':
                return self._dispatch_mcp_server(tool, arguments)
            else:
                raise ValueError(f'Unknown tool type: {tool.tool_type}')

        except Exception as e:
            _logger.error('Tool dispatch failed for %s: %s', tool.name, str(e))
            return json.dumps({
                'success': False,
                'error': str(e)[:500],  # Truncate very long errors
            })

    def _dispatch_odoo(self, tool, arguments: dict, env, user) -> str:
        """
        Execute tool on Odoo ORM.

        Calls specified model method with given arguments.

        Args:
            tool: mcp.tool with tool_type='odoo'
            arguments: Method parameters
            env: Odoo environment
            user: res.users executing

        Returns:
            str: JSON with result
        """
        try:
            model_name = tool.odoo_model
            method_name = tool.odoo_method

            # ── Apply access control ────────────────────────────────────
            # If sudo_execute is True, use sudo() to bypass ACL checks
            if tool.sudo_execute:
                model = env[model_name].sudo()
            else:
                model = env[model_name].with_user(user)

            # ── Execute method ──────────────────────────────────────────
            if method_name == 'search_read':
                domain = json.loads(tool.odoo_domain or '[]')
                fields = tool.odoo_fields.split(',') if tool.odoo_fields else []
                limit = tool.odoo_limit or 10
                records = model.search_read(domain, fields, limit=limit)
                result = records
            elif method_name == 'create':
                # Parse and convert arguments for Odoo ORM compatibility
                _logger.debug('calendar_event_create: args type=%s', type(arguments).__name__)
                vals = self._prepare_create_values(arguments, model_name)
                # Check if parsing returned an error
                if isinstance(vals, dict) and 'error' in vals:
                    return json.dumps({
                        'success': False,
                        'error': vals.get('error'),
                        'message': 'Failed to parse arguments'
                    })
                try:
                    record = model.create(vals)
                    result = {'id': record.id, 'name': record.name}
                except Exception as e:
                    _logger.error('calendar_event_create ORM failed: %s', str(e))
                    return json.dumps({
                        'success': False,
                        'error': str(e),
                        'message': f'Failed to create {model_name}: {str(e)}'
                    })
            elif method_name == 'write':
                record_id = arguments.pop('id')
                record = model.browse(record_id)
                record.write(arguments)
                result = {'id': record.id, 'updated': True}
            elif method_name == 'search':
                domain = arguments.get('domain', [])
                limit = arguments.get('limit', 10)
                result = model.search(domain, limit=limit).ids
            else:
                # Custom method call
                method = getattr(model, method_name, None)
                if not method:
                    raise ValueError(f'Method {method_name} not found on {model_name}')
                result = method(**arguments)

            return json.dumps({'success': True, 'result': result})

        except Exception as e:
            return json.dumps({'success': False, 'error': str(e)[:500]})

    def _dispatch_http(self, tool, arguments: dict) -> str:
        """
        Execute tool via HTTP API call.

        POSTs to external endpoint with auth and arguments.

        Args:
            tool: mcp.tool with tool_type='external'
            arguments: Request parameters

        Returns:
            str: JSON with result
        """
        try:
            url = tool.endpoint_url
            method = tool.http_method
            headers = self._build_auth_headers(tool)
            timeout = tool.timeout_seconds

            # ── Make HTTP call ──────────────────────────────────────────
            if method == 'GET':
                response = requests.get(url, params=arguments, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, json=arguments, headers=headers, timeout=timeout)
            elif method == 'PUT':
                response = requests.put(url, json=arguments, headers=headers, timeout=timeout)
            elif method == 'DELETE':
                response = requests.delete(url, params=arguments, headers=headers, timeout=timeout)
            else:
                raise ValueError(f'Unsupported HTTP method: {method}')

            response.raise_for_status()
            data = response.json()

            # ── Extract response path if specified ───────────────────────
            if tool.response_path:
                result = self._extract_path(data, tool.response_path)
            else:
                result = data

            return json.dumps({'success': True, 'result': result})

        except Exception as e:
            return json.dumps({'success': False, 'error': str(e)[:500]})

    def _dispatch_mcp_server(self, tool, arguments: dict) -> str:
        """
        Execute tool on custom MCP server.

        POSTs to /call endpoint with tool name and arguments.

        Args:
            tool: mcp.tool with tool_type='mcp_server'
            arguments: Tool parameters

        Returns:
            str: JSON with result
        """
        try:
            url = f'{tool.mcp_server_url}/call'
            headers = {
                'Authorization': f'Bearer {tool.mcp_server_key}',
                'Content-Type': 'application/json',
            }
            payload = {
                'tool': tool.name,
                'arguments': arguments,
            }

            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()

            return json.dumps({'success': True, 'result': response.json()})

        except Exception as e:
            return json.dumps({'success': False, 'error': str(e)[:500]})

    def _extract_path(self, data: dict, path: str):
        """
        Extract nested value from dict using dot notation.

        Args:
            data (dict): Source data
            path (str): Dot-notation path (e.g., 'response.data.items')

        Returns:
            Value at path, or None if not found

        Example:
            data = {'response': {'data': {'items': [1,2,3]}}}
            value = _extract_path(data, 'response.data.items')
            # Output: [1,2,3]
        """
        parts = path.split('.')
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    def _build_auth_headers(self, tool) -> dict:
        """
        Build Authorization headers based on tool auth_type.

        Args:
            tool: mcp.tool with auth configuration

        Returns:
            dict: Headers to include in HTTP request
        """
        headers = {}

        if tool.auth_type == 'none':
            pass
        elif tool.auth_type == 'bearer':
            headers['Authorization'] = f'Bearer {tool.auth_value}'
        elif tool.auth_type == 'basic':
            import base64
            encoded = base64.b64encode(tool.auth_value.encode()).decode()
            headers['Authorization'] = f'Basic {encoded}'
        elif tool.auth_type == 'api_key_header':
            headers[tool.auth_header_name] = tool.auth_value

        return headers

    def _prepare_create_values(self, arguments, model_name: str) -> dict:
        """
        Parse and convert arguments for Odoo ORM compatibility.

        Handles:
        - Arguments as JSON string: parse to dict
        - Datetime fields (start, stop, etc.): parse string to datetime
        - Relational fields (partner_ids, etc.): convert list to Command tuples
        - Date fields: parse string to date

        Args:
            arguments: Raw arguments from AI (can be dict or JSON string)
            model_name: Odoo model name

        Returns:
            dict: Values ready for model.create()
        """
        # Parse JSON string to dict if needed
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError) as e:
                _logger.error('Failed to parse tool arguments as JSON: %s', str(e))
                return {'error': f'Invalid arguments format: {str(e)}'}

        if not isinstance(arguments, dict):
            _logger.error('Tool arguments are not a dict after parsing: %s', type(arguments))
            return {'error': f'Arguments must be a dict, got: {type(arguments).__name__}'}

        vals = {}

        # Fields that are datetime
        datetime_fields = {'start', 'stop', 'date', 'date_deadline', 'planned_date',
                          'effective_date', 'create_date', 'write_date'}

        # Fields that are dates (not datetime)
        date_fields = {'invoice_date', 'date_order', 'date_invoice', 'date'}

        # Fields that are relational (Many2many/One2many)
        relational_fields = {'partner_ids', 'order_line', 'invoice_line_ids',
                            'tag_ids', 'category_id', 'product_id', 'user_id',
                            'team_id', 'company_id', 'currency_id'}

        for key, value in arguments.items():
            if value is None:
                continue

            # Handle datetime fields
            if key in datetime_fields and isinstance(value, str):
                # Try parsing datetime string
                value = self._parse_datetime(value)

            # Handle date fields
            elif key in date_fields and isinstance(value, str):
                # Try parsing date string
                value = self._parse_date(value)

            # Handle relational fields (Many2many/One2many)
            elif key in relational_fields:
                if isinstance(value, list):
                    # Convert list of IDs to Command.set() format
                    if value and isinstance(value[0], int):
                        value = Command.set(value)
                    # If already Command tuples, leave as-is
                elif isinstance(value, str) and value:
                    # Comma-separated string of IDs
                    try:
                        ids = [int(x.strip()) for x in value.split(',') if x.strip().isdigit()]
                        value = Command.set(ids)
                    except ValueError:
                        pass  # Keep as-is if not parseable

            vals[key] = value

        return vals

    def _parse_datetime(self, value: str):
        """
        Parse datetime string to Odoo-compatible format.

        Handles formats:
        - '2026-05-15 14:00:00'
        - '2026-05-15T14:00:00'
        - '2026-05-15T14:00:00Z'
        - '2026-05-15 14:00'

        Returns:
            datetime object or original string if parsing fails
        """
        if not isinstance(value, str):
            return value

        # Remove timezone suffix if present
        value = re.sub(r'[+-]\d{2}:?\d{2}$', '', value)
        value = re.sub(r'Z$', '', value)
        value = value.strip()

        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%dT%H:%M',
            '%Y-%m-%d',
        ]

        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue

        # Return original if no format matched
        return value

    def _parse_date(self, value: str):
        """
        Parse date string to Odoo-compatible format.

        Handles formats:
        - '2026-05-15'
        - '05/15/2026'
        - '15-05-2026'

        Returns:
            date object or original string if parsing fails
        """
        if not isinstance(value, str):
            return value

        from datetime import date

        formats = [
            '%Y-%m-%d',
            '%m/%d/%Y',
            '%d-%m-%Y',
            '%Y/%m/%d',
        ]

        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue

        return value
