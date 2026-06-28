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

# Module root resolved from __file__ so code tools work on any machine
_MODULE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Odoo installation root (e.g. /home/.../odoo18) — allows code tools to read core addon files
try:
    import odoo as _odoo_pkg
    _ODOO_ROOT = os.path.normpath(os.path.join(os.path.dirname(_odoo_pkg.__file__), '..'))
except Exception:
    _ODOO_ROOT = None


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

    def _dispatch_search_read(self, arguments, env, user):
        model_name = arguments['model']
        domain = _normalize_domain(arguments.get('domain', []))
        fields = arguments.get('fields', [])
        limit = min(arguments.get('limit', 10), 100)
        order = arguments.get('order')

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
        return {'id': record.id}

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

        search_roots = [_MODULE_ROOT]
        if _ODOO_ROOT and os.path.isdir(_ODOO_ROOT):
            search_roots.append(_ODOO_ROOT)

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

        # Try custom module root first, then Odoo core root
        allowed_roots = [_MODULE_ROOT]
        if _ODOO_ROOT and os.path.isdir(_ODOO_ROOT):
            allowed_roots.append(_ODOO_ROOT)

        target_path = None
        for root in allowed_roots:
            candidate = os.path.abspath(os.path.join(root, filepath))
            if candidate.startswith(root) and os.path.exists(candidate):
                target_path = candidate
                break

        if target_path is None:
            raise FileNotFoundError(f"File {filepath} not found.")

        with open(target_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        return "".join(lines[start_line - 1 : end_line])

    def _dispatch_execute_orm(self, arguments, env, user):
        code = arguments['code']
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
            'datetime': datetime,
            'date': date,
            'timedelta': timedelta,
            'defaultdict': defaultdict,
            'Counter': Counter,
        }
        # Wrap in a function so multi-line code with assignments works.
        # Auto-insert return before the last expression line (bare name or call).
        stripped = code.strip()
        lines = stripped.splitlines()
        last_idx = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), -1)
        if last_idx >= 0:
            last = lines[last_idx].strip()
            _stmt_kw = ('return ', 'raise ', 'pass', 'break', 'continue',
                        'import ', 'from ', 'if ', 'else:', 'elif ', 'for ',
                        'while ', 'with ', 'try:', 'except', 'def ', 'class ', '#')
            _is_assign = bool(re.match(r'^[\w\s\[\].]+\s*[+\-*/%&|^]?=(?!=)', last))
            if last and not any(last.startswith(k) for k in _stmt_kw) and not _is_assign:
                indent = len(lines[last_idx]) - len(lines[last_idx].lstrip())
                lines[last_idx] = ' ' * indent + 'return ' + last
                stripped = '\n'.join(lines)
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
                        value = Command.set(value)
                elif isinstance(value, str) and value:
                    try:
                        ids = [int(x.strip()) for x in value.split(',') if x.strip().isdigit()]
                        value = Command.set(ids)
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
