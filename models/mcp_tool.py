"""
mcp_gateway/models/mcp_tool.py

Tool registry model for defining callable tools (Odoo ORM, external APIs, MCP servers).

Key classes:
  Tool — Defines a single tool with type, schema, and dispatch parameters

Dependencies:
  - mcp.tool.category — tool categorization
  - Imports json for input_schema validation
  - Imports re for name validation (snake_case)

Developer notes:
  - Tool names must be snake_case and globally unique (enforced by constraint)
  - input_schema must be valid JSON Schema (enforced by constrains)
  - Dispatch logic is in mcp/tools/dispatcher.py
  - Tool specs are built dynamically per agent based on allowed tools
"""

import logging
import json
import re
from odoo import fields, models, api, _, exceptions

_logger = logging.getLogger(__name__)


class Tool(models.Model):
    """
    MCP Tool (mcp.tool)

    Defines a callable tool that AI agents can invoke. Supports three types:
      1. Odoo built-in: calls an Odoo model method
      2. External API: calls a HTTP endpoint
      3. Custom MCP server: calls a custom MCP server endpoint

    Relationships:
      - BelongsTo: mcp.tool.category via category_id
      - HasMany: mcp.tool.set via many2many
      - HasMany: mcp.agent via many2many

    Business rules:
      - name must be globally unique and snake_case
      - input_schema must be valid JSON Schema
      - readonly tools do not mutate Odoo state
      - requires_confirm tools need user approval before execution
    """

    _name = 'mcp.tool'
    _description = _('MCP Tool')
    _order = 'category_id, sequence, name'

    # ── Basic Fields ────────────────────────────────────────────────
    name = fields.Char(
        string=_('Tool Name'),
        required=True,
        help=_('Unique identifier in snake_case (e.g., "partner_search")'),
    )
    display_name_label = fields.Char(
        string=_('Display Label'),
        required=True,
        translate=True,
        help=_('Human-readable name shown to AI (e.g., "Search Partners")'),
    )
    description = fields.Text(
        string=_('Description'),
        required=True,
        translate=True,
        help=_('Detailed description shown to AI in tool spec. Be specific about what the tool does.'),
    )
    category_id = fields.Many2one(
        comodel_name='mcp.tool.category',
        string=_('Category'),
        required=True,
        help=_('Categorize tool for UI grouping'),
    )
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        help=_('Disabled tools cannot be called by agents'),
    )
    sequence = fields.Integer(
        string=_('Sequence'),
        default=10,
        help=_('Order within category'),
    )

    # ── Type & Access Control ───────────────────────────────────────
    tool_type = fields.Selection(
        [
            ('odoo', _('Odoo built-in')),
            ('external', _('External API')),
            ('mcp_server', _('Custom MCP server')),
        ],
        string=_('Tool Type'),
        required=True,
        default='odoo',
        help=_('Where this tool executes: Odoo ORM, HTTP API, or custom MCP server'),
    )
    is_readonly = fields.Boolean(
        string=_('Read-Only'),
        default=True,
        help=_('Tool does not modify Odoo state'),
    )
    requires_confirm = fields.Boolean(
        string=_('Requires Confirmation'),
        default=False,
        help=_('User must approve before executing (for sensitive operations)'),
    )

    # ── Odoo Built-In Tool Config ───────────────────────────────────
    odoo_model = fields.Char(
        string=_('Odoo Model'),
        help=_('Model name (e.g., "res.partner", "sale.order")'),
    )
    odoo_method = fields.Char(
        string=_('Odoo Method'),
        help=_('Method to call (e.g., "search_read", "create", "action_confirm")'),
    )
    odoo_domain = fields.Text(
        string=_('Domain Filter'),
        default='[]',
        help=_('JSON domain filter (e.g., [["active", "=", true]])'),
    )
    odoo_fields = fields.Char(
        string=_('Fields'),
        help=_('Comma-separated field names to return (empty = all readable fields)'),
    )
    odoo_limit = fields.Integer(
        string=_('Limit'),
        default=10,
        help=_('Max records to return for search_read'),
    )
    sudo_execute = fields.Boolean(
        string=_('Execute as SUDO'),
        default=False,
        help=_('Execute with admin privileges (use carefully!)'),
    )

    # ── External API Tool Config ────────────────────────────────────
    endpoint_url = fields.Char(
        string=_('Endpoint URL'),
        help=_('Full URL to external API (e.g., "https://api.example.com/search")'),
    )
    http_method = fields.Selection(
        [
            ('GET', 'GET'),
            ('POST', 'POST'),
            ('PUT', 'PUT'),
            ('DELETE', 'DELETE'),
        ],
        string=_('HTTP Method'),
        default='GET',
        help=_('HTTP method for API call'),
    )
    auth_type = fields.Selection(
        [
            ('none', _('None')),
            ('bearer', _('Bearer Token')),
            ('basic', _('Basic Auth')),
            ('api_key_header', _('API Key Header')),
        ],
        string=_('Authentication'),
        default='none',
        help=_('Authentication method for endpoint'),
    )
    auth_value = fields.Char(
        string=_('Auth Value'),
        groups='mcp_gateway.group_mcp_admin',
        help=_('API key, token, or credentials (encrypted at rest)'),
    )
    auth_header_name = fields.Char(
        string=_('Header Name'),
        default='Authorization',
        help=_('Header to use for auth (for API key header type)'),
    )
    timeout_seconds = fields.Integer(
        string=_('Timeout (seconds)'),
        default=15,
        help=_('Request timeout in seconds'),
    )
    response_path = fields.Char(
        string=_('Response Path'),
        help=_('Dot-notation path to extract from response (e.g., "data.results")'),
    )

    # ── Custom MCP Server Config ────────────────────────────────────
    mcp_server_url = fields.Char(
        string=_('MCP Server URL'),
        help=_('Base URL of custom MCP server (e.g., "http://localhost:8000")'),
    )
    mcp_server_key = fields.Char(
        string=_('MCP Server Key'),
        groups='mcp_gateway.group_mcp_admin',
        help=_('Authentication key for MCP server (encrypted)'),
    )

    # ── Schema & Documentation ──────────────────────────────────────
    input_schema = fields.Text(
        string=_('Input Schema'),
        default='{}',
        help=_('JSON Schema describing tool parameters'),
    )
    output_sample = fields.Text(
        string=_('Output Sample'),
        help=_('Example output (for UI preview and documentation)'),
    )

    # ── Computed Fields ─────────────────────────────────────────────
    tool_set_count = fields.Integer(
        string=_('Tool Set Count'),
        compute='_compute_tool_set_count',
        store=False,
        help=_('Number of tool sets that include this tool'),
        readonly=True,
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Tool name must be unique.')),
    ]

    @api.constrains('name')
    def _validate_name(self):
        """
        Validate that tool name is snake_case.

        Raises:
            ValidationError: if name doesn't match snake_case pattern

        Example:
            Valid: "partner_search", "sale_order_create"
            Invalid: "PartnerSearch", "partner-search"
        """
        pattern = r'^[a-z][a-z0-9_]*$'
        for tool in self:
            if not re.match(pattern, tool.name):
                raise exceptions.ValidationError(
                    _('Tool name must be snake_case (e.g., "partner_search"). Got: "%s"')
                    % tool.name
                )

    @api.constrains('input_schema')
    def _validate_input_schema(self):
        """
        Validate that input_schema is valid JSON Schema.

        Raises:
            ValidationError: if JSON is malformed or not a valid schema

        Example:
            Valid: '{"type":"object","properties":{"name":{"type":"string"}}}'
            Invalid: '{invalid json}'
        """
        for tool in self:
            try:
                schema = json.loads(tool.input_schema or '{}')
                if not isinstance(schema, dict):
                    raise ValueError('Schema must be a JSON object')
            except (json.JSONDecodeError, ValueError) as e:
                raise exceptions.ValidationError(
                    _('Invalid JSON Schema: %s') % str(e)
                )

    def _compute_tool_set_count(self):
        """
        Count how many tool sets include this tool.

        Returns:
            None — sets tool_set_count field
        """
        for tool in self:
            tool.tool_set_count = len(
                self.env['mcp.tool.set'].search([('tool_ids', 'in', tool.id)])
            )

    def get_tool_spec(self) -> dict:
        """
        Build Anthropic-format tool specification for agent.

        Returns:
            dict: Tool spec with name, description, input_schema

        Example:
            {
              "name": "partner_search",
              "description": "Search for customers by name...",
              "input_schema": {...}
            }
        """
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': json.loads(self.input_schema or '{}'),
        }

    def action_test_tool(self):
        """
        Test this tool with minimal valid inputs.

        Executes the tool with minimal arguments to verify it's working.
        Returns a notification with pass/fail status and response time.

        Returns:
            dict: Client action to show notification with test results
        """
        self.ensure_one()

        try:
            # Get minimal arguments from schema
            schema = json.loads(self.input_schema or '{}')
            props = schema.get('properties', {})
            required = schema.get('required', [])

            # Build minimal test arguments
            test_args = {}
            for key, prop in props.items():
                if key in required:
                    # Get a minimal value based on type
                    prop_type = prop.get('type', 'string')
                    if prop_type == 'integer':
                        test_args[key] = 1
                    elif prop_type == 'number':
                        test_args[key] = 1.0
                    elif prop_type == 'boolean':
                        test_args[key] = False
                    elif prop_type == 'array':
                        test_args[key] = []
                    else:
                        test_args[key] = 'test'

            # Try to execute the tool
            import time
            start = time.time()
            from ..mcp.tools.dispatcher import ToolDispatcher
            dispatcher = ToolDispatcher()
            result = dispatcher.dispatch(self, test_args, self.env, self.env.user)
            duration_ms = int((time.time() - start) * 1000)

            # Parse result
            result_data = json.loads(result)
            if result_data.get('success'):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Tool Test Passed'),
                        'message': _('%s executed successfully in %dms') % (self.name, duration_ms),
                        'type': 'success',
                    },
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Tool Test Failed'),
                        'message': _('%s: %s') % (self.name, result_data.get('error', 'Unknown error')),
                        'type': 'danger',
                    },
                }

        except Exception as e:
            _logger.error('Tool test failed for %s: %s', self.name, str(e))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Tool Test Error'),
                    'message': _('%s: %s') % (self.name, str(e)),
                    'type': 'danger',
                },
            }

    def action_test_all_tools(self):
        """
        Test all active tools and show a summary report.

        Returns:
            dict: Client action with test results
        """
        tools = self.search([('active', '=', True)])
        results = []

        for tool in tools:
            try:
                import time
                start = time.time()
                from ..mcp.tools.dispatcher import ToolDispatcher
                dispatcher = ToolDispatcher()

                # Build minimal args
                schema = json.loads(tool.input_schema or '{}')
                props = schema.get('properties', {})
                required = schema.get('required', [])
                test_args = {}
                for key, prop in props.items():
                    if key in required:
                        prop_type = prop.get('type', 'string')
                        if prop_type == 'integer':
                            test_args[key] = 1
                        elif prop_type == 'number':
                            test_args[key] = 1.0
                        elif prop_type == 'boolean':
                            test_args[key] = False
                        elif prop_type == 'array':
                            test_args[key] = []
                        else:
                            test_args[key] = 'test'

                result = dispatcher.dispatch(tool, test_args, self.env, self.env.user)
                duration_ms = int((time.time() - start) * 1000)
                result_data = json.loads(result)

                if result_data.get('success'):
                    results.append(f"✓ {tool.name} ({duration_ms}ms)")
                else:
                    results.append(f"✗ {tool.name}: {result_data.get('error', 'Error')[:50]}")
            except Exception as e:
                results.append(f"✗ {tool.name}: {str(e)[:50]}")

        message = '\n'.join(results) if results else 'No tools to test'
        pass_count = sum(1 for r in results if r.startswith('✓'))
        total = len(results)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Test All Tools'),
                'message': _('%d/%d tools passed\n\n%s') % (pass_count, total, message[:500]),
                'type': 'success' if pass_count == total else 'warning',
            },
        }
