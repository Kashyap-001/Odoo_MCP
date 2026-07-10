"""
mcp_gateway/models/mcp_access_rule.py

Access control model for managing user/group permissions to agents and tools.

Key classes:
  AccessRule — Define which users/groups can access which agents/tools

Dependencies:
  - res.groups — Odoo user groups
  - res.users — Odoo users
  - mcp.agent — agents to grant access to
  - mcp.tool — tools to grant access to

Developer notes:
  - Multiple rules are merged via OR logic (any matching rule grants access)
  - get_rules_for_user() is called during each gateway.run() to enforce permissions
  - Rate limiting is checked before every tool call
"""

import logging
from odoo import fields, models, api, _

_logger = logging.getLogger(__name__)


class AccessRule(models.Model):
    """
    Access Rule (mcp.access.rule)

    Define which users/groups can access which agents and tools.
    Supports rate limiting and granular permission control.

    Relationships:
      - HasMany: res.groups via many2many
      - HasMany: res.users via many2many
      - HasMany: mcp.agent via many2many
      - HasMany: mcp.tool via many2many

    Business rules:
      - Name must be unique
      - Multiple matching rules are merged (OR logic)
      - Rate limits enforced as 24-hour rolling windows
    """

    _name = 'mcp.access.rule'
    _description = _('MCP Access Rule')
    _order = 'sequence, name'

    sequence = fields.Integer(
        string=_('Sequence'),
        default=10,
        help=_('Order of evaluation'),
    )
    name = fields.Char(
        string=_('Rule Name'),
        required=True,
        translate=True,
        help=_('e.g., "Sales Team Access", "Finance Manager Permissions"'),
    )
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        help=_('Inactive rules are not evaluated'),
    )

    # ── Who ─────────────────────────────────────────────────────────
    group_ids = fields.Many2many(
        comodel_name='res.groups',
        relation='mcp_access_rule_group_rel',
        column1='rule_id',
        column2='group_id',
        string=_('Groups'),
        help=_('Users in these groups are granted access'),
    )
    user_ids = fields.Many2many(
        comodel_name='res.users',
        relation='mcp_access_rule_user_rel',
        column1='rule_id',
        column2='user_id',
        string=_('Users'),
        help=_('These specific users are granted access'),
    )

    # ── What ────────────────────────────────────────────────────────
    agent_ids = fields.Many2many(
        comodel_name='mcp.agent',
        relation='mcp_access_rule_agent_rel',
        column1='rule_id',
        column2='agent_id',
        string=_('Agents'),
        help=_('Agents this rule grants access to (empty = all)'),
    )
    tool_ids = fields.Many2many(
        comodel_name='mcp.tool',
        relation='mcp_access_rule_tool_rel',
        column1='rule_id',
        column2='tool_id',
        string=_('Tools'),
        help=_('Tools this rule grants access to (empty = all)'),
    )

    # ── Permissions ─────────────────────────────────────────────────
    can_view_sessions = fields.Boolean(
        string=_('Can View Sessions'),
        default=True,
        help=_('User can view chat history and session details'),
    )
    can_export = fields.Boolean(
        string=_('Can Export Transcripts'),
        default=False,
        help=_('User can download session transcripts as .txt'),
    )

    # ── Rate Limiting ───────────────────────────────────────────────
    rate_limit_day = fields.Integer(
        string=_('Daily Rate Limit'),
        default=0,
        help=_('Max API calls per 24 hours (0 = unlimited)'),
    )
    rate_limit_month = fields.Integer(
        string=_('Monthly Rate Limit'),
        default=0,
        help=_('Max API calls per calendar month (0 = unlimited)'),
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Rule name must be unique.')),
    ]

    @api.model
    def get_rules_for_user(self, user) -> dict:
        """
        Get effective permissions for a user (merge all matching rules).

        Called during each gateway.run() to determine:
          - Which agents user can access
          - Which tools user can call
          - Rate limits for this session
          - Export/view permissions

        Args:
            user (res.users): User to check

        Returns:
            dict: {
              'agent_ids': recordset of allowed agents,
              'tool_ids': recordset of allowed tools,
              'can_view_sessions': bool,
              'can_export': bool,
              'rate_limit_day': int,
              'rate_limit_month': int,
            }

        Developer notes:
            - Multiple matching rules are merged via OR logic
            - Empty agent_ids/tool_ids in a rule means "all" are allowed
            - If user matches no rules, returns empty sets
        """
        matching_rules = self.sudo().search([
            ('active', '=', True),
            '|', ('group_ids', 'in', user.groups_id.ids),
                 ('user_ids', 'in', user.id),
        ])

        agent_ids = self.env['mcp.agent']
        tool_ids = self.env['mcp.tool']
        can_view_sessions = False
        can_export = False
        rate_limit_day = 0
        rate_limit_month = 0
        
        has_allow_all_agents = False
        has_allow_all_tools = False

        for rule in matching_rules:
            # Agents: if rule has specific agents, OR them; if empty, allow all
            if rule.agent_ids:
                agent_ids = agent_ids | rule.agent_ids
            else:
                has_allow_all_agents = True

            # Tools: same logic
            if rule.tool_ids:
                tool_ids = tool_ids | rule.tool_ids
            else:
                has_allow_all_tools = True

            can_view_sessions = can_view_sessions or rule.can_view_sessions
            can_export = can_export or rule.can_export

            # Rate limits: take maximum (most permissive)
            if rule.rate_limit_day > 0:
                if rate_limit_day == 0:
                    rate_limit_day = rule.rate_limit_day
                else:
                    rate_limit_day = max(rate_limit_day, rule.rate_limit_day)

            if rule.rate_limit_month > 0:
                if rate_limit_month == 0:
                    rate_limit_month = rule.rate_limit_month
                else:
                    rate_limit_month = max(rate_limit_month, rule.rate_limit_month)

        return {
            'agent_ids': agent_ids,
            'tool_ids': tool_ids,
            'can_view_sessions': can_view_sessions,
            'can_export': can_export,
            'rate_limit_day': rate_limit_day,
            'rate_limit_month': rate_limit_month,
            'rules_matched': bool(matching_rules),
            'all_agents_allowed': has_allow_all_agents,
            'all_tools_allowed': has_allow_all_tools,
        }
