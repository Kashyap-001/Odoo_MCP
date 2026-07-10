import secrets
import base64
import json
import math
import re
import datetime as dt_module
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval, wrap_module

from ..mcp.tools.dispatcher import _render_via_odoo_report_layout

# Odoo brand palette — keep in sync with THEMES.default.colors in charts_gallery.js.
# Used as the color fallback only when a chart has never been explicitly saved
# with a color of its own (see action_refresh_chart).
_ODOO_DEFAULT_COLORS = ['#714B67', '#D9A441', '#2E5F5C', '#A9727C', '#8F5C82', '#C9A0A6', '#6B8E9E', '#4A2F44']
# Keys captured into default_style_snapshot on every create()/write() to
# options, and restored on every refresh — so the most recent explicit save
# (Style Editor, manual edit, AI update) is always what refresh rolls back to.
_STYLE_SNAPSHOT_KEYS = ('series', 'xAxis', 'yAxis', 'radar', 'color')
# Of those, these are the type-defining (structural) ones with no sensible
# universal fallback — if none of them were ever saved, refresh leaves
# data_code's freshly generated shape alone instead of stripping it.
_STRUCTURAL_SNAPSHOT_KEYS = ('series', 'xAxis', 'yAxis', 'radar')


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
    dashboard_id = fields.Many2one(
        'mcp.dashboard', string=_('Dashboard'), ondelete='restrict',
        default=lambda self: self.env.ref('mcp_gateway.mcp_dashboard_uncategorized', raise_if_not_found=False),
        help=_('Which dashboard page this chart appears under in the Chart Gallery.'),
    )
    is_shared = fields.Boolean(
        string=_('Shared'), default=False,
        help=_('Visible to every AI Gateway User, not just its creator (managers/admins '
               'always see everything regardless of this flag). Unrelated to is_public, '
               'which controls the external, unauthenticated internet share link — this '
               'only controls internal visibility between Odoo users. Defaults to private '
               '(2026-07-06) — only charts created before that stay shared by default.'),
    )
    default_style_snapshot = fields.Text(
        string=_('Current Style Snapshot'),
        help=_('Color and series/axis/radar shape from the most recent explicit '
               'save (Style Editor, manual edit, or AI update). Shifts to '
               'previous_style_snapshot on the NEXT explicit save — refresh '
               'restores from previous_style_snapshot, not this one, so refresh '
               'always goes back one step rather than re-confirming your latest save.'),
    )
    previous_style_snapshot = fields.Text(
        string=_('Previous Style Snapshot'),
        help=_('Color and series/axis/radar shape from the save BEFORE the most '
               'recent one. This is what action_refresh_chart() restores — refresh '
               'always goes back one step, not to the current/latest save. Falls '
               'back to the Odoo palette for color, or leaves the type as-is, if '
               'there is no previous save yet.'),
    )

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
            if not vals.get('default_style_snapshot'):
                try:
                    opts = json.loads(vals.get('options') or '{}')
                except (TypeError, ValueError):
                    opts = None
                if isinstance(opts, dict):
                    snapshot = {k: opts[k] for k in _STYLE_SNAPSHOT_KEYS if k in opts}
                    if snapshot:
                        vals['default_style_snapshot'] = json.dumps(snapshot)
        return super().create(vals_list)

    def write(self, vals):
        # Any EXPLICIT external write to options (Style Editor "Save Style", a
        # manual form edit, or the AI's update_record) shifts the two-slot
        # history: current -> previous, newly saved shape -> current. Refresh
        # restores from "previous" (not "current"), so it always goes back one
        # step rather than re-confirming your latest save — see action_refresh_chart.
        #
        # action_refresh_chart()'s own write explicitly includes
        # default_style_snapshot (unchanged) in its vals to signal "this isn't
        # a new explicit save, don't shift" — that's what the `not in vals`
        # guard below is for.
        if 'options' in vals and 'default_style_snapshot' not in vals:
            try:
                opts = json.loads(vals['options'] or '{}')
            except (TypeError, ValueError):
                opts = None
            if isinstance(opts, dict):
                new_snapshot = {k: opts[k] for k in _STYLE_SNAPSHOT_KEYS if k in opts}
                if new_snapshot:
                    for rec in self:
                        super(McpEchart, rec).write({'previous_style_snapshot': rec.default_style_snapshot})
                    vals = dict(vals, default_style_snapshot=json.dumps(new_snapshot))
        return super().write(vals)

    def copy_data(self, default=None):
        default = dict(default or {})
        default.setdefault('is_public', False)
        vals_list = super().copy_data(default=default)
        if default.get('name'):
            return vals_list
        return [dict(vals, name=_('%s (copy)', rec.name)) for rec, vals in zip(self, vals_list)]

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

    @api.model
    def action_export_chart_pdf(self, image_base64):
        """Wrap a client-captured chart PNG (data URL or bare base64) in
        Odoo's own report layout (company header/footer, same as
        generate_export_file's PDF branch in mcp/tools/dispatcher.py — see
        _render_via_odoo_report_layout there) and convert to PDF. Deliberately
        @api.model (not tied to a specific record): the client already has the
        rendered image, this only needs to turn it into a downloadable PDF, so
        it works equally for AI-created charts and any options-only snapshot
        with no saved mcp.echart record at all. Landscape — charts are wider
        than they are tall, portrait would just shrink them further. The
        client decides the filename (it already has the chart's title), so
        this only returns the PDF bytes."""
        if ',' in image_base64:
            image_base64 = image_base64.split(',', 1)[1]
        content_html = f'<div class="text-center"><img src="data:image/png;base64,{image_base64}" style="max-width:100%;"/></div>'
        pdf_bytes = _render_via_odoo_report_layout(self.env, content_html, landscape=True)
        return {
            'pdf_base64': base64.b64encode(pdf_bytes).decode('ascii'),
        }

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
            if isinstance(options, dict):
                try:
                    snapshot = json.loads(self.previous_style_snapshot or '{}')
                except (TypeError, ValueError):
                    snapshot = {}
                if not isinstance(snapshot, dict):
                    snapshot = {}

                # Refresh always goes back ONE step: it restores the color
                # from the save BEFORE the most recent one (previous_style_
                # snapshot), not the current/latest save — see write() for how
                # the two-slot current/previous history shifts. A chart with
                # no previous save yet falls back to the Odoo palette.
                options['color'] = snapshot.get('color') or list(_ODOO_DEFAULT_COLORS)

                # Same idea for the type-defining (structural) keys, but with
                # no sensible universal fallback: only restore/strip them if
                # the snapshot actually has at least one. Without this guard,
                # a color-only (or empty) snapshot would make every structural
                # key "not in" it and get popped, stripping the series
                # data_code just freshly generated (blank chart).
                if any(key in snapshot for key in _STRUCTURAL_SNAPSHOT_KEYS):
                    for key in _STRUCTURAL_SNAPSHOT_KEYS:
                        if key in snapshot:
                            options[key] = snapshot[key]
                        else:
                            options.pop(key, None)
                try:
                    old_options = json.loads(self.options or '{}')
                except (TypeError, ValueError):
                    old_options = {}
                if isinstance(old_options, dict) and 'backgroundColor' in old_options:
                    options['backgroundColor'] = old_options['backgroundColor']
                # Explicitly include default_style_snapshot (unchanged) so
                # write()'s guard treats this as a refresh, not a new explicit
                # save — restoring "previous" must not shift the two-slot
                # history further, or a second refresh would go back another
                # step instead of staying put.
                self.write({
                    'options': json.dumps(options),
                    'default_style_snapshot': self.default_style_snapshot,
                })
            else:
                self.options = options or '{}'
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
