"""
mcp_gateway/tests/test_tools.py

Test suite for tool registration and dispatch.

Test classes:
  TestToolRegistry — Tool CRUD and schema validation
  TestToolDispatch — Tool execution routing
  TestToolDescriptions — F1 description standard validation
  TestToolDispatcherDatetime — F2 datetime handling
  TestToolDispatcherErrors — Error handling validation

Dependencies:
  - unittest.mock — Mocking external APIs
"""

import json
from datetime import datetime, date
from unittest import mock
from odoo.tests import TransactionCase
from odoo.exceptions import ValidationError


class TestToolRegistry(TransactionCase):
    """Test tool registration and management."""

    def setUp(self):
        super().setUp()
        self.category = self.env['mcp.tool.category'].create({
            'name': 'Test Category',
        })

    def test_tool_creation_basic(self):
        """Test creating a tool."""
        tool = self.env['mcp.tool'].create({
            'name': 'test_tool',
            'display_name_label': 'Test Tool',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'res.partner',
            'odoo_method': 'search_read',
            'input_schema': '{}',
        })

        self.assertTrue(tool.exists())
        self.assertEqual(tool.name, 'test_tool')

    def test_tool_name_validation(self):
        """Test tool name must be snake_case."""
        with self.assertRaises(ValidationError):
            self.env['mcp.tool'].create({
                'name': 'InvalidToolName',  # Should be invalid_tool_name
                'category_id': self.category.id,
                'tool_type': 'odoo',
            })

    def test_tool_schema_validation(self):
        """Test input schema must be valid JSON."""
        with self.assertRaises(ValidationError):
            self.env['mcp.tool'].create({
                'name': 'bad_schema_tool',
                'category_id': self.category.id,
                'tool_type': 'odoo',
                'input_schema': '{invalid json}',  # Invalid JSON
            })

    def test_tool_spec_generation(self):
        """Test tool spec generation for provider."""
        tool = self.env['mcp.tool'].create({
            'name': 'partner_search',
            'display_name_label': 'Search Partners',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'description': 'Search for business partners',
            'input_schema': json.dumps({
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                },
            }),
        })

        spec = tool.get_tool_spec()

        self.assertEqual(spec['name'], 'partner_search')
        self.assertEqual(spec['description'], 'Search for business partners')
        self.assertIn('input_schema', spec)


class TestToolDispatch(TransactionCase):
    """Test tool execution dispatch."""

    def setUp(self):
        super().setUp()
        self.category = self.env['mcp.tool.category'].create({'name': 'Test'})

    def test_dispatch_odoo_tool(self):
        """Test dispatching Odoo ORM tool."""
        tool = self.env['mcp.tool'].create({
            'name': 'partner_search',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'res.partner',
            'odoo_method': 'search_read',
            'odoo_fields': ['id', 'name', 'email'],
        })

        from mcp.tools.dispatcher import ToolDispatcher
        dispatcher = ToolDispatcher()

        result = dispatcher.dispatch(
            tool,
            {'fields': ['id', 'name'], 'limit': 5},
            self.env,
            self.env.user,
        )

        # Result should be JSON string
        self.assertIsInstance(result, str)
        result_data = json.loads(result)
        self.assertIn('success', result_data)

    @mock.patch('requests.get')
    def test_dispatch_external_tool(self, mock_get):
        """Test dispatching external HTTP tool."""
        mock_get.return_value.json.return_value = {'data': 'success'}
        mock_get.return_value.status_code = 200

        tool = self.env['mcp.tool'].create({
            'name': 'external_api_call',
            'category_id': self.category.id,
            'tool_type': 'external',
            'external_url': 'https://api.example.com/endpoint',
            'external_auth_type': 'none',
        })

        from mcp.tools.dispatcher import ToolDispatcher
        dispatcher = ToolDispatcher()

        result = dispatcher.dispatch(
            tool,
            {'param': 'value'},
            self.env,
            self.env.user,
        )

        result_data = json.loads(result)
        self.assertEqual(result_data['success'], True)

    def test_tool_error_handling(self):
        """Test tool execution error handling."""
        tool = self.env['mcp.tool'].create({
            'name': 'error_tool',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'nonexistent.model',  # Invalid model
            'odoo_method': 'search_read',
        })

        from mcp.tools.dispatcher import ToolDispatcher
        dispatcher = ToolDispatcher()

        result = dispatcher.dispatch(
            tool,
            {},
            self.env,
            self.env.user,
        )

        # Should not raise, returns error JSON
        result_data = json.loads(result)
        self.assertEqual(result_data['success'], False)
        self.assertIn('error', result_data)


