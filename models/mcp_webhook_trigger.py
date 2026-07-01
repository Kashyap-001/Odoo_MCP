"""
mcp_gateway/models/mcp_webhook_trigger.py

Webhook trigger model for automatically invoking agents on Odoo model events.

Key classes:
  WebhookTrigger — Configure automatic agent calls on create/write/delete events

Dependencies:
  - mcp.agent — agent to invoke
  - mcp.tool.set — tools available in triggered session
  - mcp.session — created session record
  - Imports uuid for token generation
  - Imports jinja2 for message template rendering

Developer notes:
  - Tokens are URL-safe secrets (UUIDs) for webhook endpoints
  - Domain filters restrict which records trigger the webhook
  - Message templates use Jinja2 with {record} variable for record data
  - Triggered sessions use source='webhook' for audit trail
"""

import logging
import uuid
from odoo import fields, models, api, _, exceptions
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WebhookTrigger(models.Model):
    """
    Webhook Trigger (mcp.webhook.trigger)

    Automatically invoke an AI agent when specific Odoo model events occur.
    Useful for automated analysis, summarization, and actions.

    Relationships:
      - BelongsTo: mcp.agent via agent_id
      - BelongsTo: mcp.tool.set via tool_set_id (optional)

    Business rules:
      - Token must be unique and kept secret
      - Domain filter restricts which records trigger
      - Message template rendered per record
      - Only one trigger per agent per model per event (recommended)
    """

    _name = 'mcp.webhook.trigger'
    _description = _('AI Agent Webhook Trigger')
    _order = 'sequence, name'

    sequence = fields.Integer(
        string=_('Sequence'),
        default=10,
    )
    name = fields.Char(
        string=_('Trigger Name'),
        required=True,
        translate=True,
        help=_('e.g., "Auto-email on new lead"'),
    )
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        help=_('Inactive triggers do not fire'),
    )

    # ── Trigger Configuration ───────────────────────────────────────
    agent_id = fields.Many2one(
        comodel_name='mcp.agent',
        string=_('Agent'),
        required=True,
        help=_('Agent to invoke'),
    )
    tool_set_id = fields.Many2one(
        comodel_name='mcp.tool.set',
        string=_('Tool Set'),
        help=_('Optional: limit tools available in triggered session'),
    )
    trigger_model = fields.Char(
        string=_('Trigger Model'),
        required=True,
        help=_('e.g., "crm.lead", "sale.order", "account.move"'),
    )
    trigger_on = fields.Selection(
        [
            ('create', _('On Create')),
            ('write', _('On Write')),
            ('unlink', _('On Delete')),
        ],
        string=_('Trigger Event'),
        required=True,
        default='create',
    )
    trigger_fields = fields.Char(
        string=_('Trigger Fields'),
        help=_('For write events: only fire if these fields changed (comma-sep)'),
    )
    domain = fields.Text(
        string=_('Domain Filter'),
        default='[]',
        help=_('JSON domain (e.g., [["country_id.code","=","US"]]). Empty = all records.'),
    )

    # ── Message Template ────────────────────────────────────────────
    message_template = fields.Text(
        string=_('Message Template'),
        required=True,
        translate=True,
        help=_('Jinja2 template with {record} variable. E.g., "New lead: {record.name}"'),
    )

    # ── Outbound (Odoo → n8n/Zapier) ────────────────────────────────
    outbound_url = fields.Char(
        string=_('Outbound URL'),
        help=_('Paste your n8n/Zapier webhook URL here. Odoo will POST the result to this URL after the AI runs.'),
    )
    outbound_secret = fields.Char(
        string=_('Outbound Secret'),
        help=_('Optional: sent as Authorization: Bearer <secret> header'),
    )

    # ── Token & Audit ───────────────────────────────────────────────
    token = fields.Char(
        string=_('Webhook Token'),
        readonly=True,
        groups='mcp_gateway.group_mcp_admin',
        help=_('Secret token for external webhook endpoint'),
    )
    webhook_url = fields.Char(
        string=_('Webhook URL'),
        compute='_compute_webhook_url',
        readonly=True,
        store=False,
        help=_('Generated URL for this webhook trigger'),
    )
    description = fields.Text(
        string=_('Description'),
        translate=True,
        help=_('Optional details about this webhook trigger'),
    )
    last_triggered = fields.Datetime(
        string=_('Last Triggered'),
        readonly=True,
        help=_('Timestamp of most recent trigger'),
    )
    trigger_count = fields.Integer(
        string=_('Trigger Count'),
        readonly=True,
        default=0,
        help=_('Total number of times triggered'),
    )

    _sql_constraints = [
        ('token_uniq', 'UNIQUE(token)', _('Token must be unique.')),
    ]

    @api.depends('token')
    def _compute_webhook_url(self):
        """
        Compute the public URL for the webhook trigger.
        """
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        for trigger in self:
            if trigger.token:
                if base_url:
                    trigger.webhook_url = f"{base_url.rstrip('/')}/mcp/webhook/{trigger.token}"
                else:
                    trigger.webhook_url = f"/mcp/webhook/{trigger.token}"
            else:
                trigger.webhook_url = ''

    @api.model_create_multi
    def create(self, vals_list):
        """
        Create triggers and generate tokens if missing.

        Args:
            vals_list (list): Value dicts

        Returns:
            RecordSet: Created trigger records
        """
        for vals in vals_list:
            if not vals.get('token'):
                vals['token'] = str(uuid.uuid4())
        return super().create(vals_list)

    def action_generate_token(self):
        """
        Generate a new webhook token.

        Overwrites existing token with a new UUID.

        Returns:
            None
        """
        for trigger in self:
            trigger.token = str(uuid.uuid4())
            _logger.info('Generated new token for webhook trigger: %s', trigger.name)

    def _get_message(self, record) -> str:
        """
        Render message template for a specific record.

        Args:
            record: Odoo model instance to render template with

        Returns:
            str: Rendered message ready to send to agent

        Raises:
            UserError: if template rendering fails

        Example:
            message = trigger._get_message(lead_record)
            # Output: "New lead: John Doe"
        """
        try:
            from jinja2 import Template
            template = Template(self.message_template)
            return template.render(record=record)
        except Exception as e:
            raise exceptions.UserError(
                _('Message template render error: %s') % str(e)
            )

    def fire(self, record):
        """
        Invoke the agent for this trigger with the given record.

        Creates a new session and calls the gateway to process the message.
        Session is created with source='webhook' for audit trail.

        Args:
            record: Odoo record that triggered this webhook

        Returns:
            dict: Session data with reply and tokens

        Raises:
            UserError: if agent call fails

        Developer notes:
            - This method is called by the HTTP webhook endpoint
            - Also called by automatic create/write/unlink hooks if enabled
            - Session recorded with trigger_model and trigger_res_id
        """
        from ..mcp.gateway import McpGateway

        if record is not None and record._name != self.trigger_model:
            raise exceptions.ValidationError(
                _('Model mismatch: webhook trigger expects %s, got %s')
                % (self.trigger_model, record._name)
            )

        try:
            message = self._get_message(record) if record is not None else self.message_template
            session_vals = {
                'agent_id': self.agent_id.id,
                'user_id': self.env.user.id,
                'source': 'webhook',
                'metadata': '{}',
            }
            if record is not None:
                session_vals['trigger_model'] = record._name
                session_vals['trigger_res_id'] = record.id
            session = self.env['mcp.session'].create(session_vals)

            gateway = McpGateway(self.env, self.env.user)
            run_kwargs = {
                'agent_id': self.agent_id.id,
                'user_message': message,
                'session_id': session.id,
            }
            if record is not None:
                run_kwargs['active_model'] = record._name
                run_kwargs['active_id'] = record.id
            result = gateway.run(**run_kwargs)

            self.trigger_count += 1
            self.last_triggered = fields.Datetime.now()

            if self.outbound_url:
                self._call_outbound(record, result)

            return result
        except Exception as e:
            _logger.error('Webhook trigger failed: %s', str(e))
            raise UserError(_('Webhook invocation failed: %s') % str(e))

    def _call_outbound(self, record, ai_result):
        import requests as _requests
        payload = {
            'trigger': self.name,
            'model': record._name if record else None,
            'record_id': record.id if record else None,
            'ai_reply': ai_result.get('reply', ''),
            'session_id': ai_result.get('session_id'),
        }
        if record:
            # Send basic readable fields only (skip binaries/computed)
            safe_fields = [
                f for f, fd in record._fields.items()
                if not fd.compute and fd.type not in ('binary',)
            ]
            try:
                payload['record_data'] = record.read(safe_fields[:20])[0]
            except Exception:
                pass
        headers = {'Content-Type': 'application/json'}
        if self.outbound_secret:
            headers['Authorization'] = f'Bearer {self.outbound_secret}'
        try:
            _requests.post(self.outbound_url, json=payload, timeout=10)
        except Exception as e:
            _logger.warning('Outbound webhook call to %s failed: %s', self.outbound_url, e)
