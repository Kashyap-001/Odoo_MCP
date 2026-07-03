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

        # Plain-text replies get wrapped in the structured-output envelope
        # ({"_type": "text", "content": ...}) — see gateway.py's final guard.
        reply_data = json.loads(result['reply'])
        self.assertEqual(reply_data['_type'], 'text')
        self.assertEqual(reply_data['content'], 'Hello! How can I help?')
        self.assertEqual(result['tool_calls'], 0)
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)

    @mock.patch('odoo.addons.mcp_gateway.mcp.providers.openai.OpenAIAdapter.call')
    def test_gateway_retries_on_fabricated_action_claim(self, mock_call):
        """A reply claiming a write happened ('Updated the...') with zero tool
        calls is fabricated — the gateway must force a retry even when the
        user's own wording doesn't match the action_words list (regression
        test for the 2026-07-03 'change email' hallucination bug)."""
        mock_call.side_effect = [
            {
                'text': "Updated the email of Mitchell Admin to newadmin@example.com.",
                'stop_reason': 'end_turn',
                'tool_calls': [],
                'input_tokens': 10,
                'output_tokens': 5,
            },
            {
                'text': "I was unable to make that change, please confirm the record.",
                'stop_reason': 'end_turn',
                'tool_calls': [],
                'input_tokens': 10,
                'output_tokens': 5,
            },
        ]

        gateway = McpGateway(self.env, self.env.user)
        gateway.run(
            agent_id=self.agent.id,
            user_message="Xqz Mitchell Admin's email to newadmin@example.com.",  # deliberately avoids every action_words entry
        )

        self.assertEqual(mock_call.call_count, 2, "fabricated success claim must trigger a retry")

    @mock.patch('odoo.addons.mcp_gateway.mcp.providers.openai.OpenAIAdapter.call')
    def test_gateway_hallucination_check_uses_latest_user_message(self, mock_call):
        """The hallucination check must scan for the LATEST user message, not
        the first one in `messages` — a synthetic "What is today's date?" pair
        is always inserted at index 1 of every turn's history (see the
        date-injection block in _call_provider_with_tools), so a forward scan
        always found that instead of the real triggering message, silently
        breaking has_action_word on every turn (regression test for the
        2026-07-03 bug where the warning log showed the date-check question
        instead of the user's actual avatar-setting request)."""
        mock_call.side_effect = [
            {
                # Honest reply, no fabrication verb, no 'success' — only
                # has_action_word (from the REAL user message) should trigger retry.
                'text': "I need more details before proceeding.",
                'stop_reason': 'end_turn',
                'tool_calls': [],
                'input_tokens': 10,
                'output_tokens': 5,
            },
            {
                'text': "Understood, let me look that up.",
                'stop_reason': 'end_turn',
                'tool_calls': [],
                'input_tokens': 10,
                'output_tokens': 5,
            },
        ]

        gateway = McpGateway(self.env, self.env.user)
        gateway.run(
            agent_id=self.agent.id,
            user_message="Please delete the record for Mitchell Admin.",
        )

        self.assertEqual(
            mock_call.call_count, 2,
            "action word in the LATEST user message must trigger a retry, "
            "even though a synthetic 'What is today's date?' user message "
            "was inserted earlier in history"
        )

    @mock.patch('odoo.addons.mcp_gateway.mcp.providers.openai.OpenAIAdapter.call')
    def test_gateway_retries_on_generic_success_claim(self, mock_call):
        """The generic 'success'/'successfully' catch-all must fire even when
        the verb itself isn't in fabrication_verbs (regression test for the
        2026-07-03 'note posted on chatter' hallucination — the model used
        'posted', which the first fix's verb list didn't cover either)."""
        mock_call.side_effect = [
            {
                'text': "Note filed on the record successfully.",  # 'filed' is not in fabrication_verbs
                'stop_reason': 'stop',
                'tool_calls': [],
                'input_tokens': 10,
                'output_tokens': 5,
            },
            {
                'text': "I was unable to file that note, please confirm the record.",
                'stop_reason': 'stop',
                'tool_calls': [],
                'input_tokens': 10,
                'output_tokens': 5,
            },
        ]

        gateway = McpGateway(self.env, self.env.user)
        gateway.run(
            agent_id=self.agent.id,
            user_message="Xqz a note on Mitchell Admin's chatter saying hello.",  # avoids action_words too
        )

        self.assertEqual(mock_call.call_count, 2, "generic 'success' claim must trigger a retry")

    def test_terminal_block_create_echart_returns_chart_type(self):
        """create_echart's terminal reply must carry the real options JSON from
        the DB record (not just a name/type/model summary) so the frontend can
        render a live chart instead of a bare confirmation card."""
        chart = self.env['mcp.echart'].create({
            'name': 'Test Chart',
            'options': '{"title": {"text": "Test"}, "series": [{"type": "bar"}]}',
        })

        gateway = McpGateway(self.env, self.env.user)
        block = gateway._build_terminal_block(
            'create_echart',
            {'result': {'id': chart.id, 'name': chart.name}},
        )

        self.assertEqual(block['_type'], 'chart')
        self.assertEqual(block['options'], chart.options)

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
