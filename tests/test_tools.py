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

import base64
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
            'description': 'Test tool description',
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
                'display_name_label': 'Invalid Tool Name',
                'description': 'Test description',
                'category_id': self.category.id,
                'tool_type': 'odoo',
            })

    def test_tool_schema_validation(self):
        """Test input schema must be valid JSON."""
        with self.assertRaises(ValidationError):
            self.env['mcp.tool'].create({
                'name': 'bad_schema_tool',
                'display_name_label': 'Bad Schema Tool',
                'description': 'Test description',
                'category_id': self.category.id,
                'tool_type': 'odoo',
                'input_schema': '{invalid json}',  # Invalid JSON
            })

    def test_tool_spec_generation(self):
        """Test tool spec generation for provider."""
        tool = self.env['mcp.tool'].create({
            'name': 'partner_search_test',
            'display_name_label': 'Search Partners Test',
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

        self.assertEqual(spec['name'], 'partner_search_test')
        self.assertEqual(spec['description'], 'Search for business partners')
        self.assertIn('input_schema', spec)


class TestToolDispatch(TransactionCase):
    """Test tool execution dispatch."""

    def setUp(self):
        super().setUp()
        self.category = self.env['mcp.tool.category'].create({'name': 'Test'})

    def test_dispatch_odoo_tool(self):
        """Test dispatching Odoo ORM tool."""
        # Using search_read (one of the 18 generic tools)
        tool = self.env['mcp.tool'].search([('name', '=', 'search_read')], limit=1)
        if not tool:
            tool = self.env['mcp.tool'].create({
                'name': 'search_read',
                'display_name_label': 'Search Records',
                'description': 'Query Odoo database records',
                'category_id': self.category.id,
                'tool_type': 'odoo',
                'odoo_model': 'ir.model',
                'odoo_method': 'search_read',
            })

        from ..mcp.tools.dispatcher import ToolDispatcher
        dispatcher = ToolDispatcher()

        result = dispatcher.dispatch(
            tool,
            {'model': 'res.partner', 'fields': ['id', 'name'], 'limit': 5},
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
            'display_name_label': 'External API Call',
            'description': 'External API Call description',
            'category_id': self.category.id,
            'tool_type': 'external',
            'endpoint_url': 'https://api.example.com/endpoint',
            'auth_type': 'none',
        })

        from ..mcp.tools.dispatcher import ToolDispatcher
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
            'display_name_label': 'Error Tool',
            'description': 'Error Tool description',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'nonexistent.model',  # Invalid model
            'odoo_method': 'search_read',
        })

        from ..mcp.tools.dispatcher import ToolDispatcher
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

    def test_dispatch_read_attachment_serializes_binary_data(self):
        """ir.attachment.datas comes back from search_read as base64-encoded
        bytes, not str — json.dumps can't serialize bytes. Regression test for
        the 2026-07-03 'Object of type bytes is not JSON serializable' crash
        on any read_attachment call for a small enough attachment."""
        attachment = self.env['ir.attachment'].create({
            'name': 'test.png',
            'datas': base64.b64encode(b'fake-png-bytes'),
            'mimetype': 'image/png',
        })

        tool = self.env.ref('mcp_gateway.tool_read_attachment')

        from ..mcp.tools.dispatcher import ToolDispatcher
        dispatcher = ToolDispatcher()

        result = dispatcher.dispatch(
            tool,
            {'attachment_id': attachment.id},
            self.env,
            self.env.user,
        )

        result_data = json.loads(result)
        self.assertTrue(result_data['success'], result_data.get('error'))
        self.assertTrue(result_data['result']['data_included'])
        self.assertIsInstance(result_data['result']['data_base64'], str)


class TestToolDescriptions(TransactionCase):
    """Test description standard for most-used tools."""

    def test_generic_tools_description(self):
        """Test new generic tools are registered and have description."""
        tool = self.env['mcp.tool'].search([('name', '=', 'search_read')], limit=1)
        if not tool:
            self.skipTest("Tool not installed")

        desc = tool.description
        self.assertIsNotNone(desc)
        self.assertIn('Find, list, filter, or count records', desc)


