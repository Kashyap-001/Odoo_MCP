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


PREMADE_AVATARS = {
    'generic': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_gen" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#714B67;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#4e3447;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_gen)"/>
  <rect x="28" y="28" width="44" height="44" rx="8" fill="none" stroke="#FFFFFF" stroke-width="5"/>
  <circle cx="42" cy="45" r="4" fill="#FFFFFF"/>
  <circle cx="58" cy="45" r="4" fill="#FFFFFF"/>
  <rect x="44" y="58" width="12" height="4" rx="1" fill="#FFFFFF"/>
  <rect x="47" y="16" width="6" height="12" fill="#FFFFFF"/>
  <circle cx="50" cy="16" r="4" fill="#FFFFFF"/>
</svg>'''),
    'sales': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_sales" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#ea00d9;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#711c91;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_sales)"/>
  <path d="M30,70 L48,45 L62,55 L74,30" stroke="#FFFFFF" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="74" cy="30" r="4" fill="#FFFFFF"/>
</svg>'''),
    'accounting': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_acc" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#00F260;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#0575E6;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_acc)"/>
  <rect x="25" y="20" width="50" height="60" rx="5" fill="none" stroke="#FFFFFF" stroke-width="5"/>
  <line x1="35" y1="35" x2="65" y2="35" stroke="#FFFFFF" stroke-width="5" stroke-linecap="round"/>
  <line x1="35" y1="50" x2="65" y2="50" stroke="#FFFFFF" stroke-width="5" stroke-linecap="round"/>
  <line x1="35" y1="65" x2="55" y2="65" stroke="#FFFFFF" stroke-width="5" stroke-linecap="round"/>
</svg>'''),
    'inventory': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_inv" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#f857a6;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#ff5858;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_inv)"/>
  <path d="M50,20 L78,34 L78,66 L50,80 L22,66 L22,34 Z" fill="none" stroke="#FFFFFF" stroke-width="5" stroke-linejoin="round"/>
  <path d="M50,20 L50,80 M22,34 L50,48 L78,34" stroke="#FFFFFF" stroke-width="4" fill="none"/>
</svg>'''),
    'developer': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_dev" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#232526;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#414345;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_dev)"/>
  <path d="M35,35 L20,50 L35,65" stroke="#FFFFFF" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M65,35 L80,50 L65,65" stroke="#FFFFFF" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  <line x1="55" y1="30" x2="45" y2="70" stroke="#FFFFFF" stroke-width="6" stroke-linecap="round"/>
</svg>'''),
    'support': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_sup" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#e65c00;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#F9D423;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_sup)"/>
  <circle cx="50" cy="50" r="30" fill="none" stroke="#FFFFFF" stroke-width="5"/>
  <path d="M32,55 C32,35 68,35 68,55" stroke="#FFFFFF" stroke-width="5" fill="none" stroke-linecap="round"/>
  <rect x="25" y="50" width="8" height="12" rx="2" fill="#FFFFFF"/>
  <rect x="67" y="50" width="8" height="12" rx="2" fill="#FFFFFF"/>
</svg>'''),
    'brain': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_brain" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#8E2DE2;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#4A00E0;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_brain)"/>
  <path d="M48,30 C35,30 30,40 30,52 C30,64 38,72 48,72 C48,72 48,67 48,62 M52,30 C65,30 70,40 70,52 C70,64 62,72 52,72 C52,72 52,67 52,62" fill="none" stroke="#FFFFFF" stroke-width="4" stroke-linecap="round"/>
  <circle cx="48" cy="40" r="3" fill="#FFFFFF"/>
  <circle cx="52" cy="40" r="3" fill="#FFFFFF"/>
  <circle cx="38" cy="52" r="3" fill="#FFFFFF"/>
  <circle cx="62" cy="52" r="3" fill="#FFFFFF"/>
  <circle cx="48" cy="64" r="3" fill="#FFFFFF"/>
  <circle cx="52" cy="64" r="3" fill="#FFFFFF"/>
  <path d="M48,40 L38,52 L48,64 M52,40 L62,52 L52,64" fill="none" stroke="#FFFFFF" stroke-width="2" opacity="0.6"/>
</svg>'''),
    'rocket': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_rocket" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#FF416C;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#FF4B2B;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_rocket)"/>
  <path d="M50,22 C56,35 64,45 64,62 L36,62 C36,45 44,35 50,22 Z" fill="#FFFFFF"/>
  <circle cx="50" cy="45" r="4" fill="#FF416C"/>
  <path d="M36,52 L24,66 L36,62 Z" fill="#FFFFFF"/>
  <path d="M64,52 L76,66 L64,62 Z" fill="#FFFFFF"/>
  <path d="M46,66 L50,82 L54,66 Z" fill="#FFDE00"/>
</svg>'''),
    'wizard': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_wiz" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#00c6ff;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#0072ff;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_wiz)"/>
  <path d="M30,70 L65,35 L70,40 L35,75 Z" fill="#FFFFFF"/>
  <rect x="63" y="32" width="10" height="10" rx="2" transform="rotate(45 68 37)" fill="#FFD700"/>
  <polygon points="50,25 52,30 57,30 53,33 55,38 50,35 45,38 47,33 43,30 48,30" fill="#FFFFFF"/>
  <polygon points="75,50 76.5,53.5 80,53.5 77,55.5 78.5,59 75,57 71.5,59 73,55.5 70,53.5 73.5,53.5" fill="#FFFFFF"/>
  <circle cx="45" cy="40" r="2" fill="#FFFFFF"/>
  <circle cx="60" cy="55" r="2" fill="#FFFFFF"/>
