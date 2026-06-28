import secrets
import json
import math
import re
import datetime as dt_module
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval, wrap_module


class McpEchart(models.Model):
    _name = 'mcp.echart'
    _description = _('MCP EChart Dashboard Widget')
    _order = 'create_date desc'

    name = fields.Char(string=_('Chart Name'), required=True)
    data_code = fields.Text(
        string=_('Data Code'),
        help=_('Python script (env, user available) that returns a complete ECharts options dict.')
    )
    options = fields.Text(
        string=_('EChart Options (JSON)'),
        help=_('JSON options structure passed directly to Apache ECharts setOption().')
    )
    active = fields.Boolean(string=_('Active'), default=True)

    # Sharing
    public_token = fields.Char(string=_('Public Token'), readonly=True, copy=False, index=True)
    is_public = fields.Boolean(string=_('Public Access'), default=False)
    public_url = fields.Char(string=_('Public URL'), compute='_compute_public_url', store=False)
    embed_code = fields.Char(string=_('Embed Code'), compute='_compute_embed_code', store=False)
    expose_data = fields.Boolean(
        string=_('Expose Data in Tool Response'),
        default=True,
        help=_('When disabled, AI agents only generate dashboard code — data never leaves the server.')
    )

    # Advanced JS hooks
    pre_init_js = fields.Text(
        string=_('Pre-Init JavaScript'),
        help=_('JavaScript executed before echarts.init(). Use for registerMap, registerTheme, etc.')
    )
    post_init_js = fields.Text(
        string=_('Post-Init JavaScript'),
        help=_('JavaScript executed after setOption(). Variable "chart" is in scope. Use for click handlers, etc.')
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('public_token'):
                vals['public_token'] = secrets.token_urlsafe(32)
        return super().create(vals_list)

    @api.depends('is_public', 'public_token')
    def _compute_public_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            if rec.is_public and rec.public_token:
                rec.public_url = f'{base}/mcp/echart/public/{rec.id}/{rec.public_token}'
            else:
                rec.public_url = ''

    @api.depends('public_url')
    def _compute_embed_code(self):
        for rec in self:
            if rec.public_url:
                rec.embed_code = (
                    f'<iframe src="{rec.public_url}?embed=1" '
                    f'width="100%" height="700" style="border:none;" loading="lazy"></iframe>'
                )
            else:
                rec.embed_code = ''

    def action_refresh_chart(self):
        self.ensure_one()
        if not self.data_code:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {'message': _('No data code to run.'), 'type': 'warning'},
            }

        stripped = self.data_code.strip()
        indented = '\n'.join('    ' + line for line in stripped.splitlines())
        fn_code = f'def _chart_fn():\n{indented}\n'
        _safe_json = wrap_module(json, ['dumps', 'loads', 'JSONDecodeError'])
        _safe_re = wrap_module(re, ['compile', 'match', 'search', 'findall', 'sub', 'split',
                                     'escape', 'IGNORECASE', 'MULTILINE', 'DOTALL', 'error'])
        _safe_math = wrap_module(math, ['floor', 'ceil', 'sqrt', 'log', 'log10', 'exp', 'pow',
                                         'fabs', 'pi', 'e', 'inf', 'nan', 'isnan', 'isinf'])
        fn_globals = {
            'env': self.env,
            'user': self.env.user,
            'datetime': datetime,
            'date': date,
            'timedelta': timedelta,
            'defaultdict': defaultdict,
            'Counter': Counter,
            'json': _safe_json,
            're': _safe_re,
            'math': _safe_math,
        }
        try:
            safe_eval(fn_code, fn_globals, mode='exec', nocopy=True)
            options = fn_globals['_chart_fn']()
            self.options = json.dumps(options) if isinstance(options, dict) else (options or '{}')
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {'message': _('Error: %s') % str(e), 'type': 'danger', 'sticky': True},
            }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': _('Chart refreshed successfully.'), 'type': 'success'},
        }
