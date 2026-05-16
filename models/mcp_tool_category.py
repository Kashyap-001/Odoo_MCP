"""
mcp_gateway/models/mcp_tool_category.py

Tool categorization model for organizing tools by domain (Sales, Finance, HR, etc.).

Key classes:
  ToolCategory — tool category with grouping and display

Dependencies:
  - Odoo base models only

Developer notes:
  - Used for UI grouping in kanban and list views
  - Color field provides visual differentiation in views
"""

import logging
from odoo import fields, models, api, _

_logger = logging.getLogger(__name__)


class ToolCategory(models.Model):
    """
    Tool Category (mcp.tool.category)

    Organizes tools by functional domain. Provides UI grouping and
    visual differentiation in tool lists and kanban views.

    Relationships:
      - HasMany: mcp.tool via category_id

    Business rules:
      - Name must be unique
    """

    _name = 'mcp.tool.category'
    _description = _('MCP Tool Category')
    _order = 'sequence, name'

    sequence = fields.Integer(
        string=_('Sequence'),
        default=10,
        help=_('Order for UI display and Kanban grouping'),
    )
    name = fields.Char(
        string=_('Category Name'),
        required=True,
        translate=True,
        help=_('e.g., "Sales & CRM", "Finance & Accounting"'),
    )
    color = fields.Integer(
        string=_('Color'),
        default=0,
        help=_('Kanban color (0-11) for visual differentiation'),
    )
    icon = fields.Char(
        string=_('Icon'),
        help=_('Font Awesome class (e.g., "fa-shopping-cart", "fa-dollar-sign")'),
    )
    description = fields.Text(
        string=_('Description'),
        translate=True,
        help=_('What types of tools belong in this category'),
    )
    tool_ids = fields.One2many(
        comodel_name='mcp.tool',
        inverse_name='category_id',
        string=_('Tools'),
        help=_('Tools in this category'),
    )

    # Computed
    tool_count = fields.Integer(
        string=_('Tool Count'),
        compute='_compute_tool_count',
        store=True,
        help=_('Number of tools in this category'),
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Category name must be unique.')),
    ]

    @api.depends('tool_ids')
    def _compute_tool_count(self):
        """
        Count tools in each category.

        Returns:
            None — sets tool_count field
        """
        for category in self:
            category.tool_count = len(category.tool_ids)
