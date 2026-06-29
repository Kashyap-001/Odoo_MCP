"""
mcp_gateway/mcp/tools/dispatcher.py

Tool call dispatcher — routes calls to Odoo ORM, HTTP APIs, or MCP servers.
Supports the full 18 specs representing Odoo ORM, file lookups, safe_eval scripting,
visual graphs, webapps, hot-loading modules, and subagents.
"""

import logging
import json
import re
import os
import subprocess
import requests
import base64
import urllib.request as _urllib_req
import hashlib
import hmac
import math
import itertools
import datetime as dt_module
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter
import threading

from odoo.fields import Command
from odoo import exceptions
from odoo.tools.safe_eval import safe_eval, wrap_module

_logger = logging.getLogger(__name__)

_safe_json = wrap_module(json, ['dumps', 'loads', 'JSONDecodeError'])
_safe_re = wrap_module(re, ['compile', 'match', 'search', 'findall', 'finditer', 'sub', 'split',
                             'escape', 'fullmatch', 'IGNORECASE', 'I', 'MULTILINE', 'M',
                             'DOTALL', 'S', 'VERBOSE', 'X', 'error'])
_safe_math = wrap_module(math, ['floor', 'ceil', 'sqrt', 'log', 'log2', 'log10', 'exp', 'pow',
                                 'fabs', 'factorial', 'gcd', 'pi', 'e', 'inf', 'nan',
                                 'isnan', 'isinf', 'isfinite', 'trunc', 'degrees', 'radians',
                                 'sin', 'cos', 'tan', 'atan', 'atan2', 'hypot'])
_safe_itertools = wrap_module(itertools, [
    'chain', 'groupby', 'islice', 'product', 'combinations',
    'permutations', 'starmap', 'takewhile', 'dropwhile', 'zip_longest',
])
_safe_hashlib = wrap_module(hashlib, ['md5', 'sha1', 'sha256', 'sha512', 'new', 'pbkdf2_hmac'])
_safe_hmac = wrap_module(hmac, ['new', 'digest', 'compare_digest'])

import io as _io
import xlrd as _xlrd
import openpyxl as _openpyxl

_safe_base64 = wrap_module(base64, ['b64encode', 'b64decode', 'encodebytes', 'decodebytes'])
_safe_io = wrap_module(_io, ['BytesIO', 'StringIO'])
_safe_xlrd = wrap_module(_xlrd, ['open_workbook', 'xldate_as_datetime'])
_safe_openpyxl = wrap_module(_openpyxl, ['load_workbook'])

# Module root resolved from __file__ so code tools work on any machine
_MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Runtime addons paths — populated by Odoo at startup, correct even when system odoo is importable
try:
    import odoo.addons as _odoo_addons_pkg
    _ODOO_ADDONS_PATHS = list(_odoo_addons_pkg.__path__)
except Exception:
    _ODOO_ADDONS_PATHS = []



# pantalytics: avatar_* auto-redirect to image_1920
_AVATAR_FIELD_RE = re.compile(r'^avatar_(128|256|512|1024|1920)$')

# tuanle96: model rename history dict (copied from tools_diagnostics.py)
_MODEL_HISTORY = {
    'account.invoice': 'account.move',
    'account.invoice.line': 'account.move.line',
    'hr.holidays': 'hr.leave',
    'hr.holidays.status': 'hr.leave.type',
    'hr.holidays.allocation': 'hr.leave.allocation',
    'stock.quant.move': 'stock.move',
    'crm.case.categ': 'crm.tag',
    'product.attribute.value': 'product.attribute.value',
    'sale.order.line': 'sale.order.line',
}

_BULK_MAX = 1000  # pantalytics cap for bulk operations


