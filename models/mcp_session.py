"""
mcp_gateway/models/mcp_session.py

Session and message models for audit logging and chat history.

Key classes:
  Session — Conversation session with agent, audit data, token tracking
  SessionMessage — Individual message in a session (user, assistant, tool call, result)

Dependencies:
  - mcp.agent — agent in the session
  - res.users — user who initiated session
  - Imports json for metadata serialization
  - Imports base64 for file export encoding

Developer notes:
  - Sessions are immutable after creation (for audit compliance)
  - All tool calls logged BEFORE execution (audit trail exists even on crash)
  - Session state: active → done/error (one-way transition)
  - export_transcript generates downloadable .txt file with full chat history
"""

import logging
import re
import base64
from datetime import datetime, timedelta
from odoo import fields, models, api, _

_logger = logging.getLogger(__name__)


class Session(models.Model):
    """
    Session (mcp.session)

    Audit log of a single conversation with an AI agent.
    Immutable record for compliance and analysis.

    Relationships:
      - BelongsTo: res.users via user_id (who started it)
      - BelongsTo: mcp.agent via agent_id (which agent)
      - HasMany: mcp.session.message via session_message_ids
      - BelongsTo: mcp.cost.entry via cost_entries

    Business rules:
      - Once created, never modified (immutable audit log)
      - State: active → done/error (one-way)
      - All tool calls logged BEFORE execution
      - Tool results logged AFTER execution
    """

    _name = 'mcp.session'
    _description = _('Agent Session')
    _order = 'create_date DESC'
    _inherit = ['mail.thread']

    # ── Identifiers ─────────────────────────────────────────────────
    name = fields.Char(
        string=_('Session Name'),
        default=lambda self: _('New Session'),
        help=_('Auto-generated name (Session YYYY-MM-DD HH:MM)'),
    )
    user_id = fields.Many2one(
        comodel_name='res.users',
        string=_('User'),
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
        help=_('User who initiated this session'),
    )
    agent_id = fields.Many2one(
        comodel_name='mcp.agent',
        string=_('Agent'),
        required=True,
        help=_('AI agent in this session'),
    )
    is_pinned = fields.Boolean(
        string=_('Pinned'),
        default=False,
        help=_('If pinned, this session will not be automatically deleted by clean-up crons.'),
    )

    # ── Status & State ──────────────────────────────────────────────
    state = fields.Selection(
        [
            ('active', _('Active')),
            ('done', _('Done')),
            ('error', _('Error')),
        ],
        string=_('State'),
        default='active',
        required=True,
        tracking=True,
        readonly=True,
        help=_('Session lifecycle state'),
    )
    error_message = fields.Text(
        string=_('Error Message'),
        readonly=True,
        help=_('If state=error, the exception message'),
    )

    # ── Messages ────────────────────────────────────────────────────
    session_message_ids = fields.One2many(
        comodel_name='mcp.session.message',
        inverse_name='session_id',
        string=_('Session Messages'),
        readonly=True,
        help=_('Conversation messages and tool calls'),
    )

    # ── Token Usage ─────────────────────────────────────────────────
    input_tokens = fields.Integer(
        string=_('Input Tokens'),
        readonly=True,
        help=_('Total tokens in prompts sent to LLM'),
    )
    output_tokens = fields.Integer(
        string=_('Output Tokens'),
        readonly=True,
        help=_('Total tokens in LLM responses'),
    )

    # ── Cost Tracking ───────────────────────────────────────────────
    estimated_cost_usd = fields.Float(
        string=_('Estimated Cost (USD)'),
        compute='_compute_estimated_cost',
        store=True,
        readonly=True,
        help=_('USD cost calculated from token usage and agent rates'),
    )

    # ── Duration ────────────────────────────────────────────────────
    duration_seconds = fields.Float(
        string=_('Duration (seconds)'),
        compute='_compute_duration',
        store=True,
        readonly=True,
        help=_('Total time from session start to completion'),
    )

    # ── Metadata ────────────────────────────────────────────────────
    source = fields.Selection(
        [
            ('chat', _('Chat UI')),
            ('api', _('HTTP API')),
            ('webhook', _('Webhook')),
            ('wizard', _('Wizard')),
        ],
        string=_('Source'),
        readonly=True,
        default='chat',
        help=_('How this session was initiated'),
    )
    trigger_model = fields.Char(
        string=_('Trigger Model'),
        readonly=True,
        help=_('If source=webhook, the model that triggered it'),
    )
    trigger_res_id = fields.Integer(
        string=_('Trigger Record ID'),
        readonly=True,
        help=_('If source=webhook, the record ID that triggered it'),
    )
    metadata = fields.Text(
        string=_('Metadata (JSON)'),
        readonly=True,
        help=_('IP address, user-agent, and other context'),
    )

    # ── Computed ────────────────────────────────────────────────────
    tool_call_count = fields.Integer(
        string=_('Tool Calls'),
        compute='_compute_tool_call_count',
        store=True,
        readonly=True,
        help=_('Number of tool calls made during session'),
    )

    _sql_constraints = [
        ('state_check', "CHECK(state IN ('active', 'done', 'error'))", _('Invalid state.')),
    ]

    def message_post(self, body='', message_type='comment', subtype_xmlid='mail.mt_comment', **kwargs):
        """
        Override to auto-respond to user messages in chatter.

        When user posts a comment, call the agent and post response.
        """
        # Call original first to save user message
        result = super().message_post(
            body=body,
            message_type=message_type,
            subtype_xmlid=subtype_xmlid,
            **kwargs
        )

        # Only respond to user comments, not from agent/system
        if message_type == 'comment' and not self._context.get('mcp_agent_response'):
            # Get plain text from HTML body
            user_msg = self._strip_html(body)

            if user_msg and user_msg.strip():
                try:
                    from ..mcp.gateway import McpGateway
                    gateway = McpGateway(self.env, self.env.user)
                    _logger.info('Calling gateway for agent_id=%s, message=%s', self.agent_id.id, user_msg[:50])
                    response = gateway.run(
                        agent_id=self.agent_id.id,
                        user_message=user_msg,
                        session_id=self.id,
                    )
                    _logger.info('Gateway response: %s', response)

                    # Post agent response with flag to prevent loop
                    reply_text = response.get('reply', '')
                    if not reply_text:
                        _logger.warning('Gateway returned empty reply')
                        reply_text = ' (No response from agent)'

                    # Get author (bot user) and agent name for this agent
                    bot_user = self.agent_id.author_id or self.agent_id._get_bot_user()
                    author_partner = bot_user.partner_id.id if bot_user.partner_id else None
                    agent_name = self.agent_id.name or 'Agent'

                    # Check if reply contains HTML - format accordingly
                    if reply_text and ('<' in reply_text and '>' in reply_text):
                        # HTML content - format as HTML message
                        self.with_context(mcp_agent_response=True).message_post(
                            body=f"<b>[{agent_name}]</b><br/>{reply_text}",
                            message_type='comment',
                            author_id=author_partner,
                            subtype_xmlid='mail.mt_comment',
                            body_is_html=True,
                        )
                    else:
                        # Plain text - wrap in agent name
                        self.with_context(mcp_agent_response=True).message_post(
                            body=f"[{agent_name}]: {reply_text}",
                            message_type='comment',
                            author_id=author_partner,
                        )
                except Exception as e:
                    _logger.error('Agent response failed: %s', str(e))
                    import traceback
                    _logger.error('Traceback: %s', traceback.format_exc())

                    # Get author (bot user) for this agent
                    bot_user = self.agent_id.author_id or self.agent_id._get_bot_user()
                    author_partner = bot_user.partner_id.id if bot_user.partner_id else None
                    agent_name = self.agent_id.name or 'Agent'

                    self.with_context(mcp_agent_response=True).message_post(
                        body=f"[{agent_name}]: {str(e)}",
                        message_type='comment',
                        author_id=author_partner,
                    )

        return result

    def _strip_html(self, html):
        """Strip HTML tags to get plain text."""
        clean = re.sub('<[^<]+?>', '', html or '')
        return clean.strip()

    @api.model_create_multi
    def create(self, vals_list):
        """
        Create sessions with auto-generated names.

        Args:
            vals_list (list): List of value dicts

        Returns:
            RecordSet: Created session records
        """
        for vals in vals_list:
            if not vals.get('name'):
                now = datetime.now()
                vals['name'] = now.strftime(_('Session %Y-%m-%d %H:%M'))
        records = super().create(vals_list)
        # Delete any empty sessions (no messages) left by previous abandoned chats
        new_ids = records.ids
        for rec in records:
            if rec.user_id:
                self.sudo().search([
                    ('user_id', '=', rec.user_id.id),
                    ('id', 'not in', new_ids),
                    ('session_message_ids', '=', False),
                ]).unlink()
        return records

    @api.model
    def cron_cleanup_sessions(self):
        """Called by Odoo Cron to delete empty and stale sessions."""
        # 1. Delete all empty sessions (exempting pinned ones)
        empty_sessions = self.search([
            ('session_message_ids', '=', False),
            ('is_pinned', '=', False)
        ])
        empty_count = len(empty_sessions)
        if empty_sessions:
            empty_sessions.unlink()

        # 2. Delete sessions older than 30 days (exempting pinned ones)
        limit_date = fields.Datetime.now() - timedelta(days=30)
        stale_sessions = self.search([
            ('create_date', '<', limit_date),
            ('is_pinned', '=', False)
        ])
        stale_count = len(stale_sessions)
        if stale_sessions:
            stale_sessions.unlink()

        _logger.info("MCP Cleanup: Deleted %d empty sessions and %d stale sessions.", empty_count, stale_count)
        return True

    @api.depends('session_message_ids')
    def _compute_tool_call_count(self):
        """
        Count tool calls in session.

        Returns:
            None — sets tool_call_count field
        """
        for session in self:
            session.tool_call_count = len(
                session.session_message_ids.filtered(lambda m: m.role == 'tool_call')
            )

    @api.depends('input_tokens', 'output_tokens', 'agent_id')
    def _compute_estimated_cost(self):
        """
        Calculate USD cost from token usage and agent rates.

        Returns:
            None — sets estimated_cost_usd field
        """
        for session in self:
            agent = session.agent_id
            if agent:
                input_cost = (session.input_tokens / 1000) * agent.cost_per_1k_input
                output_cost = (session.output_tokens / 1000) * agent.cost_per_1k_output
                session.estimated_cost_usd = input_cost + output_cost
            else:
                session.estimated_cost_usd = 0.0

    @api.depends('create_date', 'session_message_ids.create_date')
    def _compute_duration(self):
        """
        Calculate session duration from first message to last.

        Returns:
            None — sets duration_seconds field
        """
        for session in self:
            if session.session_message_ids:
                first = session.create_date
                last = max(m.create_date for m in session.session_message_ids)
                delta = last - first
                session.duration_seconds = delta.total_seconds()
            else:
                session.duration_seconds = 0.0

    def action_export_transcript(self):
        """
        Export session transcript as plaintext .txt file download.

        Returns:
            dict: ir.actions.act_url to download file
        """
        lines = [f'Session: {self.name}']
        lines.append(f'Agent: {self.agent_id.name}')
        lines.append(f'User: {self.user_id.name}')
        lines.append(f'Date: {self.create_date}')
        lines.append(f'Duration: {self.duration_seconds:.1f}s')
        lines.append(f'Tokens: {self.input_tokens} in, {self.output_tokens} out')
        lines.append(f'Cost: ${self.estimated_cost_usd:.4f}')
        lines.append('=' * 60)

        for msg in self.session_message_ids:
            lines.append(f'\n[{msg.role.upper()}] ({msg.create_date})')
            if msg.role == 'tool_call':
                lines.append(f'Tool: {msg.tool_name}')
                lines.append(f'Arguments: {msg.content}')
            elif msg.role == 'tool_result':
                lines.append(f'Result: {msg.content[:200]}...' if len(msg.content) > 200
                            else f'Result: {msg.content}')
            else:
                lines.append(msg.content)

        content = '\n'.join(lines)
        encoded = base64.b64encode(content.encode()).decode()
        filename = f'{self.name.replace(" ", "_")}.txt'

        return {
            'type': 'ir.actions.act_url',
            'url': f'data:text/plain;base64,{encoded}',
            'target': 'download',
            'name': filename,
        }

    def action_replay(self):
        """
        Open chat wizard in read-only mode to review session.

        Returns:
            dict: Chat wizard action
        """
        return {
            'name': _('Review Session: %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'mcp.chat.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_agent_id': self.agent_id.id,
                'default_session_id': self.id,
                'readonly': True,
            },
        }


