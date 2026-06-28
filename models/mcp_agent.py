"""
mcp_gateway/models/mcp_agent.py

AI Agent model for managing LLM agent configurations, API keys, and tool assignments.

Key classes:
  Agent — AI agent profile with provider, model, prompt, and tool configuration

Dependencies:
  - mcp.tool — tools assigned to agent
  - mcp.tool.set — tool bundles assigned to agent
  - mcp.session — conversation sessions
  - res.groups — access control groups
  - Imports cryptography.fernet for API key encryption
  - Imports logging for debug output

Developer notes:
  - API keys stored encrypted using Fernet symmetric encryption
  - FERNET_KEY auto-generated on first use if missing
  - Providers loaded dynamically from mcp.providers.<provider> module
  - Use _decrypt_api_key() only when calling provider (never log it)
  - _get_provider_instance() returns an AbstractProvider subclass
"""

import logging
from base64 import b64encode, b64decode
from cryptography.fernet import Fernet, InvalidToken
from odoo import fields, models, api, _, exceptions
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class Agent(models.Model):
    """
    AI Agent Profile (mcp.agent)

    Manages configuration for a single AI agent, including:
      - LLM provider selection and model
      - API key (Fernet-encrypted at rest)
      - System prompt and LLM parameters (temperature, tokens, etc.)
      - Assigned tools and access restrictions
      - Context and memory injection settings

    Relationships:
      - HasMany: mcp.tool.set via many2many (tool_set_ids)
      - HasMany: mcp.tool via many2many (tool_ids, for direct assignment)
      - HasMany: res.groups via many2many (allowed_group_ids)
      - HasMany: mcp.session via one2many

    Business rules:
      - Name must be unique
      - Provider and model_name required
      - API key required for non-local providers (Anthropic, OpenAI, Gemini)
      - Temperature must be 0.0–2.0
      - max_tokens must be 1–32000
      - Encryption key auto-generated and persisted
    """

    _name = 'mcp.agent'
    _description = _('AI Agent Profile')
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name'

    # ── Basic Configuration ─────────────────────────────────────────
    sequence = fields.Integer(
        string=_('Sequence'),
        default=10,
        help=_('Order for drag-and-drop reordering in views'),
    )
    name = fields.Char(
        string=_('Agent Name'),
        required=True,
        tracking=True,
        help=_('Human-readable name (e.g., "Sales Assistant", "Finance Bot")'),
    )
    active = fields.Boolean(
        string=_('Active'),
        default=True,
        tracking=True,
        help=_('Inactive agents are not available for chat'),
    )
    description = fields.Text(
        string=_('Description'),
        translate=True,
        help=_('What this agent specializes in'),
    )
    notes = fields.Html(
        string=_('Notes'),
        sanitize=False,
        help=_('Internal notes about this agent'),
    )
    color = fields.Integer(
        string=_('Color'),
        default=0,
        help=_('Kanban color (0-11) for visual grouping'),
    )
    avatar = fields.Binary(
        string=_('Avatar'),
        help=_('Profile image for agent'),
    )

    # ── Provider & Model Configuration ──────────────────────────────
    provider = fields.Selection(
        [
            ('anthropic', 'Anthropic'),
            ('openai', 'OpenAI'),
            ('gemini', 'Google Gemini'),
            ('ollama', 'Ollama (local)'),
            ('minimax', 'MiniMax'),
            ('opencode', 'OpenCode AI'),
        ],
        string=_('Provider'),
        required=True,
        default='anthropic',
        tracking=True,
        help=_('LLM provider backend'),
    )
    model_name = fields.Char(
        string=_('Model Name'),
        required=True,
        help=_('Enter model name manually or use the dropdown below.'),
    )
    model_selection = fields.Selection(
        string=_('Available Models'),
        selection='_get_model_selection_options',
        help=_('Select from available models. Updates when you change provider or API key.'),
    )
    api_key = fields.Char(
        string=_('API Key'),
        groups='mcp_gateway.group_mcp_admin',
        help=_('Provider API key (encrypted at rest, never logged)'),
    )
    api_base_url = fields.Char(
        string=_('API Base URL'),
        help=_('Override default provider URL (e.g., for self-hosted models)'),
    )
    author_id = fields.Many2one(
        'res.users',
        string=_('Bot User'),
        help=_('User that will post agent responses (leave empty for system bot)'),
    )

    # ── LLM Parameters ──────────────────────────────────────────────
    max_tokens = fields.Integer(
        string=_('Max Tokens'),
        default=2048,
        help=_('Maximum tokens in response (1–32000)'),
    )
    temperature = fields.Float(
        string=_('Temperature'),
        default=0.7,
        help=_('Creativity (0.0=deterministic, 2.0=very random)'),
    )
    top_p = fields.Float(
        string=_('Top P'),
        default=1.0,
        help=_('Nucleus sampling (0.0–1.0)'),
    )
    system_prompt = fields.Text(
        string=_('System Prompt'),
        help=_('Instructions that define agent behavior'),
    )

    # ── Context & Memory ────────────────────────────────────────────
    enable_memory = fields.Boolean(
        string=_('Enable Session Memory'),
        default=False,
        help=_('Inject summaries of past sessions to inform replies'),
    )
    context_fields = fields.Text(
        string=_('Context Fields (JSON)'),
        default='[]',
        help=_('Model fields to auto-inject from active record (e.g., ["name","email"])'),
    )

    # ── Cost Tracking ───────────────────────────────────────────────
    cost_per_1k_input = fields.Float(
        string=_('Cost per 1K Input Tokens (USD)'),
        default=0.0,
        help=_('For token usage reporting (e.g., 0.003 for Claude Sonnet)'),
    )
    cost_per_1k_output = fields.Float(
        string=_('Cost per 1K Output Tokens (USD)'),
        default=0.0,
        help=_('For token usage reporting (e.g., 0.015 for Claude Sonnet)'),
    )

    # ── Tools & Access Control ──────────────────────────────────────
    tool_set_ids = fields.Many2many(
        comodel_name='mcp.tool.set',
        relation='mcp_agent_tool_set_rel',
        column1='agent_id',
        column2='tool_set_id',
        string=_('Tool Sets'),
        help=_('Pre-configured tool bundles assigned to this agent'),
    )
    tool_ids = fields.Many2many(
        comodel_name='mcp.tool',
        relation='mcp_agent_tool_rel',
        column1='agent_id',
        column2='tool_id',
        string=_('Direct Tools'),
        help=_('Individual tools assigned directly (in addition to tool sets)'),
    )
    allowed_group_ids = fields.Many2many(
        comodel_name='res.groups',
        relation='mcp_agent_group_rel',
        column1='agent_id',
        column2='group_id',
        string=_('Allowed Groups'),
        help=_('Users in these groups can use this agent'),
    )

    # ── Sessions ────────────────────────────────────────────────────
    session_ids = fields.One2many(
        comodel_name='mcp.session',
        inverse_name='agent_id',
        string=_('Sessions'),
        help=_('Conversation sessions with this agent'),
    )

    # ── Computed Fields ─────────────────────────────────────────────
    session_count = fields.Integer(
        string=_('Session Count'),
        compute='_compute_session_count',
        store=True,
        help=_('Total conversations with this agent'),
    )
    last_used = fields.Datetime(
        string=_('Last Used'),
        compute='_compute_last_used',
        store=True,
        help=_('Timestamp of most recent session'),
    )
    total_tokens = fields.Integer(
        string=_('Total Tokens'),
        compute='_compute_totals',
        store=True,
    )
    total_cost_usd = fields.Float(
        string=_('Total Cost (USD)'),
        compute='_compute_totals',
        store=True,
        digits=(10, 6),
    )
    effective_tool_ids = fields.Many2many(
        comodel_name='mcp.tool',
        compute='_compute_effective_tools',
        string=_('Effective Tools'),
        help=_('Union of all tools from tool sets + direct tools (computed)'),
    )
    status = fields.Selection(
        [
            ('online', _('Online')),
            ('error', _('Error')),
            ('unconfigured', _('Unconfigured')),
        ],
        string=_('Status'),
        compute='_compute_status',
        help=_('Agent readiness (based on API key and provider availability)'),
    )

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', _('Agent name must be unique.')),
    ]

    @api.onchange('provider')
    def _onchange_provider(self):
        """
        Set default model_name and fetch available models from API when possible.

        When user changes provider, auto-populate model_name with
        the most recent stable model for that provider.
        If API key is present, try to fetch actual available models.
        """
        defaults = {
            'anthropic': 'claude-sonnet-4-6',
            'openai': 'gpt-4o',
            'gemini': 'gemini-2.0-flash',
            'ollama': 'llama3.1',
            'minimax': 'abab6.5s-chat',
            'opencode': 'minimax-m2.5-free',
        }
        if self.provider in defaults:
            self.model_name = defaults[self.provider]

        # Clear model_selection when provider changes
        self.model_selection = False

        # Try to fetch available models if API key is present
        if self.provider and self.api_key:
            try:
                _logger.info('_onchange_provider: fetching models for %s', self.provider)
                provider_obj = self._get_provider_instance()
                if provider_obj:
                    models = provider_obj.get_available_models(self)
                    _logger.info('_onchange_provider: got models %s', models)
                    if models:
                        # Build selection from API models
                        self.model_selection = models[0] if models else False
                        self.model_name = models[0] if models else self.model_name
            except Exception as e:
                _logger.warning('_onchange_provider: failed to fetch models: %s', e)

    @api.onchange('api_key')
    def _onchange_api_key(self):
        """
        Fetch available models when API key is entered.
        """
        if self.provider and self.api_key:
            try:
                _logger.info('_onchange_api_key: fetching models for %s', self.provider)
                provider_obj = self._get_provider_instance()
                if provider_obj:
                    models = provider_obj.get_available_models(self)
                    _logger.info('_onchange_api_key: got models %s', models)
                    if models:
                        self.model_selection = models[0]
                        self.model_name = models[0]
            except Exception as e:
                _logger.warning('_onchange_api_key: failed to fetch models: %s', e)

    @api.onchange('model_selection')
    def _onchange_model_selection(self):
        """
        Sync model_selection to model_name when user picks from dropdown.
        """
        if self.model_selection:
            self.model_name = self.model_selection

    @api.model
    def _get_model_selection_options(self):
        """
        Get available models for dropdown - ALL models combined.

        Since selection method can't access current record context (no @api.one),
        returns all models from all providers. Users can search/filter in dropdown.

        Returns:
            list: List of (value, label) tuples for selection
        """
        _logger.info('_get_model_selection_options called')
        all_models = [
            # Anthropic
            'claude-opus-4-7', 'claude-sonnet-4-6', 'claude-haiku-4-5',
            # OpenAI
            'gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'o1-preview', 'o1-mini',
            # Gemini
            'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash',
            # Ollama
            'llama3.1', 'llama3', 'mistral', 'codellama',
            # MiniMax
            'abab6.5s-chat', 'abab6.5g-chat', 'abab5.5s-chat',
            # OpenCode Zen models (https://opencode.ai/zen)
            # Free models
            'minimax-m2.5-free', 'deepseek-v4-flash-free', 'big-pickle',
            'ring-2.6-1t-free', 'nemotron-3-super-free',
            # MiniMax
            'minimax-m2.7', 'minimax-m2.5',
            # Qwen
            'qwen3.6-plus', 'qwen3.5-plus',
            # GLM
            'glm-5.1', 'glm-5',
            # Kimi
            'kimi-k2.6', 'kimi-k2.5',
            # GPT 5.x series (via OpenCode Zen /v1/responses)
            'gpt-5.5', 'gpt-5.5-pro', 'gpt-5.4', 'gpt-5.4-pro',
            'gpt-5.4-mini', 'gpt-5.4-nano', 'gpt-5.3-codex',
            'gpt-5.3-codex-spark', 'gpt-5.2', 'gpt-5.2-codex',
            'gpt-5.1', 'gpt-5.1-codex', 'gpt-5.1-codex-max',
            'gpt-5.1-codex-mini', 'gpt-5', 'gpt-5-codex', 'gpt-5-nano',
            # Claude via OpenCode Zen (/v1/messages)
            'claude-opus-4-6', 'claude-opus-4-5', 'claude-opus-4-1',
            'claude-sonnet-4-5', 'claude-sonnet-4', 'claude-3-5-haiku',
            # Gemini via OpenCode Zen
            'gemini-3.1-pro', 'gemini-3-flash',
        ]
        result = [(m, m) for m in sorted(set(all_models))]
        _logger.info('_get_model_selection_options returning %d models', len(result))
        return result

    @api.constrains('temperature')
    def _check_temperature(self):
        """
        Validate temperature is within LLM bounds.

        Raises:
            ValidationError: if temperature outside 0.0–2.0 range

        Example:
            Valid: 0.0, 0.7, 2.0
            Invalid: -0.1, 2.5
        """
        for agent in self:
            if agent.temperature < 0.0 or agent.temperature > 2.0:
                raise exceptions.ValidationError(
                    _('Temperature must be between 0.0 and 2.0. Got: %.2f')
                    % agent.temperature
                )

    @api.constrains('max_tokens')
    def _check_max_tokens(self):
        """
        Validate max_tokens is within LLM bounds.

        Raises:
            ValidationError: if max_tokens outside 1–32000 range

        Example:
            Valid: 100, 2048, 32000
            Invalid: 0, 50000
        """
        for agent in self:
            if agent.max_tokens < 1 or agent.max_tokens > 32000:
                raise exceptions.ValidationError(
                    _('Max tokens must be between 1 and 32000. Got: %d')
                    % agent.max_tokens
                )

    def _get_bot_user(self):
        """
        Get or create the Jarvis bot user for agent responses.

        Returns:
            res.users: Bot user record
        """
        bot_name = 'Jarvis'
        bot_user = self.env['res.users'].search([('name', '=', bot_name)], limit=1)

        if not bot_user:
            # Create bot user with partner
            _logger.info('Creating Jarvis bot user')
            bot_partner = self.env['res.partner'].create({
                'name': bot_name,
                'email': 'jarvis@localhost',
            })
            bot_user = self.env['res.users'].create({
                'name': bot_name,
                'login': 'jarvis',
                'partner_id': bot_partner.id,
                'groups_id': [
                    (4, self.env.ref('base.group_user').id),
                ],
            })
            _logger.info('Created Jarvis bot user with id=%d', bot_user.id)

        return bot_user

    def _get_fernet_key(self) -> bytes:
        """
        Get or auto-generate Fernet encryption key for API keys.

        The key is persisted in ir.config_parameter to survive server restarts.
        If key doesn't exist, auto-generates a new one.

        Returns:
            bytes: 32-byte Fernet key

        Developer notes:
            - Called by _encrypt_api_key() and _decrypt_api_key()
            - Never logs the key
            - Safe to call multiple times (returns same key)
        """
        param_key = 'mcp_gateway.fernet_key'
        stored_key = self.env['ir.config_parameter'].get_param(param_key)

        if stored_key:
            try:
                return b64decode(stored_key)
            except Exception as e:
                _logger.error('Failed to decode Fernet key from config: %s', str(e))
                raise UserError(_('Encryption key corrupted. Contact administrator.'))

        # Auto-generate new key
        new_key = Fernet.generate_key()
        self.env['ir.config_parameter'].set_param(param_key, b64encode(new_key).decode())
        _logger.info('Generated new Fernet key for API key encryption')
        return new_key

    def _encrypt_api_key(self, plaintext: str) -> str:
        """
        Encrypt plaintext API key using Fernet symmetric encryption.

        Called when api_key field is written. Result stored in database.

        Args:
            plaintext (str): Plaintext API key

        Returns:
            str: Fernet ciphertext (URL-safe base64)

        Raises:
            UserError: if encryption fails

        Developer notes:
            - Ciphertext includes timestamp and HMAC for integrity
            - Decryption will fail if key is lost
            - Store backup of Fernet key in secure location
        """
        if not plaintext:
            return ''

        try:
            key = self._get_fernet_key()
            cipher_suite = Fernet(key)
            ciphertext = cipher_suite.encrypt(plaintext.encode())
            return ciphertext.decode()
        except Exception as e:
            _logger.error('API key encryption failed: %s', str(e))
            raise UserError(_('Failed to encrypt API key: %s') % str(e))

    @api.onchange('author_id')
    def _onchange_author_id(self):
        """
        Set bot user as default if not selected.
        """
        if not self.author_id:
            self.author_id = self._get_bot_user()

    @api.model
    def create(self, vals):
        """
        Override to set default bot user on creation.
        """
        if 'author_id' not in vals or not vals.get('author_id'):
            bot_user = self._get_bot_user()
            vals['author_id'] = bot_user.id
        return super().create(vals)

    def write(self, vals):
        """
        Override to ensure bot user is set.
        """
        if 'author_id' not in vals or not vals.get('author_id'):
            for record in self:
                if not record.author_id:
                    bot_user = record._get_bot_user()
                    vals['author_id'] = bot_user.id
                    break
        return super().write(vals)

    def _decrypt_api_key(self) -> str:
        """
        Decrypt stored API key ciphertext to plaintext.

        Called by _get_provider_instance() before calling provider.
        Result lives in memory only — never stored or logged.

        Returns:
            str: Plaintext API key

        Raises:
            UserError: if decryption fails or key is corrupted

        Developer notes:
            - Should only be called when provider call is imminent
            - Result should never be stored, logged, or transmitted
            - If Fernet key is lost, all stored API keys become unrecoverable
        """
        if not self.api_key:
            _logger.warning('No API key set for agent %s', getattr(self, 'id', 'New'))
            return ''

        # If it doesn't start with the Fernet magic string, it's likely a plaintext
        # key from an onchange event in the UI before it has been saved/encrypted.
        if not self.api_key.startswith('gAAAAAB'):
            _logger.info('API key does not appear to be encrypted. Assuming plaintext (e.g. from onchange).')
            return self.api_key

        try:
            key = self._get_fernet_key()
            cipher_suite = Fernet(key)
            plaintext = cipher_suite.decrypt(self.api_key.encode())
            decrypted = plaintext.decode()
            _logger.info('API key decrypted successfully for agent %s', getattr(self, 'id', 'New'))
            return decrypted
        except InvalidToken:
            _logger.error('API key decryption failed: invalid token or corrupted key')
            raise exceptions.UserError(
                _('Failed to decrypt API key. Key may be corrupted or Fernet key lost.')
            )
        except Exception as e:
            _logger.error('API key decryption failed: %s', str(e))
            raise exceptions.UserError(_('Failed to decrypt API key: %s') % str(e))

    def _get_provider_instance(self):
        """
        Load and instantiate the provider adapter for this agent.

        Returns the appropriate AbstractProvider subclass based on
        the agent's provider field.

        Returns:
            AbstractProvider: Provider instance (e.g., AnthropicAdapter)

        Raises:
            UserError: if provider is not supported or import fails

        Example:
            provider = agent._get_provider_instance()
            reply = provider.call(agent, messages, tool_specs)
        """
        from ..mcp.providers.anthropic import AnthropicAdapter
        from ..mcp.providers.openai import OpenAIAdapter
        from ..mcp.providers.gemini import GeminiAdapter
        from ..mcp.providers.ollama import OllamaAdapter
        from ..mcp.providers.minimax import MiniMaxAdapter
        from ..mcp.providers.opencode import OpenCodeAdapter

        provider_map = {
            'anthropic': AnthropicAdapter,
            'openai': OpenAIAdapter,
            'gemini': GeminiAdapter,
            'ollama': OllamaAdapter,
            'minimax': MiniMaxAdapter,
            'opencode': OpenCodeAdapter,
        }

        if self.provider not in provider_map:
            raise UserError(_('Unknown provider: %s') % self.provider)

        return provider_map[self.provider](self.env)

    @api.depends('session_ids.input_tokens', 'session_ids.output_tokens', 'session_ids.estimated_cost_usd')
    def _compute_totals(self):
        for agent in self:
            agent.total_tokens = sum(
                (s.input_tokens or 0) + (s.output_tokens or 0) for s in agent.session_ids
            )
            agent.total_cost_usd = sum(s.estimated_cost_usd or 0.0 for s in agent.session_ids)

    @api.depends('session_ids')
    def _compute_session_count(self):
        """
        Count total sessions for this agent.

        Returns:
            None — sets session_count field
        """
        for agent in self:
            agent.session_count = len(agent.session_ids)

    @api.depends('session_ids.create_date')
    def _compute_last_used(self):
        """
        Find most recent session timestamp.

        Returns:
            None — sets last_used field
        """
        for agent in self:
            if agent.session_ids:
                agent.last_used = max(s.create_date for s in agent.session_ids)
            else:
                agent.last_used = None

    @api.depends('tool_set_ids', 'tool_ids')
    def _compute_effective_tools(self):
        """
        Compute union of tools from tool sets and direct tools.

        Returns:
            None — sets effective_tool_ids field (computed, not stored)
        """
        for agent in self:
            tools = agent.tool_ids
            for tool_set in agent.tool_set_ids:
                tools = tools | tool_set.tool_ids
            agent.effective_tool_ids = tools

    @api.depends('provider', 'api_key')
    def _compute_status(self):
        """
        Determine agent readiness status.

        Returns 'online' if provider and api_key configured,
        'unconfigured' if missing api_key, 'error' if provider unreachable.

        Returns:
            None — sets status field
        """
        for agent in self:
            if agent.provider == 'ollama':
                # Ollama doesn't need API key
                agent.status = 'online'
            elif agent.api_key:
                agent.status = 'online'
            else:
                agent.status = 'unconfigured'

    def action_test_connection(self):
        """
        Test connection to provider and list available models.

        Opens a wizard showing:
          - Connection status (success / error)
          - Available models (if successful)
          - Recommended model selection

        Returns:
            dict: Wizard action

        Raises:
            UserError: if provider instance cannot be created
        """
        # Ensure we work with a single record (in case called from list view)
        self = self[0] if len(self) > 1 else self

        # Check if provider is configured
        if not self.provider:
            wizard = self.env['mcp.connection.test.wizard'].create({
                'status': 'error',
                'message': 'No provider selected. Please select a provider (Anthropic, OpenAI, Gemini, or Ollama) in the agent configuration.',
                'model_list': '',
            })
            return {
                'name': _('Test Connection'),
                'type': 'ir.actions.act_window',
                'res_model': 'mcp.connection.test.wizard',
                'res_id': wizard.id,
                'view_mode': 'form',
                'target': 'new',
            }

        try:
            _logger.info('Testing connection for agent ID: %s, provider: %s', self.id, self.provider)
            provider = self._get_provider_instance()
            _logger.info('Provider instance created: %s', provider)
            models = provider.get_available_models(self)
            _logger.info('Got models: %s', models)
            status = 'success'
            message = _('Connected successfully! %d models available.') % len(models)
            model_list = '\n'.join(models) if models else ''
        except Exception as e:
            _logger.error('Connection test failed: %s', str(e), exc_info=True)
            status = 'error'
            message = _('Connection failed: %s') % str(e)
            model_list = ''

        wizard = self.env['mcp.connection.test.wizard'].create({
            'status': status,
            'message': message,
            'model_list': model_list,
        })

        return {
            'name': _('Test Connection'),
            'type': 'ir.actions.act_window',
            'res_model': 'mcp.connection.test.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_refresh_models(self):
        """
        Fetch available models from the provider and auto-select first available.

        Returns:
            dict: Action to reload the form
        """
        self.ensure_one()

        if not self.provider:
            raise UserError(_('Please select a provider first.'))

        if not self.api_key:
            raise UserError(_('Please enter an API key first.'))

        try:
            provider = self._get_provider_instance()
            models = provider.get_available_models(self)

            if not models:
                raise UserError(_('No models found. Please check your API key.'))

            # Store available models in a config parameter
            key = f'mcp.available_models.{self.provider}'
            self.env['ir.config_parameter'].set_param(key, ','.join(models))

            # Auto-select first available model
            if models:
                self.model_name = models[0]
                _logger.info('Set model_name to: %s (available: %s)', models[0], models)

            # Show success notification
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Found %d models. Selected: %s') % (len(models), models[0]),
                    'type': 'success',
                },
            }

        except Exception as e:
            _logger.error('Failed to fetch models: %s', str(e))
            raise UserError(_('Failed to fetch models: %s') % str(e))

    def _get_model_selection(self):
        """Get available models based on provider."""
        self.ensure_one()
        key = f'mcp.available_models.{self.provider}'
        models_str = self.env['ir.config_parameter'].get_param(key, '')
        if models_str:
            return [(m, m) for m in models_str.split(',')]
        # Default fallback models
        defaults = {
            'anthropic': [('claude-sonnet-4-6', 'Claude Sonnet 4-6'), ('claude-3-5-sonnet-20241022', 'Claude 3.5 Sonnet')],
            'openai': [('gpt-4o', 'GPT-4o'), ('gpt-4o-mini', 'GPT-4o Mini')],
            'gemini': [('gemini-2.0-flash', 'Gemini 2.0 Flash'), ('gemini-1.5-flash', 'Gemini 1.5 Flash')],
            'ollama': [('llama3.1', 'Llama 3.1'), ('mistral', 'Mistral')],
            'minimax': [('abab6.5s-chat', 'abab6.5s-chat')],
        }
        return defaults.get(self.provider, [])

    def action_open_chat(self):
        """
        Open the chat wizard for this agent.

        Launches in-Odoo chat interface pre-selected with this agent.

        Returns:
            dict: Chat wizard action
        """
        return {
            'name': _('Chat with %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'mcp.chat.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_agent_id': self.id,
            },
        }

    def action_view_sessions(self):
        """
        Open list of all sessions for this agent.

        Returns:
            dict: Session list action filtered to this agent
        """
        return {
            'name': _('Sessions for %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'mcp.session',
            'view_mode': 'list,form',
            'domain': [('agent_id', '=', self.id)],
        }

    @api.model_create_multi
    def create(self, vals_list):
        """
        Create agent and encrypt API key.

        Overrides create to apply Fernet encryption to api_key before storing.

        Args:
            vals_list (list): List of value dicts for multi-create

        Returns:
            RecordSet: Created agent records
        """
        for vals in vals_list:
            if vals.get('api_key'):
                # Encrypt before storing
                agent_rec = self.env['mcp.agent'].new(vals)
                vals['api_key'] = agent_rec._encrypt_api_key(vals['api_key'])
        return super().create(vals_list)

    def write(self, vals):
        """
        Update agent and encrypt new API key if provided.

        Overrides write to apply Fernet encryption to api_key if changed.

        Args:
            vals (dict): Fields to update

        Returns:
            bool: True if successful
        """
        if vals.get('api_key'):
            vals['api_key'] = self._encrypt_api_key(vals['api_key'])
        return super().write(vals)