def _make_serializable(obj):
    """Recursively convert ORM results to JSON-safe types."""
    if isinstance(obj, (dt_module.date, dt_module.datetime)):
        return obj.isoformat()
    if isinstance(obj, tuple):  # Many2one from read_group returns (id, 'name')
        return [_make_serializable(x) for x in obj]
    if isinstance(obj, list):
        return [_make_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if hasattr(obj, 'ids'):  # recordset
        return obj.ids
    if isinstance(obj, bytes):  # binary fields (images, attachments)
        return None
    return obj


def _normalize_domain(domain):
    # LLMs sometimes double-wrap: [[["f","=","v"]]] → [["f","=","v"]]
    if domain and isinstance(domain[0], list) and domain[0] and isinstance(domain[0][0], list):
        return domain[0]
    return domain


class ToolDispatcher:
    """
    Routes tool calls to the correct backend execution environment.
    """

    def dispatch(self, tool, arguments: dict, env, user) -> str:
        """
        Execute a tool call and return result as JSON string.
        """
        _logger.info('DISPATCHER.dispatch called: tool=%s, args=%s', tool.name, arguments)
        try:
            if tool.tool_type == 'odoo':
                return self._dispatch_odoo(tool, arguments, env, user)
            elif tool.tool_type == 'external':
                return self._dispatch_http(tool, arguments)
            elif tool.tool_type == 'mcp_server':
                return self._dispatch_mcp_server(tool, arguments)
            else:
                raise ValueError(f'Unknown tool type: {tool.tool_type}')

        except Exception as e:
            _logger.error('Tool dispatch failed for %s: %s', tool.name, str(e))
            return json.dumps({
                'success': False,
                'error': str(e)[:500],
            })

    def _dispatch_odoo(self, tool, arguments: dict, env, user) -> str:
        """
        Execute tool on Odoo ORM.
        """
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError) as e:
                return json.dumps({'success': False, 'error': f'Invalid arguments format: {str(e)}'})

        if not isinstance(arguments, dict):
            return json.dumps({'success': False, 'error': f'Arguments must be a dict, got: {type(arguments).__name__}'})

        try:
            # ── Route by tool name directly (18 generic tools) ───────────────────
            name = tool.name
            result = None

            if name == 'list_models':
                result = self._dispatch_list_models(arguments, env, user)
            elif name == 'get_model_schema':
                result = self._dispatch_get_model_schema(arguments, env, user)
            elif name == 'search_read':
                result = self._dispatch_search_read(arguments, env, user)
            elif name == 'read_record':
                result = self._dispatch_read_record(arguments, env, user)
            elif name == 'read_group':
                result = self._dispatch_read_group(arguments, env, user)
            elif name == 'create_record':
                result = self._dispatch_create_record(arguments, env, user)
            elif name == 'update_record':
                result = self._dispatch_update_record(arguments, env, user)
            elif name == 'delete_record':
                result = self._dispatch_delete_record(arguments, env, user)
            elif name == 'execute_method':
                result = self._dispatch_execute_method(arguments, env, user)
            elif name == 'code_search':
                result = self._dispatch_code_search(arguments, env, user)
            elif name == 'code_read':
                result = self._dispatch_code_read(arguments, env, user)
            elif name == 'execute_orm':
                result = self._dispatch_execute_orm(arguments, env, user)
            elif name == 'post_message':
                result = self._dispatch_post_message(arguments, env, user)
            elif name == 'get_attachments':
                result = self._dispatch_get_attachments(arguments, env, user)
            elif name == 'read_attachment':
                result = self._dispatch_read_attachment(arguments, env, user)
            elif name == 'set_binary_field':
                result = self._dispatch_set_binary_field(arguments, env, user)
            elif name == 'upload_attachment':
                result = self._dispatch_upload_attachment(arguments, env, user)
            elif name == 'create_records':
                result = self._dispatch_create_records(arguments, env, user)
            elif name == 'update_records':
                result = self._dispatch_update_records(arguments, env, user)
            elif name == 'delete_records':
                result = self._dispatch_delete_records(arguments, env, user)
            elif name == 'lookup_model_history':
                result = self._dispatch_lookup_model_history(arguments, env, user)
            elif name == 'accounting_health_summary':
                result = self._dispatch_accounting_health_summary(arguments, env, user)
            elif name == 'import_from_file':
                result = self._dispatch_import_from_file(arguments, env, user)
            elif name == 'create_echart':
                result = self._dispatch_create_echart(arguments, env, user)
            elif name == 'ai_agent_query':
                result = self._dispatch_ai_agent_query(arguments, env, user)
            else:
                # Fallback for old custom model mapping methods
                model_name = tool.odoo_model
                method_name = tool.odoo_method
                model = env[model_name].sudo() if tool.sudo_execute else env[model_name].with_user(user)
                method = getattr(model, method_name)
                result = method(**arguments)

            return json.dumps({'success': True, 'result': result})

        except Exception as e:
            _logger.error('ORM tool run failed: %s', str(e))
            return json.dumps({'success': False, 'error': str(e)})

    # ════════════════════════════════════════════════════════════════
    # INDIVIDUAL ORM DISPATCH METHODS
    # ════════════════════════════════════════════════════════════════

    def _dispatch_list_models(self, arguments, env, user):
        regex = arguments.get('regex')
        models = env['ir.model'].search([])
        if regex:
            pattern = re.compile(regex)
            models = models.filtered(lambda m: pattern.search(m.model))
        result = []
        for m in models:
            model_class = env.registry.get(m.model)
            if model_class and not model_class._abstract and not model_class._transient:
                result.append({'model': m.model, 'name': m.name})
        return result

    # Mixin field prefixes that are usually noise when the AI is looking for data fields
    _SCHEMA_NOISE_PREFIXES = ('activity_', 'message_', 'website_message_', 'mail_')

    def _dispatch_get_model_schema(self, arguments, env, user):
        model_name = arguments['model']
        fields = arguments.get('fields')
        model = env.get(model_name)
        if model is None:
            raise ValueError(f"Model {model_name} does not exist.")
        result = model.with_user(user).fields_get(fields, ['type', 'string', 'help', 'relation', 'selection'])
        if fields:
            return result
        # Put noisy mixin fields at the end so the AI finds data fields first
        primary = {k: v for k, v in result.items() if not any(k.startswith(p) for p in self._SCHEMA_NOISE_PREFIXES)}
        noise = {k: v for k, v in result.items() if any(k.startswith(p) for p in self._SCHEMA_NOISE_PREFIXES)}
        return {**primary, **noise}

    _AUDIT_FIELDS = frozenset(['create_uid', 'write_uid', 'create_date', 'write_date', '__last_update'])

    def _dispatch_search_read(self, arguments, env, user):
        model_name = arguments['model']
        domain = _normalize_domain(arguments.get('domain', []))
        fields = arguments.get('fields', [])
        limit = min(arguments.get('limit', 10), 100)
        order = arguments.get('order')
        explicit_fields = bool(fields)

        model = env[model_name].with_user(user)
        records = model.search_read(domain, fields, limit=limit, order=order)
        # Detect binary fields so we can swap bytes→URL instead of bytes→None
        binary_fields = {f for f in fields if f in model._fields and model._fields[f].type == 'binary'} if fields else set()
        result = []
        for rec in records:
            s = _make_serializable(rec)
            rec_id = s.get('id')
            if rec_id and binary_fields:
                for bf in binary_fields:
                    if s.get(bf) is None:  # bytes were stripped by _make_serializable
                        s[bf] = f'/web/image/{model_name}/{rec_id}/{bf}'
            # ponytail: strip audit noise when AI didn't specify fields — keeps responses clean
            if not explicit_fields:
                for af in self._AUDIT_FIELDS:
                    s.pop(af, None)
            result.append(s)
        return result

    def _dispatch_read_record(self, arguments, env, user):
        model_name = arguments['model']
        res_id = arguments['res_id']
        fields = arguments.get('fields', [])

        record = env[model_name].with_user(user).browse(res_id)
        if not record.exists():
            raise ValueError(f"Record {res_id} not found in model {model_name}.")

        return _make_serializable(record.read(fields)[0])

    def _dispatch_read_group(self, arguments, env, user):
        model_name = arguments['model']
        domain = _normalize_domain(arguments.get('domain', []))
        fields = arguments['fields']
        groupby = arguments['groupby']
        limit = arguments.get('limit')
        orderby = arguments.get('order') or arguments.get('orderby')

        model = env[model_name].with_user(user)
        kwargs = {'limit': limit}
        if orderby:
            kwargs['orderby'] = orderby
        rows = model.read_group(domain, fields, groupby, **kwargs)
        return [_make_serializable(row) for row in rows]

    def _dispatch_create_record(self, arguments, env, user):
        model_name = arguments['model']
        values = arguments['values']

        processed_vals = self._prepare_create_values(values, model_name, env)
        if isinstance(processed_vals, dict) and 'error' in processed_vals:
            raise ValueError(processed_vals['error'])

        model = env[model_name].with_user(user)
        record = model.create(processed_vals)
        return {'id': record.id, 'model': model_name}

    def _dispatch_update_record(self, arguments, env, user):
        model_name = arguments['model']
        res_ids = arguments['res_ids']
        values = arguments['values']
        
        processed_vals = self._prepare_create_values(values, model_name, env)
        if isinstance(processed_vals, dict) and 'error' in processed_vals:
            raise ValueError(processed_vals['error'])
            
        records = env[model_name].with_user(user).browse(res_ids)
        records.write(processed_vals)
        return True

    def _dispatch_delete_record(self, arguments, env, user):
        model_name = arguments['model']
        res_ids = arguments['res_ids']
        
        records = env[model_name].with_user(user).browse(res_ids)
        return records.unlink()

    def _dispatch_execute_method(self, arguments, env, user):
        model_name = arguments['model']
        res_ids = arguments['res_ids']
        method_name = arguments['method']
        args = arguments.get('args', [])
        kwargs = arguments.get('kwargs', {})
        
        if method_name.startswith('_'):
            raise PermissionError("Private method execution is strictly forbidden.")
            
        records = env[model_name].with_user(user).browse(res_ids)
        if not hasattr(records, method_name):
            raise AttributeError(f"Method {method_name} does not exist on {model_name}.")

        result = getattr(records, method_name)(*args, **kwargs)
        return _make_serializable(result)

    def _dispatch_code_search(self, arguments, env, user):
        query = arguments['query']
        path_filter = arguments.get('path_filter')

        search_roots = [_MODULE_ROOT] + [p for p in _ODOO_ADDONS_PATHS if os.path.isdir(p)]

        all_matches = []
        for root_path in search_roots:
            cmd = ['rg', '--json', query, root_path]
            if path_filter:
                cmd.extend(['-g', path_filter])
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                for line in res.stdout.splitlines():
                    try:
                        data = json.loads(line)
                        if data.get('type') == 'match':
                            val = data['data']
                            rel_path = os.path.relpath(val['path']['text'], root_path)
                            all_matches.append({
                                'file': rel_path,
                                'line': val['line_number'],
                                'content': val['submatches'][0]['match']['text']
                            })
                    except Exception:
                        continue
            except Exception:
                pattern = re.compile(query)
                for walk_root, dirs, files in os.walk(root_path):
                    for f in files:
                        if path_filter and not f.endswith(path_filter.replace('*', '')):
                            continue
                        full_p = os.path.join(walk_root, f)
                        try:
                            with open(full_p, 'r', errors='ignore') as fp:
                                for idx, ln in enumerate(fp):
                                    if pattern.search(ln):
                                        all_matches.append({
                                            'file': os.path.relpath(full_p, root_path),
                                            'line': idx + 1,
                                            'content': ln.strip()
                                        })
                                        if len(all_matches) >= 50:
                                            return all_matches
                        except Exception:
                            continue
            if len(all_matches) >= 50:
                break
        return all_matches[:50]

    def _dispatch_code_read(self, arguments, env, user):
        filepath = arguments['filepath']
        start_line = arguments.get('start_line', 1)
        end_line = arguments.get('end_line', 100)

        target_path = None

        # 1. Custom module root (e.g. mcp_gateway itself)
        candidate = os.path.abspath(os.path.join(_MODULE_ROOT, filepath))
        if candidate.startswith(_MODULE_ROOT) and os.path.exists(candidate):
            target_path = candidate

        # 2. Runtime addons paths — strip leading 'addons/' if present
        if not target_path:
            stripped = filepath[7:] if filepath.startswith('addons/') else filepath
            for addons_dir in _ODOO_ADDONS_PATHS:
                candidate = os.path.abspath(os.path.join(addons_dir, stripped))
                if os.path.exists(candidate):
                    target_path = candidate
                    break

        if target_path is None:
            raise FileNotFoundError(f"File {filepath} not found.")

        with open(target_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        return "".join(lines[start_line - 1 : end_line])

    def _dispatch_post_message(self, arguments, env, user):
        model = arguments['model']
        record_id = int(arguments['record_id'])
        body = arguments['body']
        message_type = arguments.get('message_type', 'comment')
        subtype_xmlid = 'mail.mt_note' if message_type == 'internal' else 'mail.mt_comment'
        record = env[model].browse(record_id)
        msg = record.message_post(body=body, message_type=message_type, subtype_xmlid=subtype_xmlid)
        return {'message_id': msg.id, 'model': model, 'record_id': record_id}

    def _dispatch_get_attachments(self, arguments, env, user):
        model = arguments['model']
        record_id = int(arguments['record_id'])
        attachments = env['ir.attachment'].search_read(
            [('res_model', '=', model), ('res_id', '=', record_id)],
            ['id', 'name', 'mimetype', 'file_size', 'create_date']
        )
        for att in attachments:
            att['url'] = f'/web/content/{att["id"]}?download=true'
        return {'model': model, 'record_id': record_id, 'attachments': attachments, 'count': len(attachments)}

    def _dispatch_upload_attachment(self, arguments, env, user):
        att = env['ir.attachment'].create({
            'name': arguments['filename'],
            'res_model': arguments['model'],
            'res_id': int(arguments['record_id']),
            'mimetype': arguments.get('mimetype', 'application/octet-stream'),
            'datas': arguments['datas'],
        })
        return {'id': att.id, 'name': att.name, 'model': arguments['model'], 'record_id': int(arguments['record_id'])}

    _MAX_ATTACHMENT_BYTES = 1 * 1024 * 1024  # 1 MiB — same cap as tuanle96/mcp-odoo

    def _dispatch_read_attachment(self, arguments, env, user):
        att_id = int(arguments['attachment_id'])
        include_data = arguments.get('include_data', True)
        rows = env['ir.attachment'].search_read(
            [('id', '=', att_id)],
            ['id', 'name', 'mimetype', 'file_size', 'type', 'url', 'res_model', 'res_id']
        )
        if not rows:
            raise ValueError(f'Attachment not found: {att_id}')
        att = rows[0]
        data_base64 = None
        warnings = []
        file_size = int(att.get('file_size') or 0)
        if include_data and att.get('type') != 'url':
            if file_size > self._MAX_ATTACHMENT_BYTES:
                warnings.append(f'File is {file_size} bytes, over 1MB cap. Use the URL to download.')
            else:
                datas_rows = env['ir.attachment'].search_read([('id', '=', att_id)], ['datas'])
                raw = datas_rows[0].get('datas') if datas_rows else None
                if raw:
                    data_base64 = raw
        elif att.get('type') == 'url':
            warnings.append('URL-type attachment — use the url field directly.')
        att['url'] = f'/web/content/{att_id}?download=true'
        return {
            'attachment': att,
            'data_base64': data_base64,
            'data_included': data_base64 is not None,
            'warnings': warnings,
        }

    def _dispatch_set_binary_field(self, arguments, env, user):
        """pantalytics pattern: fetch from URL, write to binary/image field. Bytes never pass through LLM."""
        model = arguments['model']
        record_id = int(arguments['record_id'])
        field_name = arguments['field_name']
        source_url = arguments['source']

        # pantalytics: auto-redirect avatar_* to image_1920
        if _AVATAR_FIELD_RE.match(field_name):
            field_name = 'image_1920'

        field_meta = env[model].fields_get([field_name], ['type', 'readonly'])
        if field_name not in field_meta:
            raise ValueError(f'Field {field_name} not found on {model}')
        if field_meta[field_name].get('type') not in ('binary', 'image'):
            raise ValueError(f'Field {field_name} is not a binary/image field')
        if field_meta[field_name].get('readonly'):
            raise ValueError(f'Field {field_name} is readonly')

        # pantalytics: 25MB cap, 64KB chunks
        _MAX_BINARY = 25 * 1024 * 1024
        chunks = []
        total = 0
        req = _urllib_req.Request(source_url, headers={'User-Agent': 'mcp-gateway/1.0'})
        with _urllib_req.urlopen(req, timeout=30) as resp:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_BINARY:
                    raise ValueError('Source file exceeds 25MB limit')
                chunks.append(chunk)

        datas = base64.b64encode(b''.join(chunks)).decode('ascii')
        env[model].browse(record_id).write({field_name: datas})
        return {'model': model, 'record_id': record_id, 'field': field_name, 'size_bytes': total}

    def _dispatch_create_records(self, arguments, env, user):
        """pantalytics bulk create — multiple records in one call."""
        model = arguments['model']
        vals_list = arguments['vals_list']
        if len(vals_list) > _BULK_MAX:
            raise ValueError(f'vals_list exceeds limit of {_BULK_MAX}')
        records = env[model].with_user(user).create(vals_list)
        return {'model': model, 'ids': records.ids, 'count': len(records)}

    def _dispatch_update_records(self, arguments, env, user):
        """pantalytics bulk update — same values written to multiple records."""
        model = arguments['model']
        record_ids = arguments['record_ids']
        values = arguments['values']
        if len(record_ids) > _BULK_MAX:
            raise ValueError(f'record_ids exceeds limit of {_BULK_MAX}')
        env[model].with_user(user).browse(record_ids).write(values)
        return {'model': model, 'record_ids': record_ids, 'count': len(record_ids)}

    def _dispatch_delete_records(self, arguments, env, user):
        """pantalytics bulk delete — multiple records unlinked in one call."""
        model = arguments['model']
        record_ids = arguments['record_ids']
        if len(record_ids) > _BULK_MAX:
            raise ValueError(f'record_ids exceeds limit of {_BULK_MAX}')
        env[model].with_user(user).browse(record_ids).unlink()
        return {'model': model, 'count': len(record_ids)}

    def _dispatch_lookup_model_history(self, arguments, env, user):
        """tuanle96 pattern: resolve outdated model names to current Odoo names."""
        model = arguments['model']
        if model in _MODEL_HISTORY:
            current = _MODEL_HISTORY[model]
            return {
                'queried': model,
                'current_name': current,
                'renamed': current != model,
                'note': f'Use "{current}" instead of "{model}"' if current != model else f'"{model}" is current',
            }
        exists = bool(env.registry.get(model))
        return {
            'queried': model,
            'current_name': model if exists else None,
            'renamed': False,
            'exists': exists,
            'note': 'Model exists and name is current' if exists else f'Model "{model}" not found — check spelling or use list_models.',
        }

    def _dispatch_accounting_health_summary(self, arguments, env, user):
        """tuanle96 pattern: quick AR/AP health check without multiple tool calls."""
        Move = env['account.move'].with_user(user)
        today = date.today().isoformat()
        ar = Move.search_read(
            [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
             ('payment_state', 'not in', ['paid', 'reversed'])],
            ['id', 'name', 'partner_id', 'amount_residual', 'invoice_date_due']
        )
        ap = Move.search_read(
            [('move_type', '=', 'in_invoice'), ('state', '=', 'posted'),
             ('payment_state', 'not in', ['paid', 'reversed'])],
            ['id', 'name', 'partner_id', 'amount_residual', 'invoice_date_due']
        )
        draft = Move.search_count([('move_type', 'in', ('out_invoice', 'in_invoice')), ('state', '=', 'draft')])
        ar_overdue = [r for r in ar if r.get('invoice_date_due') and r['invoice_date_due'] < today]
        ap_overdue = [r for r in ap if r.get('invoice_date_due') and r['invoice_date_due'] < today]
        return {
            'receivables': {
                'total_count': len(ar), 'overdue_count': len(ar_overdue),
                'total_amount': sum(r['amount_residual'] for r in ar),
                'overdue_amount': sum(r['amount_residual'] for r in ar_overdue),
            },
            'payables': {
                'total_count': len(ap), 'overdue_count': len(ap_overdue),
                'total_amount': sum(r['amount_residual'] for r in ap),
                'overdue_amount': sum(r['amount_residual'] for r in ap_overdue),
            },
            'draft_invoice_backlog': draft,
            'as_of': today,
        }

    def _dispatch_import_from_file(self, arguments, env, user):
        """Parse a staged ir.attachment (CSV or Excel) and load rows into Odoo via env[model].load()."""
        import base64 as _b64
        import csv as _csv
        import io as _io

        attachment_id = int(arguments['attachment_id'])
        model = arguments['model']
        has_header = arguments.get('has_header', True)

        rows = env['ir.attachment'].search_read(
            [('id', '=', attachment_id)],
            ['name', 'mimetype', 'datas', 'file_size']
        )
        if not rows:
            raise ValueError(f'Attachment not found: {attachment_id}')
        att = rows[0]
        if not att.get('datas'):
            raise ValueError('Attachment has no data')

        raw = _b64.b64decode(att['datas'])
        mimetype = (att.get('mimetype') or '').lower()
        name = att.get('name', '')

        if (mimetype in ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         'application/vnd.ms-excel')
                or name.lower().endswith(('.xlsx', '.xls'))):
            import openpyxl
            wb = openpyxl.load_workbook(filename=_io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            all_rows = [
                [str(cell.value) if cell.value is not None else '' for cell in row]
                for row in ws.iter_rows()
            ]
            wb.close()
        else:
            text = raw.decode('utf-8-sig')  # utf-8-sig strips BOM
            all_rows = list(_csv.reader(_io.StringIO(text)))

        if not all_rows:
            raise ValueError('File is empty')

        if has_header:
            fields = all_rows[0]
            data = all_rows[1:]
        else:
            fields = [f'col{i}' for i in range(len(all_rows[0]))]
            data = all_rows

        if not data:
            raise ValueError('File has no data rows (only a header row was found)')

        result = env[model].with_user(user).load(fields, data)
        ids = result.get('ids') or []
        messages = result.get('messages') or []
        errors = [m for m in messages if m.get('type') in ('error', 'warning')]

        return {
            'model': model,
            'source_file': name,
            'fields': fields,
            'total_rows': len(data),
            'created_count': len(ids),
            'ids': ids[:100],
            'errors': errors[:20],
        }

    def _dispatch_execute_orm(self, arguments, env, user):
        code = arguments['code']

        # Strip ALL import lines — every needed module is pre-loaded in eval_context
        code = '\n'.join(
            line for line in code.splitlines()
            if not line.strip().startswith(('import ', 'from '))
        )

        def _read_excel(attachment_id, sheet=0):
            """Read xls/xlsx/ods attachment → list of row lists.
            attachment.datas is base64 in Odoo ORM; attachment.raw is the raw bytes."""
            import io as _real_io
            import zipfile as _zipfile
            import xml.etree.ElementTree as _ET
            att = env['ir.attachment'].browse(attachment_id)
            raw_b64 = att.datas
            if not raw_b64:
                raise ValueError(f"Attachment {attachment_id} has no data")
            raw = base64.b64decode(raw_b64)  # datas is ALWAYS base64 in Odoo ORM
            if raw[:4] == b'\xD0\xCF\x11\xE0':  # OLE2 magic → .xls
                wb = _xlrd.open_workbook(file_contents=raw)
                ws = wb.sheet_by_index(sheet)
                return [ws.row_values(r) for r in range(ws.nrows)]
            # ZIP-based: peek at content to distinguish xlsx vs ods
            try:
                with _zipfile.ZipFile(_real_io.BytesIO(raw)) as zf:
                    names = zf.namelist()
                if 'content.xml' in names:  # ODS
                    _NS_TABLE = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
                    _NS_TEXT  = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
                    with _zipfile.ZipFile(_real_io.BytesIO(raw)) as zf:
                        tree = _ET.parse(zf.open('content.xml'))
                    sheets = list(tree.getroot().iter(f'{{{_NS_TABLE}}}table'))
                    if not sheets:
                        return []
                    ws = sheets[min(sheet, len(sheets) - 1)]
                    rows = []
                    for row_el in ws.iter(f'{{{_NS_TABLE}}}table-row'):
                        cells = []
                        for cell in row_el:
                            tag = cell.tag.split('}')[-1]
                            if tag in ('table-cell', 'covered-table-cell'):
                                repeat = int(cell.get(f'{{{_NS_TABLE}}}number-columns-repeated', 1))
                                text = next((p.text or '' for p in cell.iter(f'{{{_NS_TEXT}}}p')), '')
                                cells.extend([text] * repeat)
                        # trim trailing blanks and skip empty rows
                        while cells and cells[-1] == '':
                            cells.pop()
                        if cells:
                            rows.append(cells)
                    return rows
                else:  # xlsx
                    wb = _openpyxl.load_workbook(_real_io.BytesIO(raw))
                    ws = wb.worksheets[min(sheet, len(wb.worksheets) - 1)]
                    return [list(row) for row in ws.iter_rows(values_only=True)]
            except _zipfile.BadZipFile:
                raise ValueError("Attachment is not a valid Excel/ODS file (bad ZIP)")

        eval_context = {
            'env': env,
            'user': user,
            'uid': user.id,
            'Command': Command,
            'json': _safe_json,
            're': _safe_re,
            'math': _safe_math,
            'itertools': _safe_itertools,
            'hashlib': _safe_hashlib,
            'hmac': _safe_hmac,
            'base64': _safe_base64,
            'io': _safe_io,
            'BytesIO': _safe_io.BytesIO,
            'StringIO': _safe_io.StringIO,
            'xlrd': _safe_xlrd,
            'openpyxl': _safe_openpyxl,
            'datetime': datetime,
            'date': date,
            'timedelta': timedelta,
            'defaultdict': defaultdict,
            'Counter': Counter,
            'read_excel': _read_excel,
            'print': print,
        }
        # Wrap in a function so multi-line code with assignments works.
        # Use AST to find the last Expr statement and insert return at its start line.
        import ast as _ast
        stripped = code.strip()
        try:
            _tree = _ast.parse(stripped)
            if _tree.body and isinstance(_tree.body[-1], _ast.Expr):
                _lines = stripped.splitlines()
                _start = _tree.body[-1].lineno - 1  # 0-indexed, points to opening line
                _lines[_start] = 'return ' + _lines[_start]
                stripped = '\n'.join(_lines)
        except SyntaxError:
            pass  # safe_eval will surface the error
        indented = '\n'.join('    ' + line for line in stripped.splitlines())
        fn_code = f'def _orm_fn():\n{indented}\n'
        safe_eval(fn_code, eval_context, mode='exec', nocopy=True)
        return eval_context['_orm_fn']()

    def _dispatch_create_echart(self, arguments, env, user):
        name = arguments['name']
        data_code = arguments.get('data_code', '')
        options = arguments.get('options', {})

        if data_code:
            stripped = data_code.strip()
            indented = '\n'.join('    ' + line for line in stripped.splitlines())
            fn_code = f'def _chart_fn():\n{indented}\n'
            fn_globals = {
                'env': env,
                'user': user,
                'datetime': datetime,
                'date': date,
                'timedelta': timedelta,
                'defaultdict': defaultdict,
                'Counter': Counter,
                'json': _safe_json,
                're': _safe_re,
                'math': _safe_math,
            }
            safe_eval(fn_code, fn_globals, mode='exec', nocopy=True)
            options = fn_globals['_chart_fn']()

        chart = env['mcp.echart'].with_user(user).create({
            'name': name,
            'data_code': data_code,
            'options': json.dumps(options) if isinstance(options, dict) else (options or '{}'),
        })
        return {'id': chart.id, 'name': name}

    def _dispatch_ai_agent_query(self, arguments, env, user):
        agent_id = arguments['agent_id']
        prompt = arguments['prompt']
        
        if not hasattr(threading.current_thread(), 'mcp_depth'):
            threading.current_thread().mcp_depth = 0
            
        if threading.current_thread().mcp_depth >= 3:
            raise exceptions.ValidationError("Maximum subagent query delegation depth exceeded (loop detected).")
            
        threading.current_thread().mcp_depth += 1
        try:
            agent = env['mcp.agent'].with_user(user).browse(agent_id)
            if not agent.exists():
                raise ValueError(f"Agent {agent_id} does not exist.")
            from ..gateway import McpGateway  # lazy import — avoids circular dependency
            gateway = McpGateway(env, user)
            result = gateway.run(agent_id=agent_id, user_message=prompt)
            return result.get('reply', '')
        finally:
            threading.current_thread().mcp_depth -= 1

    # ════════════════════════════════════════════════════════════════
    # LEGACY ORM VALUE FORMATTERS
    # ════════════════════════════════════════════════════════════════

    def _dispatch_http(self, tool, arguments: dict) -> str:
        try:
            url = tool.endpoint_url
            method = tool.http_method
            headers = self._build_auth_headers(tool)
            timeout = tool.timeout_seconds

            if method == 'GET':
                response = requests.get(url, params=arguments, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, json=arguments, headers=headers, timeout=timeout)
            elif method == 'PUT':
                response = requests.put(url, json=arguments, headers=headers, timeout=timeout)
            elif method == 'DELETE':
                response = requests.delete(url, params=arguments, headers=headers, timeout=timeout)
            else:
                raise ValueError(f'Unsupported HTTP method: {method}')

            response.raise_for_status()
            data = response.json()

            if tool.response_path:
                result = self._extract_path(data, tool.response_path)
            else:
                result = data

            return json.dumps({'success': True, 'result': result})

        except Exception as e:
            return json.dumps({'success': False, 'error': str(e)[:500]})

    def _dispatch_mcp_server(self, tool, arguments: dict) -> str:
        try:
            url = f'{tool.mcp_server_url}/call'
            headers = {
                'Authorization': f'Bearer {tool.mcp_server_key}',
                'Content-Type': 'application/json',
            }
            payload = {
                'tool': tool.name,
                'arguments': arguments,
            }

            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()

            return json.dumps({'success': True, 'result': response.json()})

        except Exception as e:
            return json.dumps({'success': False, 'error': str(e)[:500]})

    def _extract_path(self, data: dict, path: str):
        parts = path.split('.')
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    def _build_auth_headers(self, tool) -> dict:
        headers = {}
        if tool.auth_type == 'none':
            pass
        elif tool.auth_type == 'bearer':
            headers['Authorization'] = f'Bearer {tool.auth_value}'
        elif tool.auth_type == 'basic':
            encoded = base64.b64encode(tool.auth_value.encode()).decode()
            headers['Authorization'] = f'Basic {encoded}'
        elif tool.auth_type == 'api_key_header':
            headers[tool.auth_header_name] = tool.auth_value
        return headers

    def _prepare_create_values(self, arguments, model_name: str, env) -> dict:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError) as e:
                return {'error': f'Invalid arguments format: {str(e)}'}

        if not isinstance(arguments, dict):
            return {'error': f'Arguments must be a dict, got: {type(arguments).__name__}'}

        vals = {}
        model = env.get(model_name)
        if model is None:
            return {'error': f'Model {model_name} not found'}

        for key, value in arguments.items():
            if value is None:
                continue

            field = model._fields.get(key)
            if not field:
                vals[key] = value
                continue

            if field.type == 'datetime' and isinstance(value, str):
                value = self._parse_datetime(value)
            elif field.type == 'date' and isinstance(value, str):
                value = self._parse_date(value)
            elif field.type == 'many2one':
                if isinstance(value, list):
                    value = value[0] if value else False
                elif isinstance(value, str) and value.isdigit():
                    value = int(value)
            elif field.type in ('one2many', 'many2many'):
                if isinstance(value, list):
                    # Handle raw Odoo Command tuples directly
                    # e.g., [[0, 0, {...}]] or [[6, 0, [1, 2]]]
                    if value and isinstance(value[0], list):
                        # Construct Command tuples
                        cmd_list = []
                        for item in value:
                            if len(item) == 3:
                                cmd_list.append((item[0], item[1], item[2]))
                        value = cmd_list
                    elif value and isinstance(value[0], int):
                        value = [Command.set(value)]
                elif isinstance(value, str) and value:
                    try:
                        ids = [int(x.strip()) for x in value.split(',') if x.strip().isdigit()]
                        value = [Command.set(ids)]
                    except ValueError:
                        pass
            vals[key] = value
        return vals

    def _parse_datetime(self, value: str):
        if not isinstance(value, str):
            return value
        value = re.sub(r'[+-]\d{2}:?\d{2}$', '', value)
        value = re.sub(r'Z$', '', value)
        value = value.strip()
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%dT%H:%M',
            '%Y-%m-%d',
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return value

    def _parse_date(self, value: str):
        if not isinstance(value, str):
            return value
        formats = [
            '%Y-%m-%d',
            '%m/%d/%Y',
            '%d-%m-%Y',
            '%Y/%m/%d',
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return value