</svg>'''),
    'lightning': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_light" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#F2994A;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#F2C94C;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_light)"/>
  <polygon points="56,20 28,52 48,52 44,80 72,48 52,48" fill="#FFFFFF"/>
</svg>'''),
    'shield': b64encode(b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="grad_shd" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#11998e;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#38ef7d;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="20" fill="url(#grad_shd)"/>
  <path d="M30,26 L50,18 L70,26 L70,50 C70,66 50,80 50,80 C50,80 30,66 30,50 Z" fill="#FFFFFF"/>
  <path d="M42,48 L48,54 L58,38" stroke="#11998e" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''),
}


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
    premade_avatar = fields.Selection(
        [
            ('custom', _('Custom Upload')),
            ('generic', _('Generic AI (Robot)')),
            ('sales', _('Sales Assistant')),
            ('accounting', _('Accounting Assistant')),
            ('inventory', _('Inventory Assistant')),
            ('developer', _('Developer Assistant')),
            ('support', _('Support Assistant')),
            ('brain', _('Neural AI (Brain)')),
            ('rocket', _('Speed Optimizer (Rocket)')),
            ('wizard', _('Automation Wizard')),
            ('lightning', _('Fast Bolt')),
            ('shield', _('Security Shield')),
        ],
        string=_('Preset Avatar'),
        default='custom',
        help=_('Choose a preset icon or upload your custom one'),
    )
    avatar = fields.Binary(
        string=_('Avatar'),
        help=_('Profile image for agent'),
    )

    @api.onchange('premade_avatar')
    def _onchange_premade_avatar(self):
        if self.premade_avatar and self.premade_avatar != 'custom':
            self.avatar = PREMADE_AVATARS.get(self.premade_avatar)

    # ── Provider & Model Configuration ──────────────────────────────
    provider = fields.Selection(
        [
            ('anthropic', 'Anthropic'),
            ('openai', 'OpenAI'),
            ('gemini', 'Google Gemini'),
            ('ollama', 'Ollama (local)'),
            ('grok', 'Grok (xAI)'),
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
    model_selection = fields.Many2one(
        'mcp.model.option',
        string=_('Available Models'),
        domain="[('provider', '=', provider)]",
        help=_('Select from available models for this provider. Use Refresh Models to fetch latest from API.'),
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
            'grok': 'grok-3-mini',
            'opencode': 'minimax-m2.5-free',
        }
        if self.provider in defaults:
            self.model_name = defaults[self.provider]

        # Clear model_selection when provider changes (old provider's record is invalid)
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
                        self.model_name = models[0]
                        self.model_selection = self._ensure_model_option(self.provider, models[0])
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
                        self.model_name = models[0]
                        self.model_selection = self._ensure_model_option(self.provider, models[0])
            except Exception as e:
                _logger.warning('_onchange_api_key: failed to fetch models: %s', e)

    def _ensure_model_option(self, provider, model_name):
        """Find the mcp.model.option matching a live-fetched model, creating one
        (is_discovered=True) if the model isn't in the pre-seeded list yet — keeps
        the model_selection dropdown in sync with model_name instead of silently
        going empty for newly-released models the seed data doesn't know about yet."""
        rec = self.env['mcp.model.option'].search(
            [('provider', '=', provider), ('name', '=', model_name)], limit=1
        )
        if not rec:
            rec = self.env['mcp.model.option'].create({
                'provider': provider,
                'name': model_name,
                'is_discovered': True,
            })
        return rec

    @api.onchange('model_selection')
    def _onchange_model_selection(self):
        if self.model_selection:
            self.model_name = self.model_selection.name

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
        from ..mcp.providers.grok import GrokAdapter
        from ..mcp.providers.opencode import OpenCodeAdapter

        provider_map = {
            'anthropic': AnthropicAdapter,
            'openai': OpenAIAdapter,
            'gemini': GeminiAdapter,
            'ollama': OllamaAdapter,
            'grok': GrokAdapter,
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

        If neither is set, defaults to every active tool — same "empty means
        allow all" convention used by mcp.access.rule's tool_ids, so a newly
        created agent isn't silently toolless until someone remembers to
        assign a Tool Set.

        Returns:
            None — sets effective_tool_ids field (computed, not stored)
        """
        for agent in self:
            tools = agent.tool_ids
            for tool_set in agent.tool_set_ids:
                tools = tools | tool_set.tool_ids
            if not agent.tool_ids and not agent.tool_set_ids:
                tools = self.env['mcp.tool'].search([('active', '=', True)])
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
            if self.provider == 'anthropic':
                message += _('\n⚠️ Anthropic has no model-list API — this list is built-in. Start a chat to verify your API key.')
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

        if not self.api_key and self.provider != 'ollama':
            raise UserError(_('Please enter an API key first.'))

        try:
            provider = self._get_provider_instance()
            models = provider.get_available_models(self)

            if not models:
                raise UserError(_('No models found. Please check your API key.'))

            # Upsert discovered models into mcp.model.option
            ModelOption = self.env['mcp.model.option']
            for m in models:
                if not ModelOption.search([('provider', '=', self.provider), ('name', '=', m)], limit=1):
                    ModelOption.create({'provider': self.provider, 'name': m, 'is_discovered': True})

            # Auto-select first available model
            self.model_name = models[0]
            first = ModelOption.search(
                [('provider', '=', self.provider), ('name', '=', models[0])], limit=1
            )
            self.model_selection = first
            _logger.info('Set model_name to: %s (available: %s)', models[0], models)

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
