{
    'name': 'AI Gateway',
    'version': '18.0.2.0.0',
    'category': 'Technical',
    'license': 'LGPL-3',
    'author': 'AI Gateway Contributors',
    'website': 'https://github.com/your-org/odoo-mcp-gateway',
    'summary': 'AI Agent Gateway for Odoo 18 — LLM Integration with Tool Registry',
    'description': '''
AI Gateway is a production-ready Odoo 18 module for integrating Large Language Models
directly into your Odoo environment. Configure multiple AI agents, register tools
(Odoo-native and external), control access per user/group, and enable end users to
chat with agents directly inside Odoo.

Features:
  • Multi-provider LLM support (Anthropic, OpenAI, Gemini, Ollama, MiniMax, OpenCode)
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
  • Python packages: requests, cryptography, anthropic, openai, google-generativeai, mcp
  • At least one AI provider API key (Anthropic, OpenAI, Gemini, MiniMax) or Ollama instance
    ''',
    'depends': ['base', 'web', 'mail', 'bus'],
    'external_dependencies': {
        'python': ['requests', 'cryptography', 'anthropic', 'openai', 'google-generativeai', 'mcp', 'httpx'],
    },
    'data': [
        'security/mcp_security.xml',
        'security/ir.model.access.csv',
        'security/mcp_record_rules.xml',
        'data/default_tools.xml',
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
        'views/mcp_issue_views.xml',
        'views/mcp_echart_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mcp_gateway/static/src/css/mcp_chat.css',
            'mcp_gateway/static/src/js/mcp_chat_widget.js',
            'mcp_gateway/static/src/xml/mcp_chat_widget.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
}
