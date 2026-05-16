"""
mcp_gateway/wizard/mcp_connection_test_wizard.py

Connection test wizard for testing agent provider connectivity.

Key classes:
  ConnectionTestWizard — Display connection test results
"""

from odoo import fields, models, _


class ConnectionTestWizard(models.TransientModel):
    """
    Connection Test Wizard (mcp.connection.test.wizard)

    Displays results from testing agent provider connection.
    Shows status, message, and available models.

    Fields:
      status: success or error
      message: user-friendly status message
      model_list: available models from provider (if successful)
    """
    _name = 'mcp.connection.test.wizard'
    _description = 'Connection Test Wizard'

    status = fields.Selection([
        ('success', 'Success'),
        ('error', 'Error'),
    ], string='Status', readonly=True, default='error')

    message = fields.Text(
        string='Message',
        readonly=True,
        default='',
    )

    model_list = fields.Text(
        string='Available Models',
        readonly=True,
        default='',
    )

    def default_get(self, fields_list):
        """Populate defaults from context passed by action_test_connection."""
        defaults = super().default_get(fields_list)
        context = self.env.context
        defaults['status'] = context.get('default_status', 'error')
        defaults['message'] = context.get('default_message') or 'Unknown error'
        defaults['model_list'] = context.get('default_model_list') or ''
        return defaults

    def action_close(self):
        """Close the wizard."""
        return {'type': 'ir.actions.act_window_close'}