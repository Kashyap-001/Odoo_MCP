"""
mcp_gateway/models/mcp_tool_set.py

Tool set model for bundling related tools into reusable collections.

Key classes:
  ToolSet — Bundle of related tools that can be assigned to agents

Dependencies:
  - mcp.tool — tools in the set
  - mcp.agent — agents using this set

Developer notes:
  - Used to simplify agent configuration (assign entire bundle vs individual tools)
  - Supports three pre-configured sets: Sales & CRM, Finance & Accounting, Operations & HR
"""

import logging
from odoo import fields, models, api, _

_logger = logging.getLogger(__name__)


class ToolSet(models.Model):
    """
    Tool Set (mcp.tool.set)

    Pre-configured bundle of related tools that can be assigned to agents.
    Simplifies agent setup by grouping tools by domain.

    Relationships:
      - HasMany: mcp.tool via many2many
      - HasMany: mcp.agent via many2many (inverse of agent.tool_set_ids)

    Business rules:
      - Name must be unique
      - At least one tool recommended (but not required)
    """

    _name = 'mcp.tool.set'
    _description = _('MCP Tool Set')
    _order = 'sequence, name'

    sequence = fields.Integer(
        string=_('Sequence'),
        default=10,
        help=_('Order in UI'),
    )
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        help=_('Enable this tool set'),
    )
    name = fields.Char(
        string=_('Name'),
        required=True,
        translate=True,
        help=_('e.g., "Sales & CRM", "Finance & Accounting"'),
    )
    description = fields.Text(
        string=_('Description'),
        translate=True,
        help=_('What this tool set is for'),
    )
    color = fields.Integer(
        string=_('Color'),
        default=0,
        help=_('Kanban color (0-11)'),
    )
    tool_ids = fields.Many2many(
        comodel_name='mcp.tool',
        string=_('Tools'),
        help=_('Tools included in this set'),
    )
    agent_ids = fields.Many2many(
        comodel_name='mcp.agent',
        relation='mcp_agent_tool_set_rel',
        column1='tool_set_id',
        column2='agent_id',
        string=_('Agents'),
        help=_('Agents using this tool set'),
        compute='_compute_agent_ids',
        store=False,
    )

    # Computed
    tool_count = fields.Integer(
        string=_('Tool Count'),
        compute='_compute_tool_count',
        store=True,
        help=_('Number of tools in this set'),
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Tool set name must be unique.')),
    ]

    @api.depends('tool_ids')
    def _compute_tool_count(self):
        """
        Count tools in this set.

        Returns:
            None — sets tool_count field
        """
        for tool_set in self:
            tool_set.tool_count = len(tool_set.tool_ids)

    def _compute_agent_ids(self):
        """
        Find all agents using this tool set.

        Returns:
            None — sets agent_ids field (computed, not stored)
        """
        for tool_set in self:
            tool_set.agent_ids = self.env['mcp.agent'].search([
                ('tool_set_ids', 'in', tool_set.id)
            ])