class TestToolDescriptions(TransactionCase):
    """Test F1 description standard for most-used tools."""

    def test_partner_search_description_f1(self):
        """Test partner_search follows F1: has category, trigger, Do NOT, Returns."""
        tool = self.env['mcp.tool'].search([('name', '=', 'partner_search')], limit=1)
        if not tool:
            self.skipTest("Tool not installed")

        desc = tool.description
        self.assertIn('[SALES', desc)
        self.assertIn('Use this tool when the user wants to', desc)
        self.assertIn('Do NOT use', desc)
        self.assertIn('Returns:', desc)

    def test_partner_create_description_f1(self):
        """Test partner_create follows F1 format."""
        tool = self.env['mcp.tool'].search([('name', '=', 'partner_create')], limit=1)
        if not tool:
            self.skipTest("Tool not installed")

        desc = tool.description
        self.assertIn('[SALES', desc)
        self.assertIn('Use this tool when the user wants to', desc)
        self.assertIn('Do NOT use', desc)
        self.assertIn('Returns:', desc)

    def test_sale_order_search_description_f1(self):
        """Test sale_order_search follows F1 format."""
        tool = self.env['mcp.tool'].search([('name', '=', 'sale_order_search')], limit=1)
        if not tool:
            self.skipTest("Tool not installed")

        desc = tool.description
        self.assertIn('[SALES', desc)
        self.assertIn('Use this tool when the user wants to', desc)
        self.assertIn('Do NOT use', desc)

    def test_invoice_search_description_f1(self):
        """Test invoice_search follows F1 format."""
        tool = self.env['mcp.tool'].search([('name', '=', 'invoice_search')], limit=1)
        if not tool:
            self.skipTest("Tool not installed")

        desc = tool.description
        self.assertIn('[FINANCE]', desc)
        self.assertIn('Use this tool when the user wants to', desc)
        self.assertIn('Do NOT use', desc)

    def test_calendar_event_create_description_f1(self):
        """Test calendar_event_create follows F1 format."""
        tool = self.env['mcp.tool'].search([('name', '=', 'calendar_event_create')], limit=1)
        if not tool:
            self.skipTest("Tool not installed")

        desc = tool.description
        self.assertIn('[HR', desc)
        self.assertIn('Use this tool when the user wants to', desc)
        self.assertIn('Do NOT use', desc)


