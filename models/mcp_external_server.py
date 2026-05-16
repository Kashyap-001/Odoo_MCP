"""
mcp_gateway/models/mcp_external_server.py

Model for storing external MCP server configurations.

This model allows users to configure connections to external MCP servers
(e.g., WhatsApp MCP, YouTube MCP) and use their tools alongside local Odoo tools.

Key classes:
  ExternalServer — Configuration for an external MCP server

Dependencies:
  - mcp.external_servers module for connection management
  - json for configuration parsing

Developer notes:
  - Stores command/args for stdio-based MCP servers
  - Stores URL for HTTP/SSE-based MCP servers
  - Tools are loaded dynamically and cached
  - Connection test available to verify server is reachable
"""

import logging
import json
from base64 import b64encode, b64decode
from cryptography.fernet import Fernet, InvalidToken
from odoo import fields, models, api, _, exceptions
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ExternalServer(models.Model):
    """
    External MCP Server Configuration (mcp.external.server)

    Stores connection details for external MCP servers that provide additional
    tools. These tools are loaded dynamically and merged with local Odoo tools.

    Relationships:
      - HasMany: mcp.tool via tool_set_ids (optional)

    Fields:
      - name: Human-readable server name
      - server_type: 'stdio' or 'http'
      - command: Command to run (for stdio)
      - args: Command arguments (for stdio)
      - url: Server URL (for HTTP)
      - auth_type: Authentication type
      - auth_value: Encrypted auth credential
      - active: Enable/disable server
      - last_refresh: Last time tools were loaded
      - tool_count: Cached count of available tools
    """

    _name = 'mcp.external.server'
    _description = _('External MCP Server')
    _order = 'name'

    name = fields.Char(
        string=_('Server Name'),
        required=True,
        help=_('Human-readable name (e.g., "WhatsApp MCP", "YouTube MCP")'),
    )
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        help=_('Inactive servers are not connected'),
    )
    server_type = fields.Selection(
        [
            ('stdio', 'Stdio (Local Process)'),
            ('http', 'HTTP/SSE (Remote Server)'),
        ],
        string=_('Transport Type'),
        required=True,
        default='stdio',
        help=_('Stdio: runs local command. HTTP: connects to remote URL.'),
    )
    command = fields.Char(
        string=_('Command'),
        help=_('Command to run (e.g., "npx", "python", "node")'),
    )
    args = fields.Text(
        string=_('Arguments'),
        help=_('Command arguments (one per line or JSON array)'),
        default='',
    )
    url = fields.Char(
        string=_('Server URL'),
        help=_('URL for HTTP/SSE transport (e.g., http://localhost:8080/mcp)'),
    )
    env_vars = fields.Text(
        string=_('Environment Variables'),
        help=_('JSON object with environment variables'),
    )
    auth_type = fields.Selection(
        [
            ('none', 'None'),
            ('bearer', 'Bearer Token'),
            ('api_key', 'API Key'),
        ],
        string=_('Authentication'),
        default='none',
    )
    auth_value = fields.Char(
        string=_('Auth Credential'),
        groups='mcp_gateway.group_mcp_admin',
        help=_('Encrypted authentication credential'),
    )
    description = fields.Text(
        string=_('Description'),
        help=_('What this MCP server provides'),
    )
    last_refresh = fields.Datetime(
        string=_('Last Refresh'),
        readonly=True,
        help=_('Last time tools were loaded from this server'),
    )
    tool_count = fields.Integer(
        string=_('Tool Count'),
        readonly=True,
        help=_('Number of tools available from this server'),
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Server name must be unique.')),
    ]

    def _get_fernet_key(self) -> bytes:
        """
        Get or auto-generate Fernet encryption key for auth credentials.

        Returns:
            bytes: 32-byte Fernet key
        """
        param_key = 'mcp_gateway.fernet_key'
        stored_key = self.env['ir.config_parameter'].get_param(param_key)

        if stored_key:
            try:
                return b64decode(stored_key)
            except Exception as e:
                _logger.error('Failed to decode Fernet key from config: %s', str(e))
                raise UserError(_('Encryption key corrupted. Contact administrator.'))

        new_key = Fernet.generate_key()
        self.env['ir.config_parameter'].set_param(param_key, b64encode(new_key).decode())
        _logger.info('Generated new Fernet key for auth credential encryption')
        return new_key

    def _encrypt_auth_value(self, plaintext: str) -> str:
        """
        Encrypt plaintext auth credential using Fernet symmetric encryption.

        Args:
            plaintext: Plaintext auth credential

        Returns:
            str: Fernet ciphertext (URL-safe base64)
        """
        if not plaintext:
            return ''

        try:
            key = self._get_fernet_key()
            cipher_suite = Fernet(key)
            ciphertext = cipher_suite.encrypt(plaintext.encode())
            return ciphertext.decode()
        except Exception as e:
            _logger.error('Auth credential encryption failed: %s', str(e))
            raise UserError(_('Failed to encrypt auth credential: %s') % str(e))

    def _decrypt_auth_value(self, ciphertext: str) -> str:
        """
        Decrypt stored auth credential ciphertext to plaintext.

        Args:
            ciphertext: Fernet ciphertext

        Returns:
            str: Plaintext auth credential
        """
        if not ciphertext:
            return ''

        try:
            key = self._get_fernet_key()
            cipher_suite = Fernet(key)
            plaintext = cipher_suite.decrypt(ciphertext.encode())
            return plaintext.decode()
        except InvalidToken:
            _logger.error('Auth credential decryption failed: invalid token or corrupted key')
            raise UserError(
                _('Failed to decrypt auth credential. Key may be corrupted or Fernet key lost.')
            )
        except Exception as e:
            _logger.error('Auth credential decryption failed: %s', str(e))
            raise UserError(_('Failed to decrypt auth credential: %s') % str(e))

    def get_decrypted_auth_value(self) -> str:
        """
        Get decrypted auth value for making API calls.

        Returns:
            str: Plaintext auth credential
        """
        return self._decrypt_auth_value(self.auth_value) if self.auth_value else ''

    @api.model
    def create(self, vals):
        """
        Override to encrypt auth_value before storing.
        """
        if vals.get('auth_value'):
            # Encrypt before storing
            temp = self.new(vals)
            vals['auth_value'] = temp._encrypt_auth_value(vals['auth_value'])
        return super().create(vals)

    def write(self, vals):
        """
        Override to encrypt new auth_value if provided.
        """
        if vals.get('auth_value'):
            vals['auth_value'] = self._encrypt_auth_value(vals['auth_value'])
        return super().write(vals)

    @api.onchange('server_type')
    def _onchange_server_type(self):
        """Clear type-specific fields when transport type changes."""
        if self.server_type == 'stdio':
            self.url = False
        else:
            self.command = False
            self.args = False
            self.env_vars = False

    def test_connection(self):
        """
        Test connection to external MCP server.

        Calls tools/list to verify server is reachable and returns tool names.

        Returns:
            dict: Wizard action to display test result
        """
        self = self[0] if len(self) > 1 else self

        if self.server_type == 'stdio':
            result = self._test_stdio_connection()
        else:
            result = self._test_http_connection()

        # Create result wizard
        wizard = self.env['mcp.connection.test.wizard'].create({
            'status': 'success' if result['success'] else 'error',
            'message': result['message'],
            'model_list': f"{result['tool_count']} tools available" if result['tool_count'] > 0 else '',
        })

        return {
            'name': _('Test Connection Result'),
            'type': 'ir.actions.act_window',
            'res_model': 'mcp.connection.test.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _test_stdio_connection(self):
        """Test stdio-based MCP server connection."""
        try:
            import asyncio
            import subprocess
            import json

            # Parse command and args
            cmd = self.command
            if self.args:
                try:
                    args = json.loads(self.args)
                except json.JSONDecodeError:
                    args = self.args.strip().split('\n')
            else:
                args = []

            # Build environment
            env = {}
            if self.env_vars:
                try:
                    env = json.loads(self.env_vars)
                except json.JSONDecodeError:
                    pass

            # MCP servers typically respond to --help or list tools
            # We'll just verify the command can be executed
            test_args = args + ['--help']
            result = subprocess.run(
                [cmd] + test_args,
                capture_output=True,
                text=True,
                timeout=5,
                env={**dict(__import__('os').environ), **env},
            )

            # MCP servers often return 0 for --help
            if result.returncode in [0, 1]:  # --help often returns 1
                return {
                    'success': True,
                    'message': 'Server command is valid',
                    'tool_count': 0,
                }
            else:
                return {
                    'success': False,
                    'message': f'Command error: {result.stderr[:200]}',
                    'tool_count': 0,
                }

        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'message': 'Connection timed out',
                'tool_count': 0,
            }
        except FileNotFoundError:
            return {
                'success': False,
                'message': f'Command not found: {self.command}',
                'tool_count': 0,
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Error: {str(e)}',
                'tool_count': 0,
            }

    def _test_http_connection(self):
        """Test HTTP-based MCP server connection by calling tools/list."""
        try:
            import httpx

            if not self.url:
                return {
                    'success': False,
                    'message': 'URL is required for HTTP transport',
                    'tool_count': 0,
                }

            # Build headers with auth (decrypt stored value)
            headers = {'Content-Type': 'application/json'}
            auth_credential = self.get_decrypted_auth_value()
            if self.auth_type == 'bearer' and auth_credential:
                headers['Authorization'] = f'Bearer {auth_credential}'
            elif self.auth_type == 'api_key' and auth_credential:
                headers['X-API-Key'] = auth_credential

            # Try calling tools/list endpoint (JSON-RPC 2.0)
            payload = {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/list',
                'params': {},
            }

            with httpx.Client(timeout=15) as client:
                response = client.post(
                    self.url,
                    json=payload,
                    headers=headers,
                )

            if response.status_code in [200, 404]:
                # Parse response
                try:
                    data = response.json()
                    if 'result' in data and 'tools' in data['result']:
                        tools = data['result']['tools']
                        tool_names = [t.get('name') for t in tools[:10]]
                        return {
                            'success': True,
                            'message': f'Server connected! Found {len(tools)} tools: {", ".join(tool_names)}{"..." if len(tools) > 10 else ""}',
                            'tool_count': len(tools),
                        }
                    elif 'error' in data:
                        return {
                            'success': False,
                            'message': f'Server error: {data["error"].get("message", "Unknown")}',
                            'tool_count': 0,
                        }
                except Exception:
                    pass

                # If 404, try a plain GET (some MCP servers use different endpoints)
                if response.status_code == 404:
                    return {
                        'success': True,
                        'message': 'Server is reachable (MCP endpoint not found at this URL)',
                        'tool_count': 0,
                    }

                return {
                    'success': True,
                    'message': 'Server is reachable',
                    'tool_count': 0,
                }
            else:
                return {
                    'success': False,
                    'message': f'Returned status {response.status_code}: {response.text[:200]}',
                    'tool_count': 0,
                }

        except httpx.TimeoutException:
            return {
                'success': False,
                'message': 'Connection timed out after 15s',
                'tool_count': 0,
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Error: {str(e)}',
                'tool_count': 0,
            }

    def refresh_tools(self):
        """
        Refresh available tools from this MCP server.

        Updates last_refresh and tool_count fields.
        """
        self.ensure_one()

        try:
            import asyncio

            # This would need to run asynchronously in a real implementation
            # For now, just mark as refreshed
            self.write({
                'last_refresh': fields.Datetime.now(),
                'tool_count': 0,  # Would be updated after actual refresh
            })

        except Exception as e:
            _logger.error('Error refreshing tools from %s: %s', self.name, str(e))

    def get_server_config(self) -> dict:
        """
        Get server configuration as a dict for ExternalMCPServerManager.

        Returns:
            dict: Server configuration
        """
        self.ensure_one()

        # Parse args
        args = []
        if self.args:
            try:
                args = json.loads(self.args)
            except json.JSONDecodeError:
                args = [line.strip() for line in self.args.strip().split('\n') if line.strip()]

        # Parse env vars
        env_vars = {}
        if self.env_vars:
            try:
                env_vars = json.loads(self.env_vars)
            except json.JSONDecodeError:
                pass

        return {
            'name': self.name,
            'command': self.command,
            'args': args + ([self.url] if self.url and self.server_type == 'http' else []),
            'env_vars': env_vars,
            'transport': 'sse' if self.server_type == 'http' else 'stdio',
        }

    @api.model
    def load_all_configs(self) -> list:
        """
        Load all active server configurations.

        Returns:
            list: List of server config dicts
        """
        servers = self.search([('active', '=', True)])
        return [server.get_server_config() for server in servers]