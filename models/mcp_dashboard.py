from odoo import models, fields, api, _, exceptions


class McpDashboard(models.Model):
    _name = 'mcp.dashboard'
    _description = _('MCP Chart Dashboard Page')
    _order = 'sequence, name'

    name = fields.Char(string=_('Dashboard Name'), required=True)
    sequence = fields.Integer(string=_('Sequence'), default=10)
    icon = fields.Char(string=_('Icon'), default='📊')
    chart_ids = fields.One2many('mcp.echart', 'dashboard_id', string=_('Charts'))
    chart_count = fields.Integer(string=_('Chart Count'), compute='_compute_chart_count')
    is_shared = fields.Boolean(
        string=_('Shared'), default=False,
        help=_('Visible to every AI Gateway User, not just its creator (managers/admins '
               'always see everything regardless of this flag). Unrelated to a chart\'s '
               'public internet share link (is_public on mcp.echart) — this only controls '
               'internal visibility between Odoo users. Defaults to private (2026-07-06) — '
               'only dashboards created before that stay shared by default, including the '
               'seeded "Uncategorized" dashboard.'),
    )

    @api.depends('chart_ids')
    def _compute_chart_count(self):
        for rec in self:
            rec.chart_count = len(rec.chart_ids)

    def unlink(self):
        uncategorized = self.env.ref('mcp_gateway.mcp_dashboard_uncategorized', raise_if_not_found=False)
        if uncategorized and uncategorized in self:
            raise exceptions.UserError(_('The "Uncategorized" dashboard cannot be deleted.'))
        if uncategorized:
            self.mapped('chart_ids').write({'dashboard_id': uncategorized.id})
        return super().unlink()
