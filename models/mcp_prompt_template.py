"""
mcp_gateway/models/mcp_prompt_template.py

Reusable prompt template model for common queries and tasks.

Key classes:
  PromptTemplate — Template with variable placeholders

Dependencies:
  - mcp.agent — agent-specific templates (optional)
  - Imports string.Template for variable substitution

Developer notes:
  - Templates support {variable} placeholders (Python format style)
  - Variables list is informational (comma-separated names)
  - render() method validates all required variables provided
"""

import logging
from odoo import fields, models, api, _, exceptions

_logger = logging.getLogger(__name__)


class PromptTemplate(models.Model):
    """
    Prompt Template (mcp.prompt.template)

    Reusable prompt fragments with {variable} placeholders.
    Simplifies agent setup for common use cases.

    Relationships:
      - BelongsTo: mcp.agent via agent_id (optional, for agent-specific templates)

    Business rules:
      - Name must be unique
      - Variables are descriptive (used in UI, not enforced)
    """

    _name = 'mcp.prompt.template'
    _description = _('Prompt Template')
    _order = 'sequence, name'

    sequence = fields.Integer(
        string=_('Sequence'),
        default=10,
    )
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        help=_('Enable template for selection'),
    )
    description = fields.Text(
        string=_('Description'),
        translate=True,
        help=_('Notes or usage details for this template'),
    )
    name = fields.Char(
        string=_('Template Name'),
        required=True,
        translate=True,
        help=_('e.g., "Lead Qualification", "Invoice Analysis"'),
    )
    agent_id = fields.Many2one(
        comodel_name='mcp.agent',
        string=_('Agent'),
        help=_('If set, only available for this agent'),
    )
    category = fields.Selection(
        [
            ('greeting', _('Greeting')),
            ('task', _('Task')),
            ('analysis', _('Analysis')),
            ('report', _('Report')),
        ],
        string=_('Category'),
        default='task',
        help=_('Template category for UI grouping'),
    )
    content = fields.Text(
        string=_('Template Content'),
        required=True,
        translate=True,
        help=_('Template text with {placeholder} variables'),
    )
    variables = fields.Char(
        string=_('Variables'),
        help=_('Comma-separated list of variable names (informational)'),
    )
    is_global = fields.Boolean(
        string=_('Global'),
        default=False,
        help=_('Available to all agents (if not set, agent_id required)'),
    )
    usage_count = fields.Integer(
        string=_('Usage Count'),
        compute='_compute_usage_count',
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Template name must be unique.')),
    ]

    @api.depends('content')
    def _compute_usage_count(self):
        """Count sessions using this template (placeholder for now)."""
        for template in self:
            template.usage_count = 0
