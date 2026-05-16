"""
mcp_gateway/models/mcp_cost_entry.py

Cost entry model for tracking LLM token usage and expenses.

Key classes:
  CostEntry — Record of token usage and USD cost from a session

Dependencies:
  - mcp.session — session that consumed tokens
  - mcp.agent — agent used (related from session)
  - res.users — user who ran session (related from session)
  - res.currency — currency for cost (default USD)

Developer notes:
  - Automatically created after each session completes
  - Costs calculated from agent.cost_per_1k_input/output rates
  - One-to-one relationship with session (for audit trail)
"""

import logging
from odoo import fields, models, api, _

_logger = logging.getLogger(__name__)


class CostEntry(models.Model):
    """
    Cost Entry (mcp.cost.entry)

    Record of token usage and USD cost from an agent session.
    Used for billing, cost analysis, and budget tracking.

    Relationships:
      - BelongsTo: mcp.session via session_id (one-to-one)
      - BelongsTo: mcp.agent via agent_id (related from session)
      - BelongsTo: res.users via user_id (related from session)
      - BelongsTo: res.currency via currency_id

    Business rules:
      - Automatically created when session completes
      - Costs copied from agent rates at time of session
      - Immutable (for cost tracking accuracy)
    """

    _name = 'mcp.cost.entry'
    _description = _('LLM Cost Entry')
    _order = 'date DESC, session_id DESC'

    # ── Session Reference ───────────────────────────────────────────
    session_id = fields.Many2one(
        comodel_name='mcp.session',
        string=_('Session'),
        required=True,
        readonly=True,
        ondelete='cascade',
        help=_('Session that incurred this cost'),
    )

    # ── Related Records (via session) ────────────────────────────────
    agent_id = fields.Many2one(
        comodel_name='mcp.agent',
        string=_('Agent'),
        related='session_id.agent_id',
        readonly=True,
        store=True,
        help=_('Agent used in session'),
    )
    user_id = fields.Many2one(
        comodel_name='res.users',
        string=_('User'),
        related='session_id.user_id',
        readonly=True,
        store=True,
        help=_('User who initiated session'),
    )

    # ── Token Usage ─────────────────────────────────────────────────
    date = fields.Date(
        string=_('Date'),
        default=lambda self: fields.Date.today(),
        readonly=True,
        help=_('Date session occurred'),
    )
    input_tokens = fields.Integer(
        string=_('Input Tokens'),
        required=True,
        readonly=True,
        help=_('Tokens sent to LLM'),
    )
    output_tokens = fields.Integer(
        string=_('Output Tokens'),
        required=True,
        readonly=True,
        help=_('Tokens returned from LLM'),
    )

    # ── Pricing ─────────────────────────────────────────────────────
    cost_per_1k_input = fields.Float(
        string=_('Price per 1K Input'),
        readonly=True,
        help=_('Cost rate used for this entry (USD)'),
    )
    cost_per_1k_output = fields.Float(
        string=_('Price per 1K Output'),
        readonly=True,
        help=_('Cost rate used for this entry (USD)'),
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        string=_('Currency'),
        readonly=True,
        default=lambda self: self.env.ref('base.USD'),
        help=_('Currency for cost calculation'),
    )

    # ── Metadata ────────────────────────────────────────────────────
    create_date = fields.Datetime(
        string=_('Created'),
        readonly=True,
        default=lambda self: fields.Datetime.now(),
        help=_('Timestamp of creation'),
    )

    # ── Computed ────────────────────────────────────────────────────
    total_cost_usd = fields.Float(
        string=_('Total Cost (USD)'),
        compute='_compute_total_cost',
        store=True,
        readonly=True,
        help=_('USD cost from token usage * rates'),
    )

    @api.depends('input_tokens', 'output_tokens', 'cost_per_1k_input', 'cost_per_1k_output')
    def _compute_total_cost(self):
        """
        Calculate USD cost from token usage and rates.

        Returns:
            None — sets total_cost_usd field
        """
        for entry in self:
            if entry.input_tokens and entry.cost_per_1k_input:
                input_cost = (entry.input_tokens / 1000) * entry.cost_per_1k_input
            else:
                input_cost = 0.0
            if entry.output_tokens and entry.cost_per_1k_output:
                output_cost = (entry.output_tokens / 1000) * entry.cost_per_1k_output
            else:
                output_cost = 0.0
            entry.total_cost_usd = input_cost + output_cost
