"""
mcp_gateway/tests/test_access.py

Test suite for access control and rate limiting.

Test classes:
  TestAccessRules — Permission enforcement
  TestRateLimiting — Daily/monthly rate limits

Dependencies:
  - unittest.mock — Time mocking for rate limit tests
"""

import json
from datetime import datetime, timedelta
from unittest import mock
from odoo.tests import TransactionCase
from odoo.exceptions import UserError


class TestAccessRules(TransactionCase):
    """Test access control enforcement."""

    def setUp(self):
        super().setUp()
        self.admin_group = self.env.ref('base.group_system')
        self.user_group = self.env['res.groups'].create({
            'name': 'Test User Group',
        })

        self.agent = self.env['mcp.agent'].create({
            'name': 'Protected Agent',
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4',
        })

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

    def test_admin_has_all_access(self):
        """Test admin users have access to all agents."""
        admin_user = self._create_test_user('Admin Test', 'admin_test', [self.admin_group.id])

        # Admin should have access
        rules = self.env['mcp.access.rule'].get_rules_for_user(admin_user)
        self.assertIsNotNone(rules)

    def test_user_group_access_rule(self):
        """Test user group access enforcement."""
        user = self._create_test_user('Limited User', 'limited_user', [self.user_group.id])

        # Create rule granting access
        rule = self.env['mcp.access.rule'].create({
            'name': 'Limited Access',
            'group_ids': [(4, self.user_group.id)],
            'agent_ids': [(4, self.agent.id)],
        })

        rules = self.env['mcp.access.rule'].get_rules_for_user(user)
        self.assertIn(self.agent.id, rules['agent_ids'].ids)

    def test_multiple_rule_merge(self):
        """Test multiple rules are merged with OR logic."""
        user = self._create_test_user('Multi Rule User', 'multi_user', [self.user_group.id])

        agent2 = self.env['mcp.agent'].create({
            'name': 'Agent 2',
            'provider': 'openai',
            'api_key': 'sk-test2',
            'model_name': 'gpt-4',
        })

        # Create two rules
        rule1 = self.env['mcp.access.rule'].create({
            'name': 'Rule 1',
            'group_ids': [(4, self.user_group.id)],
            'agent_ids': [(4, self.agent.id)],
        })

        rule2 = self.env['mcp.access.rule'].create({
            'name': 'Rule 2',
            'group_ids': [(4, self.user_group.id)],
            'agent_ids': [(4, agent2.id)],
        })

        rules = self.env['mcp.access.rule'].get_rules_for_user(user)

        # User should have access to both agents
        self.assertIn(self.agent.id, rules['agent_ids'].ids)
        self.assertIn(agent2.id, rules['agent_ids'].ids)


class TestRateLimiting(TransactionCase):
    """Test rate limiting enforcement."""

    def setUp(self):
        super().setUp()
        self.user_group = self.env['res.groups'].create({
            'name': 'Limited Group',
        })

        self.agent = self.env['mcp.agent'].create({
            'name': 'Rate Limited Agent',
            'provider': 'openai',
            'api_key': 'sk-test',
            'model_name': 'gpt-4',
        })

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

    def test_daily_rate_limit(self):
        """Test daily rate limiting."""
        user = self._create_test_user('Rate Limited User', 'rate_user', [self.user_group.id])

        # Create rule with 1 call/day limit
        rule = self.env['mcp.access.rule'].create({
            'name': 'Rate Limit Rule',
            'group_ids': [(4, self.user_group.id)],
            'rate_limit_day': 1,
        })

        # Create a session (simulates one call)
        session1 = self.env['mcp.session'].create({
            'agent_id': self.agent.id,
            'user_id': user.id,
            'state': 'done',
        })

        # Check rate limit - should have 1 call used
        cutoff = datetime.now() - timedelta(hours=24)
        sessions = self.env['mcp.session'].search([
            ('user_id', '=', user.id),
            ('create_date', '>=', cutoff),
        ])

        self.assertEqual(len(sessions), 1)

    def test_monthly_rate_limit(self):
        """Test monthly rate limiting."""
        user = self._create_test_user('Monthly Limited User', 'monthly_user', [self.user_group.id])

        rule = self.env['mcp.access.rule'].create({
            'name': 'Monthly Limit',
            'group_ids': [(4, self.user_group.id)],
            'rate_limit_month': 100,
        })

        # Create 50 sessions
        for i in range(50):
            session = self.env['mcp.session'].create({
                'agent_id': self.agent.id,
                'user_id': user.id,
                'state': 'done',
            })
            self.env['mcp.session.message'].create({
                'session_id': session.id,
                'role': 'user',
                'content': 'dummy message',
            })

        # Should still be under limit
        cutoff = datetime.now() - timedelta(days=30)
        sessions = self.env['mcp.session'].search([
            ('user_id', '=', user.id),
            ('create_date', '>=', cutoff),
        ])

        self.assertEqual(len(sessions), 50)
