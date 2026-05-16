"""
mcp_gateway/models/mcp_issue.py

Issue tracking model for AI Gateway - internal bug/enhancement tracking.

Key classes:
  Issue — Track bugs and feature requests within the module

Dependencies:
  - res.users — Assignee and reporter
"""

import logging
from odoo import fields, models, _

_logger = logging.getLogger(__name__)


class Issue(models.Model):
    """
    Issue (mcp.issue)

    Internal issue tracking for bugs and enhancements in the AI Gateway module.
    Supports triage workflow with category and state fields.

    Categories:
      - bug — Something is broken
      - enhancement — New feature or improvement

    States:
      - needs_triage — Requires evaluation
      - needs_info — Waiting on reporter
      - ready_for_agent — Ready for AFK agent
      - ready_for_human — Needs human implementation
      - wontfix — Will not be actioned
      - done — Completed
    """

    _name = 'mcp.issue'
    _description = _('AI Gateway Issue')
    _order = 'priority desc, create_date desc'

    # ── Category ─────────────────────────────────────────────────────
    CATEGORY_SELECTION = [
        ('bug', _('Bug')),
        ('enhancement', _('Enhancement')),
    ]

    STATE_SELECTION = [
        ('needs_triage', _('Needs Triage')),
        ('needs_info', _('Needs Info')),
        ('ready_for_agent', _('Ready for Agent')),
        ('ready_for_human', _('Ready for Human')),
        ('wontfix', _("Won't Fix")),
        ('done', _('Done')),
    ]

    name = fields.Char(
        string=_('Title'),
        required=True,
        translate=True,
        help=_('Brief summary of the issue'),
    )
    category = fields.Selection(
        selection=CATEGORY_SELECTION,
        string=_('Category'),
        required=True,
        default='bug',
        help=_('Type of issue: bug or enhancement'),
    )
    state = fields.Selection(
        selection=STATE_SELECTION,
        string=_('State'),
        required=True,
        default='needs_triage',
        tracking=True,
        help=_('Current triage state'),
    )
    priority = fields.Integer(
        string=_('Priority'),
        default=5,
        help=_('Higher number = higher priority (1-10)'),
    )

    # ── Details ───────────────────────────────────────────────────────
    description = fields.Text(
        string=_('Description'),
        translate=True,
        help=_('Detailed description, steps to reproduce, etc.'),
    )

    # ── People ───────────────────────────────────────────────────────
    reporter_id = fields.Many2one(
        comodel_name='res.users',
        string=_('Reporter'),
        default=lambda self: self.env.user,
        required=True,
        help=_('Who reported this issue'),
    )
    assignee_id = fields.Many2one(
        comodel_name='res.users',
        string=_('Assignee'),
        help=_('Who is working on this'),
    )

    # ── Tracking ─────────────────────────────────────────────────────
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        help=_('Inactive issues are archived'),
    )
    tag_ids = fields.Many2many(
        comodel_name='mcp.issue.tag',
        relation='mcp_issue_tag_rel',
        column1='issue_id',
        column2='tag_id',
        string=_('Tags'),
        help=_('Optional tags for organization'),
    )

    # ── Triage ───────────────────────────────────────────────────────
    triage_notes = fields.Text(
        string=_('Triage Notes'),
        help=_('Notes from triage process'),
    )

    _sql_constraints = [
        ('name_not_empty', 'CHECK(name IS NOT NULL AND name != \'\')',
         _('Title is required.')),
    ]

    def action_mark_needs_triage(self):
        self.write({'state': 'needs_triage'})

    def action_mark_needs_info(self):
        self.write({'state': 'needs_info'})

    def action_mark_ready_for_agent(self):
        self.write({'state': 'ready_for_agent'})

    def action_mark_ready_for_human(self):
        self.write({'state': 'ready_for_human'})

    def action_mark_wontfix(self):
        self.write({'state': 'wontfix'})

    def action_mark_done(self):
        self.write({'state': 'done'})


class IssueTag(models.Model):
    """Tag for organizing issues."""

    _name = 'mcp.issue.tag'
    _description = _('Issue Tag')

    name = fields.Char(
        string=_('Name'),
        required=True,
        translate=True,
    )
    color = fields.Integer(
        string=_('Color Index'),
        default=0,
        help=_('Color for kanban view'),
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Tag name must be unique.')),
    ]