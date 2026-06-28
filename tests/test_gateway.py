"""
mcp_gateway/tests/test_gateway.py

Test suite for core gateway orchestration logic.

Test classes:
  TestGatewayRun — End-to-end agentic loop tests
  TestContextInjection — System prompt context features
  TestMemoryInjection — Session memory persistence

Dependencies:
  - unittest.mock — Mocking provider responses
  - TransactionCase — Odoo test harness
"""

import json
from unittest import mock
from odoo.tests import TransactionCase
from odoo.exceptions import AccessError, UserError
from ..mcp.gateway import McpGateway


class TestGatewayRun(TransactionCase):
    """Test core gateway.run() orchestration."""

    def setUp(self):
        super().setUp()
        # Create test agent
        self.agent = self.env['mcp.agent'].create({
            'name': 'Test Agent',
            'provider': 'openai',
            'api_key': 'sk-test-key',
            'model_name': 'gpt-4',
            'system_prompt': 'You are a helpful assistant.',
        })

    @mock.patch('odoo.addons.mcp_gateway.mcp.providers.openai.OpenAIAdapter.call')
    def test_gateway_run_basic(self, mock_call):
        """Test basic gateway run with mocked provider."""
        mock_call.return_value = {
            'text': 'Hello! How can I help?',
            'stop_reason': 'end_turn',
            'tool_calls': [],
            'input_tokens': 10,
            'output_tokens': 5,
        }

        gateway = McpGateway(self.env, self.env.user)

        result = gateway.run(
            agent_id=self.agent.id,
            user_message='Hi there',
        )

        self.assertEqual(result['reply'], 'Hello! How can I help?')
        self.assertEqual(result['tool_calls'], 0)
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)

    def _create_test_user(self, name, login, groups=None):
        self.env.cr.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='res_partner' AND column_name='autopost_bills'
        """)
        if self.env.cr.fetchone():
            self.env.cr.execute("""
                INSERT INTO res_partner (name, active, autopost_bills, create_date, write_date, create_uid, write_uid) 
                VALUES (%s, True, 'never', now(), now(), %s, %s) RETURNING id
            """, (name, self.env.uid, self.env.uid))
            partner_id = self.env.cr.fetchone()[0]
            partner = self.env['res.partner'].browse(partner_id)
        else:
            partner_vals = {
                'name': name,
            }
            partner = self.env['res.partner'].create(partner_vals)
        user_vals = {
            'login': login,
            'partner_id': partner.id,
        }
        if groups:
            user_vals['groups_id'] = [(4, g) for g in groups]
        return self.env['res.users'].create(user_vals)

    def test_gateway_access_denied(self):
        """Test gateway denies access for unauthorized users."""
        # Create restricted agent
        restricted_agent = self.env['mcp.agent'].create({
            'name': 'Restricted Agent',
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4',
            'description': 'Restricted Agent Description',
        })

        # Create a non-admin user
        non_admin_group = self.env['res.groups'].create({'name': 'Test Non-Admin Group'})
        user = self._create_test_user('Test User', 'test_user', [non_admin_group.id])

        gateway = McpGateway(self.env, user)

        with self.assertRaises(AccessError):
            gateway.run(agent_id=restricted_agent.id, user_message='Test')

    @mock.patch('odoo.addons.mcp_gateway.mcp.providers.openai.OpenAIAdapter.call')
    def test_gateway_rate_limit(self, mock_call):
        """Test rate limiting enforcement."""
        # Create a non-admin user and group
        user_group = self.env['res.groups'].create({'name': 'Rate Limit Group'})
        user = self._create_test_user('Rate Limit User', 'rate_limit_user', [user_group.id])

        # Set rate limit to 1 call/day for this group and grant access to the agent
        self.env['mcp.access.rule'].create({
            'name': 'Limited Rule',
            'group_ids': [(4, user_group.id)],
            'agent_ids': [(4, self.agent.id)],
            'rate_limit_day': 1,
        })

        gateway = McpGateway(self.env, user)

        mock_call.return_value = {
            'text': 'Result 1',
            'stop_reason': 'end_turn',
            'tool_calls': [],
            'input_tokens': 10,
            'output_tokens': 5,
        }

        # First call should succeed
        result1 = gateway.run(agent_id=self.agent.id, user_message='Call 1')
        self.assertIsNotNone(result1['session_id'])

        # Second call should fail (rate limited)
        with self.assertRaises(UserError):
            gateway.run(agent_id=self.agent.id, user_message='Call 2')


class TestContextInjection(TransactionCase):
    """Test context injection into system prompt."""

    def setUp(self):
        super().setUp()
        self.agent = self.env['mcp.agent'].create({
            'name': 'Context Agent',
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4',
            'context_fields': json.dumps(['name', 'email']),
        })

    @mock.patch('odoo.addons.mcp_gateway.mcp.providers.openai.OpenAIAdapter.call')
    def test_context_injection(self, mock_call):
        """Test active record context is injected."""
        # Use existing partner to avoid NotNullViolation constraints on partner creation
        partner = self.env.user.partner_id
        partner.write({
            'name': 'Test Partner',
            'email': 'test@example.com',
        })

        gateway = McpGateway(self.env, self.env.user)

        mock_call.return_value = {
            'text': 'Context received',
            'stop_reason': 'end_turn',
            'tool_calls': [],
            'input_tokens': 50,
            'output_tokens': 10,
        }

        result = gateway.run(
            agent_id=self.agent.id,
            user_message='Who is this?',
            active_model='res.partner',
            active_id=partner.id,
        )

        # Verify context was passed to provider
        call_args = mock_call.call_args
        messages = call_args[0][1]  # Second arg is messages

        # System message should contain context
        system_msg = [m for m in messages if m.get('role') == 'system'][0]
        self.assertIn('ACTIVE RECORD CONTEXT', system_msg['content'])
        self.assertIn(partner.name, system_msg['content'])


class TestMemoryInjection(TransactionCase):
    """Test session memory injection."""

    def setUp(self):
        super().setUp()
        self.agent = self.env['mcp.agent'].create({
            'name': 'Memory Agent',
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4',
            'enable_memory': True,
        })

    def test_memory_persistence(self):
        """Test memories are created and stored."""
        session = self.env['mcp.session'].create({
            'agent_id': self.agent.id,
            'user_id': self.env.user.id,
        })

        memory = self.env['mcp.agent.memory'].create({
            'agent_id': self.agent.id,
            'user_id': self.env.user.id,
            'session_id': session.id,
            'summary': 'User asked about sales process',
        })

        self.assertTrue(memory.exists())
        self.assertEqual(memory.summary, 'User asked about sales process')