class TestToolDispatcherDatetime(TransactionCase):
    """Test F2 datetime handling in dispatcher."""

    def setUp(self):
        super().setUp()
        self.dispatcher = self.env['mcp.tool'].env['mcp.tools.dispatcher'].ToolDispatcher()
        self.category = self.env['mcp.tool.category'].create({'name': 'Test'})

    @mock.patch('odoo.models.BaseModel.search_read')
    def test_calendar_event_datetime_parsing(self, mock_search_read):
        """Test calendar_event_create parses datetime strings correctly."""
        mock_search_read.return_value = [{'id': 1, 'name': 'Test Event'}]

        tool = self.env['mcp.tool'].create({
            'name': 'calendar_test',
            'display_name_label': 'Test',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'calendar.event',
            'odoo_method': 'search_read',
        })

        result = self.dispatcher.dispatch(
            tool,
            {'start': '2025-06-15 14:00:00', 'stop': '2025-06-15 15:00:00'},
            self.env,
            self.env.user,
        )

        result_data = json.loads(result)
        self.assertTrue(result_data.get('success'))

    def test_prepare_create_values_datetime(self):
        """Test _prepare_create_values parses datetime strings."""
        tool = self.env['mcp.tool'].create({
            'name': 'test_datetime',
            'display_name_label': 'Test',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'calendar.event',
            'odoo_method': 'create',
        })

        result = self.dispatcher._prepare_create_values({
            'name': 'Test Event',
            'start': '2025-06-15 14:00:00',
            'stop': '2025-06-15 15:00:00',
            'partner_ids': [1, 2, 3],
        }, 'calendar.event')

        self.assertIsInstance(result['start'], datetime)
        self.assertIsInstance(result['stop'], datetime)
        self.assertIsInstance(result['partner_ids'], tuple)  # Command.set() returns tuple


class TestToolDispatcherErrors(TransactionCase):
    """Test error handling returns dict, not exceptions."""

    def setUp(self):
        super().setUp()
        self.dispatcher = self.env['mcp.tool'].env['mcp.tools.dispatcher'].ToolDispatcher()
        self.category = self.env['mcp.tool.category'].create({'name': 'Test'})

    def test_odoo_invalid_model_returns_error_dict(self):
        """Test invalid model returns error dict, not exception."""
        tool = self.env['mcp.tool'].create({
            'name': 'test_invalid',
            'display_name_label': 'Test',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'nonexistent.model.that.does.not.exist',
            'odoo_method': 'search_read',
        })

        result = self.dispatcher.dispatch(tool, {}, self.env, self.env.user)
        result_data = json.loads(result)

        self.assertIn('success', result_data)
        self.assertFalse(result_data['success'])
        self.assertIn('error', result_data)

    def test_odoo_invalid_method_returns_error_dict(self):
        """Test invalid method returns error dict."""
        tool = self.env['mcp.tool'].create({
            'name': 'test_bad_method',
            'display_name_label': 'Test',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'res.partner',
            'odoo_method': 'nonexistent_method',
        })

        result = self.dispatcher.dispatch(tool, {}, self.env, self.env.user)
        result_data = json.loads(result)

        self.assertIn('success', result_data)
        self.assertFalse(result_data['success'])
        self.assertIn('error', result_data)

    @mock.patch('requests.post')
    def test_external_tool_connection_error(self, mock_post):
        """Test external tool connection error returns error dict."""
        mock_post.side_effect = Exception("Connection refused")

        tool = self.env['mcp.tool'].create({
            'name': 'test_external',
            'display_name_label': 'Test',
            'category_id': self.category.id,
            'tool_type': 'external',
            'endpoint_url': 'https://bad.url.fake/api',
            'http_method': 'POST',
        })

        result = self.dispatcher.dispatch(tool, {}, self.env, self.env.user)
        result_data = json.loads(result)

        self.assertIn('success', result_data)
        self.assertFalse(result_data['success'])
        self.assertIn('error', result_data)

    @mock.patch('requests.post')
    def test_mcp_server_connection_error(self, mock_post):
        """Test MCP server error returns error dict."""
        mock_post.side_effect = Exception("Server unavailable")

        tool = self.env['mcp.tool'].create({
            'name': 'test_mcp',
            'display_name_label': 'Test',
            'category_id': self.category.id,
            'tool_type': 'mcp_server',
            'mcp_server_url': 'http://localhost:9999',
            'mcp_server_key': 'test-key',
        })

        result = self.dispatcher.dispatch(tool, {'arg': 'value'}, self.env, self.env.user)
        result_data = json.loads(result)

        self.assertIn('success', result_data)
        self.assertFalse(result_data['success'])
        self.assertIn('error', result_data)
