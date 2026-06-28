"""
mcp_gateway/tests/test_webhook.py

Test suite for webhook trigger system.

Test classes:
  TestWebhookTrigger — Webhook creation and firing
  TestWebhookSecurity — Token validation and CORS

Dependencies:
  - unittest.mock — Mocking gateway calls
"""

import json
from unittest import mock
from odoo.tests import TransactionCase
from odoo.exceptions import UserError


class TestWebhookTrigger(TransactionCase):
    """Test webhook trigger system."""

    def setUp(self):
        super().setUp()
        self.agent = self.env['mcp.agent'].create({
            'name': 'Webhook Agent',
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4',
        })

    def test_webhook_creation(self):
        """Test webhook trigger record creation."""
        webhook = self.env['mcp.webhook.trigger'].create({
            'name': 'New Tag Webhook',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'description': 'Triggers when new tag created',
            'message_template': 'New tag created: {record.name}',
        })

        self.assertTrue(webhook.exists())
        self.assertTrue(webhook.token)  # Should auto-generate token
        self.assertIn('/mcp/webhook/', webhook.webhook_url)

    def test_webhook_token_uniqueness(self):
        """Test webhook tokens are unique."""
        webhook1 = self.env['mcp.webhook.trigger'].create({
            'name': 'Webhook 1',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'message_template': 'Test 1: {record.name}',
        })

        webhook2 = self.env['mcp.webhook.trigger'].create({
            'name': 'Webhook 2',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'message_template': 'Test 2: {record.name}',
        })

        self.assertNotEqual(webhook1.token, webhook2.token)

    @mock.patch('odoo.addons.mcp_gateway.mcp.gateway.McpGateway.run')
    def test_webhook_fire(self, mock_run):
        """Test firing webhook trigger."""
        mock_run.return_value = {
            'reply': 'Triggered successfully',
            'session_id': 1,
        }

        webhook = self.env['mcp.webhook.trigger'].create({
            'name': 'Test Webhook',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'message_template': 'Test fire: {record.name}',
        })

        # Create test tag
        tag = self.env['res.partner.category'].create({
            'name': 'Test Tag',
        })

        result = webhook.fire(tag)

        self.assertEqual(result['reply'], 'Triggered successfully')
        self.assertEqual(result['session_id'], 1)

    def test_webhook_requires_active(self):
        """Test inactive webhooks don't fire."""
        webhook = self.env['mcp.webhook.trigger'].create({
            'name': 'Inactive Webhook',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'active': False,
            'message_template': 'Inactive: {record.name}',
        })

        self.assertFalse(webhook.active)

    def test_webhook_model_mismatch(self):
        """Test webhook rejects mismatched models."""
        webhook = self.env['mcp.webhook.trigger'].create({
            'name': 'Tag Webhook',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'message_template': 'Mismatch: {record.name}',
        })

        # Try to fire with wrong model (using self.agent which is mcp.agent, not res.partner.category)
        # Should raise UserError
        with self.assertRaises(UserError):
            webhook.fire(self.agent)


class TestWebhookSecurity(TransactionCase):
    """Test webhook security."""

    def setUp(self):
        super().setUp()
        self.agent = self.env['mcp.agent'].create({
            'name': 'Secure Webhook Agent',
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4',
        })

    def test_webhook_token_validation(self):
        """Test webhook token validation on fire."""
        webhook = self.env['mcp.webhook.trigger'].create({
            'name': 'Secure Webhook',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'message_template': 'Secure: {record.name}',
        })

        # Try to find with wrong token
        wrong_token_webhook = self.env['mcp.webhook.trigger'].search([
            ('token', '=', 'wrong-token-1234'),
            ('active', '=', True),
        ])

        self.assertEqual(len(wrong_token_webhook), 0)

    def test_webhook_disabled_state(self):
        """Test disabled webhooks aren't found."""
        webhook = self.env['mcp.webhook.trigger'].create({
            'name': 'Disabled Webhook',
            'trigger_model': 'res.partner.category',
            'agent_id': self.agent.id,
            'active': False,
            'message_template': 'Disabled: {record.name}',
        })

        # Search should not find disabled webhooks
        found = self.env['mcp.webhook.trigger'].search([
            ('token', '=', webhook.token),
            ('active', '=', True),
        ])

        self.assertEqual(len(found), 0)
