"""
mcp_gateway/mcp/tools/builtin_tools.py

Generic and Developer Odoo MCP Tools.
Defines 18 tools mapping to dynamic ORM, code inspection, safe_eval scripting,
visual dashboards, webapp compilation, package installers, and subagents.
"""

import json

BUILTIN_TOOLS = [
    # ════════════════════════════════════════════════════════════════
    # CATEGORY 1: READ TOOLS (Auto-Enabled)
    # ════════════════════════════════════════════════════════════════
    {
        'name': 'list_models',
        'display_name_label': 'List Models',
        'description': 'Discover installed Odoo models in the database. Supports regex filter on technical model names.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'list_models',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'regex': {'type': 'string', 'description': 'Regex filter for model name (e.g. "^sale\\.")'},
            },
        }),
        'output_sample': '[{"model": "sale.order", "name": "Sales Order"}]',
    },

    {
        'name': 'get_model_schema',
        'display_name_label': 'Get Model Schema',
        'description': 'Retrieve complete schema definition of an Odoo model, including fields, field types, labels, and relational parameters.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'get_model_schema',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Target Odoo model (e.g. "res.partner")'},
                'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Specific fields to inspect'},
            },
            'required': ['model'],
        }),
        'output_sample': '{"name": {"type": "char", "string": "Name"}, "partner_id": {"type": "many2one", "relation": "res.partner"}}',
    },

    {
        'name': 'search_read',
        'display_name_label': 'Search Records',
        'description': 'Query Odoo database records using filters, field lists, pagination limits, and ordering.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'search_read',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name (e.g. "crm.lead")'},
                'domain': {'type': 'array', 'description': 'Odoo domain array (e.g. [["state", "=", "draft"]])'},
                'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Fields to return'},
                'limit': {'type': 'integer', 'description': 'Max records to fetch (default: 10, max: 100)', 'default': 10},
                'order': {'type': 'string', 'description': 'Order by field (e.g. "write_date desc")'},
            },
            'required': ['model'],
        }),
        'output_sample': '[{"id": 1, "name": "Acme Lead", "probability": 0.5}]',
    },

    {
        'name': 'read_record',
        'display_name_label': 'Read Record Details',
        'description': 'Retrieve all or specific fields of a single database record by its primary ID.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'read_record',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name (e.g. "res.partner")'},
                'res_id': {'type': 'integer', 'description': 'Record primary database ID'},
                'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Specific fields to read'},
            },
            'required': ['model', 'res_id'],
        }),
        'output_sample': '{"id": 1, "name": "Acme Inc", "email": "info@acme.com"}',
    },

    {
        'name': 'read_group',
        'display_name_label': 'Aggregate Records (Group By)',
        'description': 'Group and aggregate database fields, generating sums or counts grouped by keys.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'read_group',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name (e.g. "sale.order")'},
                'domain': {'type': 'array', 'description': 'Odoo domain array filter'},
                'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Aggregated fields (e.g. ["price_total:sum"])'},
                'groupby': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Fields to group by (e.g. ["date_order:month"])'},
                'limit': {'type': 'integer', 'description': 'Max group records'},
                'order': {'type': 'string', 'description': 'Sorting parameters'},
            },
            'required': ['model', 'fields', 'groupby'],
        }),
        'output_sample': '[{"date_order_count": 5, "price_total": 4500.0, "date_order:month": "May 2026"}]',
    },

    # ════════════════════════════════════════════════════════════════
    # CATEGORY 2: WRITE TOOLS (Allowlist Required)
    # ════════════════════════════════════════════════════════════════
    {
        'name': 'create_record',
        'display_name_label': 'Create Record',
        'description': 'Insert a new record in an Odoo model. Relational fields accept Command commands.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'create_record',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name (e.g. "sale.order")'},
                'values': {'type': 'object', 'description': 'Key-value dictionary mapping fields to create values'},
            },
            'required': ['model', 'values'],
        }),
        'output_sample': '{"id": 42}',
    },

    {
        'name': 'update_record',
        'display_name_label': 'Update Record(s)',
        'description': 'Modify write properties of one or more records in an Odoo model by record IDs.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'update_record',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name (e.g. "res.partner")'},
                'res_ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Target record database IDs'},
                'values': {'type': 'object', 'description': 'Key-value updates'},
            },
            'required': ['model', 'res_ids', 'values'],
        }),
        'output_sample': '{"success": true}',
    },

    {
        'name': 'delete_record',
        'display_name_label': 'Delete Record(s)',
        'description': 'Delete one or more records from an Odoo model by record IDs.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'delete_record',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name'},
                'res_ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Database IDs to unlink'},
            },
            'required': ['model', 'res_ids'],
        }),
        'output_sample': '{"success": true}',
    },

    {
        'name': 'execute_method',
        'display_name_label': 'Execute Model Method',
        'description': 'Invoke a custom Odoo workflow method or action (e.g. action_confirm) on a specific recordset.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'execute_method',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'model': {'type': 'string', 'description': 'Technical model name'},
                'res_ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Target record database IDs'},
                'method': {'type': 'string', 'description': 'Method name to call (e.g. "action_confirm")'},
                'args': {'type': 'array', 'description': 'Positional method arguments'},
                'kwargs': {'type': 'object', 'description': 'Keyword method arguments'},
            },
            'required': ['model', 'res_ids', 'method'],
        }),
        'output_sample': '{"result": null}',
    },

    # ════════════════════════════════════════════════════════════════
    # CATEGORY 3: CODE TOOLS (Allowlist Required)
    # ════════════════════════════════════════════════════════════════
    {
        'name': 'code_search',
        'display_name_label': 'Search Addons Code',
        'description': 'Search within the workspace addons code files for regex string patterns.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'code_search',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Pattern regex to search for'},
                'path_filter': {'type': 'string', 'description': 'Glob pattern to filter paths (e.g., "*.py")'},
            },
            'required': ['query'],
        }),
        'output_sample': '[{"file": "models/res_partner.py", "line": 42, "content": "class ResPartner(models.Model):"}]',
    },

    {
        'name': 'code_read',
        'display_name_label': 'Read Code File',
        'description': 'View contents of a workspace python/XML/CSV file page by page.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'code_read',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'filepath': {'type': 'string', 'description': 'Relative path to file in project'},
                'start_line': {'type': 'integer', 'description': 'Start line (1-indexed)', 'default': 1},
                'end_line': {'type': 'integer', 'description': 'End line (1-indexed)', 'default': 100},
            },
            'required': ['filepath'],
        }),
        'output_sample': '"class Partner(models.Model):\\n    _inherit = \'res.partner\'"',
    },

    # ════════════════════════════════════════════════════════════════
    # CATEGORY 4: ADVANCED / SANDBOX TOOLS (High Risk)
    # ════════════════════════════════════════════════════════════════
    {
        'name': 'execute_orm',
        'display_name_label': 'Execute Backend Script (safe_eval)',
        'description': 'Evaluate arbitrary python script blocks utilizing Odoo backend environment in safe_eval. Do NOT use python "import" statements (allowed modules: base64, io, xlrd, openpyxl, re, json, datetime, math are preloaded and available directly). Do NOT use python "with" statements (they are forbidden by Odoo safe_eval; write explicit assignments and close manually instead).',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'execute_orm',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'code': {'type': 'string', 'description': 'Python block to execute (expose env, user, Command, base64, io, BytesIO, StringIO, xlrd, openpyxl, json, re, math)'},
            },
            'required': ['code'],
        }),
        'output_sample': '{"result": 42}',
    },

    {
        'name': 'create_echart',
        'display_name_label': 'Create EChart Dashboard Widget',
        'description': 'Generate and save a visual Apache ECharts widget in Odoo database.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'create_echart',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Chart title shown in MCP Charts app'},
                'data_code': {'type': 'string', 'description': 'Python script (env, user available) that queries Odoo ORM and returns a complete ECharts options dict. Must end with: return {...}'},
            },
            'required': ['name', 'data_code'],
        }),
        'output_sample': '{"id": 1, "name": "June Sales"}',
    },

    {
        'name': 'ai_agent_query',
        'display_name_label': 'Delegate Query to AI Agent',
        'description': 'Delegate queries or analytics requests to another configured database AI subagent (with recursion protection).',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'ir.model',
        'odoo_method': 'ai_agent_query',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'agent_id': {'type': 'integer', 'description': 'Relational ID of target mcp.agent'},
                'prompt': {'type': 'string', 'description': 'Prompt instructions'},
            },
            'required': ['agent_id', 'prompt'],
        }),
        'output_sample': '"Response from delegated Agent..."',
    },
]
