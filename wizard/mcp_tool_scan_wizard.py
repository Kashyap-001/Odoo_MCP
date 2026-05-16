"""
mcp_gateway/wizard/mcp_tool_scan_wizard.py

Tool scanner wizard for auto-discovering Odoo model methods.

Key classes:
  ToolScanWizard — Scan installed modules and discover Odoo tools
  ToolScanLine — Individual tool candidates for registration

Developer notes:
  - Scans env.registry for model classes
  - Suggests search_read, create, write methods as tools
  - Marks existing tools to prevent duplicates
  - Allows batch registration of multiple tools
"""

import logging
from odoo import fields, models, _, exceptions

_logger = logging.getLogger(__name__)


class ToolScanWizard(models.TransientModel):
    """
    Tool Scan Wizard (mcp.tool.scan.wizard)

    Auto-discover Odoo model methods and register as tools.
    Simplifies tool setup for new users.

    Lifecycle:
      1. User clicks "Scan Tools"
      2. Wizard scans installed modules
      3. Shows candidate models/methods
      4. User selects which to register
      5. Wizard creates mcp.tool records
    """

    _name = 'mcp.tool.scan.wizard'
    _description = _('Tool Scanner')

    module_filter = fields.Char(
        string=_('Module Filter'),
        help=_('Scan only specific module (e.g., "sale", "crm")'),
    )
    scanned_line_ids = fields.One2many(
        comodel_name='mcp.tool.scan.line',
        inverse_name='wizard_id',
        string=_('Scanned Tools'),
    )

    def action_scan(self):
        """
        Scan installed modules for Odoo model tools.

        Introspects registry and populates scanned_line_ids.
        Marks already-registered tools.

        Returns:
            None (updates scanned_line_ids)
        """
        self.scanned_line_ids = [(5, 0, 0)]  # Clear existing

        try:
            registry = self.env.registry
            lines_to_create = []

            for model_name in sorted(registry.models.keys()):
                # Skip internal models
                if model_name.startswith('_'):
                    continue
                if model_name in ('ir.model', 'ir.model.fields'):
                    continue

                model = self.env[model_name]
                model_string = model._description

                # Check for common methods
                for method_name in ['search_read', 'create', 'write', 'action_confirm']:
                    if hasattr(model, method_name):
                        tool_name = f'{model_name.replace(".", "_")}_{method_name}'

                        # Check if already exists
                        exists = self.env['mcp.tool'].search([('name', '=', tool_name)])

                        lines_to_create.append((0, 0, {
                            'model_name': model_name,
                            'model_description': model_string,
                            'method': method_name,
                            'already_exists': bool(exists),
                            'selected': not bool(exists),  # Auto-select new ones
                        }))

            if lines_to_create:
                self.write({'scanned_line_ids': lines_to_create})
                _logger.info('Tool scan found %d candidates', len(lines_to_create))
            else:
                raise exceptions.UserError(_('No tools found to scan'))

        except Exception as e:
            _logger.error('Tool scan error: %s', str(e))
            raise exceptions.UserError(_('Scan failed: %s') % str(e))

    def action_create_selected(self):
        """
        Create mcp.tool records for selected scan results.

        For each selected line, creates a tool with basic config.

        Returns:
            dict: Notification action with result count
        """
        selected_lines = self.scanned_line_ids.filtered(
            lambda l: l.selected and not l.already_exists
        )

        if not selected_lines:
            raise exceptions.UserError(_('No tools selected'))

        tools_created = 0
        for line in selected_lines:
            try:
                tool_name = f'{line.model_name.replace(".", "_")}_{line.method}'

                # Auto-set tool config based on method type
                is_readonly = line.method in ('search_read', 'read', 'search')
                requires_confirm = line.method in ('create', 'write', 'unlink', 'action_confirm')

                # Get or create category
                category = self.env['mcp.tool.category'].search(
                    [('name', '=', 'Other')], limit=1
                )
                if not category:
                    category = self.env['mcp.tool.category'].create({'name': 'Other'})

                self.env['mcp.tool'].create({
                    'name': tool_name,
                    'display_name_label': f'{line.model_description} — {line.method}',
                    'description': f'Auto-discovered tool for {line.model_name}.{line.method}',
                    'category_id': category.id,
                    'tool_type': 'odoo',
                    'odoo_model': line.model_name,
                    'odoo_method': line.method,
                    'is_readonly': is_readonly,
                    'requires_confirm': requires_confirm,
                    'input_schema': '{}',
                })

                tools_created += 1
                _logger.info('Created tool: %s', tool_name)

            except Exception as e:
                _logger.warning('Failed to create tool %s: %s', line.model_name, str(e))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Created %d new tools') % tools_created,
                'sticky': False,
            }
        }


class ToolScanLine(models.TransientModel):
    """
    Tool Scan Line (mcp.tool.scan.line)

    Represents a candidate tool from module scanning.
    """

    _name = 'mcp.tool.scan.line'
    _description = _('Tool Scan Candidate')

    wizard_id = fields.Many2one(
        comodel_name='mcp.tool.scan.wizard',
        string=_('Wizard'),
        required=True,
        ondelete='cascade',
    )
    model_name = fields.Char(
        string=_('Model Name'),
        readonly=True,
    )
    model_description = fields.Char(
        string=_('Model Description'),
        readonly=True,
    )
    method = fields.Selection(
        [
            ('search_read', 'search_read'),
            ('create', 'create'),
            ('write', 'write'),
            ('action_confirm', 'action_confirm'),
        ],
        string=_('Method'),
        readonly=True,
    )
    selected = fields.Boolean(
        string=_('Selected'),
        default=True,
    )
    already_exists = fields.Boolean(
        string=_('Already Exists'),
        readonly=True,
    )