class TestToolDispatcherDatetime(TransactionCase):
    """Test datetime handling in dispatcher."""

    def setUp(self):
        super().setUp()
        from ..mcp.tools.dispatcher import ToolDispatcher
        self.dispatcher = ToolDispatcher()
        self.category = self.env['mcp.tool.category'].create({'name': 'Test'})

    @mock.patch('odoo.models.BaseModel.search_read')
    def test_calendar_event_datetime_parsing(self, mock_search_read):
        """Test search_read parses datetime strings correctly.

        Uses mail.message (from the 'mail' dependency, always installed) rather
        than calendar.event — calendar isn't in this module's depends, so the
        model doesn't exist at all in a minimal test DB, regardless of mocking
        search_read. Same class of fix as the 2026-07-02 sale_order_count test
        gotcha: only rely on models this module's own dependency graph guarantees.
        """
        mock_search_read.return_value = [{'id': 1, 'subject': 'Test Message'}]

        tool = self.env['mcp.tool'].create({
            'name': 'calendar_test',
            'display_name_label': 'Test',
            'description': 'Calendar test description',
            'category_id': self.category.id,
            'tool_type': 'odoo',
            'odoo_model': 'mail.message',
            'odoo_method': 'search_read',
        })

        result = self.dispatcher.dispatch(
            tool,
            {'model': 'mail.message', 'domain': [['date', '>=', '2025-01-15 00:00:00']]},
            self.env,
            self.env.user,
        )

        result_data = json.loads(result)
        self.assertTrue(result_data['success'])

    def test_datetime_formatting_utility(self):
        """Test datetime formats are correctly converted."""
        # Test helper date/datetime parsers
        parsed_dt = self.dispatcher._parse_datetime('2025-01-15T09:00:00Z')
        parsed_d = self.dispatcher._parse_date('2025-01-15')

        self.assertIsInstance(parsed_dt, datetime)
        self.assertIsInstance(parsed_d, date)


class TestToolDispatcherErrors(TransactionCase):
    """Test error handling returns dict, not exceptions."""

    def setUp(self):
        super().setUp()
        from ..mcp.tools.dispatcher import ToolDispatcher
        self.dispatcher = ToolDispatcher()
        self.category = self.env['mcp.tool.category'].create({'name': 'Test'})

    def test_odoo_invalid_model_returns_error_dict(self):
        """Test invalid model returns error dict, not exception."""
        tool = self.env['mcp.tool'].create({
            'name': 'test_invalid',
            'display_name_label': 'Test',
            'description': 'Test invalid description',
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
            'description': 'Test description',
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

    def test_search_read_rejects_non_stored_field_domain(self):
        """Non-stored computed field in domain must fail loud, not silently drop the filter."""
        # _dispatch_odoo routes by tool.name matching literal built-in names
        # (e.g. 'search_read') — NOT by tool.odoo_method — so this must reuse
        # the real seeded tool_search_read record rather than create a second
        # tool named 'search_read' (which would both violate the unique
        # constraint against the seeded data AND, if renamed to dodge that,
        # silently stop routing through _dispatch_search_read at all).
        tool = self.env.ref('mcp_gateway.tool_search_read')
        # Use a field defined by mcp_gateway itself (mcp.echart.public_url,
        # compute='_compute_public_url', store=False) rather than one from an
        # unrelated module like `sale` — this test runs during mcp_gateway's
        # OWN module-load step under Odoo's real test runner, before modules
        # that load later in the dependency graph are necessarily registered,
        # so a field from an external module can resolve to None here even
        # when genuinely installed (order-dependent flake, not a real gap).
        arguments = {'model': 'mcp.echart', 'domain': [['public_url', '=', 'x']], 'fields': ['name']}

        result = self.dispatcher.dispatch(tool, arguments, self.env, self.env.user)
        result_data = json.loads(result)

        self.assertFalse(result_data['success'])
        self.assertIn('non-stored', result_data['error'].lower())

    @mock.patch('requests.post')
    def test_external_tool_connection_error(self, mock_post):
        """Test external tool connection error returns error dict."""
        mock_post.side_effect = Exception("Connection refused")

        tool = self.env['mcp.tool'].create({
            'name': 'test_external',
            'display_name_label': 'Test',
            'description': 'Test description',
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
            'description': 'Test description',
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
