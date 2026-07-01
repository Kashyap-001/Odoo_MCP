from odoo import fields, models


class McpModelOption(models.Model):
    _name = 'mcp.model.option'
    _description = 'Available Model'
    _rec_name = 'name'
    _order = 'provider, name'
    _sql_constraints = [
        ('unique_provider_name', 'UNIQUE(provider, name)', 'Model already exists for this provider'),
    ]

    provider = fields.Char(required=True)
    name = fields.Char(required=True, string='Model ID')
    is_discovered = fields.Boolean(default=False, help='Fetched live from provider API')