class SessionMessage(models.Model):
    """
    Session Message (mcp.session.message)

    Individual message or event in a session's audit log.
    Roles: user, assistant, tool_call, tool_result, system.

    Relationships:
      - BelongsTo: mcp.session via session_id

    Business rules:
      - Immutable (created once, never modified)
      - Tool calls logged BEFORE execution
      - Tool results logged AFTER execution
      - Messages ordered by create_date (implicit ordering)
    """

    _name = 'mcp.session.message'
    _description = _('Session Message')
    _order = 'create_date ASC'

    session_id = fields.Many2one(
        comodel_name='mcp.session',
        string=_('Session'),
        required=True,
        readonly=True,
        ondelete='cascade',
        help=_('Parent session'),
    )
    role = fields.Selection(
        [
            ('user', _('User')),
            ('assistant', _('Assistant')),
            ('tool_call', _('Tool Call')),
            ('tool_result', _('Tool Result')),
            ('system', _('System')),
        ],
        string=_('Role'),
        required=True,
        readonly=True,
        help=_('Message source/type'),
    )
    content = fields.Text(
        string=_('Content'),
        required=True,
        readonly=True,
        help=_('Message text or tool arguments/result'),
    )
    tool_name = fields.Char(
        string=_('Tool Name'),
        readonly=True,
        help=_('If role=tool_call or tool_result, the tool name'),
    )
    tool_call_id = fields.Char(
        string=_('Tool Call ID'),
        readonly=True,
        help=_('Provider-specific ID for correlating tool calls and results'),
    )
    token_count = fields.Integer(
        string=_('Token Count'),
        readonly=True,
        help=_('Approximate tokens in this message'),
    )
    duration_ms = fields.Integer(
        string=_('Duration (ms)'),
        readonly=True,
        help=_('How long this step took (e.g., tool execution time)'),
    )
    create_date = fields.Datetime(
        string=_('Created'),
        readonly=True,
        default=lambda self: datetime.now(),
        help=_('Timestamp of message'),
    )

    _sql_constraints = [
        ('role_valid', "CHECK(role IN ('user','assistant','tool_call','tool_result','system'))",
         _('Invalid role.')),
    ]
