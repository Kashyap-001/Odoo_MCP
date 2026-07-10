{
    'name': 'Ai ChatBot',
    'version': '18.0.1.0.0',
    'category': 'Technical',
    'license': 'LGPL-3',
    'author': 'AI Gateway Contributors',
    'website': 'https://github.com/your-org/odoo-mcp-gateway',
    'summary': 'Ai ChatBot for Odoo 18 — LLM Integration with Tool Registry',
    'description': '''
Ai ChatBot is a production-ready Odoo 18 module for integrating Large Language Models
directly into your Odoo environment. Configure multiple AI agents, register tools
(Odoo-native and external), control access per user/group, and enable end users to
chat with agents directly inside Odoo.

Features:
  • Multi-provider LLM support (Anthropic, OpenAI, Gemini, Ollama, Grok/xAI, OpenCode)
  • MCP Server for Odoo - expose Odoo tools to external AI clients
  • Connect to external MCP servers (WhatsApp, YouTube, etc.)
  • No-code tool registration (Odoo ORM, HTTP APIs, custom MCP servers)
  • Group-based access control with rate limiting
  • Session audit trail with token usage and cost tracking
  • Context injection and persistent session memory
  • Webhook triggers for automation
  • In-Odoo chat interface with OWL 3 components
  • Full HTTP API for external integrations

Requirements:
  • Python packages: requests, cryptography, openpyxl, xlrd — all four are already
    required by Odoo itself, so this module needs ZERO extra pip installs. Every
    LLM provider (Anthropic, OpenAI, Gemini, Grok, Ollama, OpenCode) is called via
    plain HTTPS requests, no vendor SDK.
  • wkhtmltopdf (system binary, not pip) for the generate_export_file PDF export —
    already required by Odoo itself for its own PDF reports
  • At least one AI provider API key (Anthropic, OpenAI, Gemini, Grok, OpenCode) or Ollama instance
    ''',
    'depends': ['base', 'web', 'mail', 'bus'],
    'external_dependencies': {
        # requests/cryptography/openpyxl/xlrd are all already required by Odoo
        # itself (see odoo/requirements.txt) — listed here for clarity, not
        # because this module adds anything beyond what Odoo already needs.
        'python': ['requests', 'cryptography', 'openpyxl', 'xlrd'],
    },
    'data': [
        'security/mcp_security.xml',
        'security/ir.model.access.csv',
        'security/mcp_record_rules.xml',
        'data/mcp_dashboard_data.xml',
        'data/mcp_model_options.xml',
        'data/default_tools.xml',
        'data/default_prompt_templates.xml',
        'data/default_access_rules.xml',
        'data/mcp_cron.xml',
        'views/mcp_agent_views.xml',
        'views/mcp_tool_views.xml',
        'views/mcp_tool_category_views.xml',
        'views/mcp_tool_set_views.xml',
        'views/mcp_access_rule_views.xml',
        'views/mcp_session_views.xml',
        'views/mcp_prompt_template_views.xml',
        'views/mcp_webhook_trigger_views.xml',
        'views/mcp_chat_wizard_views.xml',
        'views/mcp_tool_scan_wizard_views.xml',
        'views/mcp_connection_test_wizard_views.xml',
        'views/mcp_cost_entry_views.xml',
        'views/mcp_external_server_views.xml',
        'views/mcp_echart_views.xml',
        'views/mcp_dashboard_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mcp_gateway/static/lib/echarts.min.js',
            'mcp_gateway/static/src/css/mcp_chat.css',
            'mcp_gateway/static/src/js/mcp_chat_widget.js',
            'mcp_gateway/static/src/xml/mcp_chat_widget.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
    'images': ['static/description/AiChatbot.gif']
}


