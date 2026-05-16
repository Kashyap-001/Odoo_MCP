"""
mcp_gateway/wizard/mcp_chat_wizard.py

In-Odoo chat wizard for selecting agent and starting a chat session.

Key classes:
  ChatWizard — Select agent and open session with chatter

Dependencies:
  - mcp.session — session management
  - mcp.agent — agent selection

Developer notes:
  - Simple wizard: select agent → Start Chat → session form with chatter
  - User types in chatter, agent auto-responds via message_post override
"""

import logging
from odoo import fields, models, _, exceptions

_logger = logging.getLogger(__name__)


class ChatWizard(models.TransientModel):
    """
    Chat Wizard (mcp.chat.wizard)

    Simple wizard to select an agent and start a chat session.
    The actual chatting happens in the session's chatter.

    Flow:
      1. User selects agent in form
      2. Clicks Start Chat
      3. Session form opens with chatter
      4. User types in chatter, agent auto-responds
    """

    _name = 'mcp.chat.wizard'
    _description = _('AI Agent Chat')

    agent_id = fields.Many2one(
        comodel_name='mcp.agent',
        string=_('Agent'),
        required=True,
        help=_('AI agent to chat with'),
    )

    def action_start_chat(self):
        """
        Create session with selected agent and open it.

        The session form has chatter where the user can type
        and the agent will auto-respond.

        Returns:
            dict: Action to open session form
        """
        if not self.agent_id:
            raise exceptions.UserError(_('Please select an agent'))

        # Create session with selected agent
        session = self.env['mcp.session'].create({
            'agent_id': self.agent_id.id,
            'user_id': self.env.user.id,
            'source': 'chat',
        })

        # Open session form (chatter is the chat interface)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mcp.session',
            'res_id': session.id,
            'view_mode': 'form',
            'target': 'new',
        }