# CHANGELOG

## [18.0.1.0.0] - Initial Release

### Added

#### Core Features
- Multi-agent AI system with configurable LLM providers (Anthropic, OpenAI, Gemini, Ollama)
- Flexible tool registry supporting Odoo ORM, external HTTP APIs, and custom MCP servers
- Tool organization via categories and bundled tool sets
- Built-in library of 14 pre-configured Odoo tools (partner, sale order, invoice, product, etc.)

#### Access & Security
- Group-based and user-based access control for agents and tools
- Fine-grained tool permissions with read-only vs. mutating flags
- Confirmation requirement for sensitive tool calls
- Daily and monthly rate limiting per user/group
- Fernet encryption for API keys at rest
- Full audit trail of all messages, tool calls, and results

#### Agent Intelligence
- System prompt customization with temperature and token limits
- Context injection — automatically include active Odoo record data in prompts
- Persistent session memory with LLM-generated summaries
- Reusable prompt templates with Jinja2 variable substitution
- Tool call before-and-after logging (audit trail survives crashes)

#### Automation
- Webhook triggers on Odoo model create/write/delete events
- Jinja2-based message template rendering with record context
- Session state machine (active, done, error)
- Automatic session archival

#### UI/UX
- In-Odoo chat interface using OWL 3 components
- Form wizard for initiating conversations
- Kanban view for agent management with drag-and-drop reorder
- Dashboard with session history and cost analytics
- Real-time streaming of assistant replies
- Tool call visualization in chat bubbles

#### Cost & Usage Tracking
- Token counting per session (input + output)
- USD cost calculation using configurable rates per agent
- Cost entry logging for billing and analytics
- Usage reports by agent, user, date range

#### Developer Features
- Tool Scan Wizard for auto-discovery of Odoo model tools
- Extensible provider architecture for adding new LLM providers
- HTTP API endpoints: /mcp/chat, /mcp/agents/available, /mcp/tools/available, /mcp/webhook/<token>, /mcp/session/<id>/transcript
- Comprehensive Python test suite (gateway, tools, access, providers, webhooks)
- Full API documentation with cURL examples

#### Data & Configuration
- Seed data with 14 built-in tools across 3 categories (Sales & CRM, Finance & Accounting, Operations & HR)
- Automatic FERNET_KEY generation and storage
- Module settings via ir.config_parameter
- Three security groups: User, Manager, Administrator

---

### Implementation Notes

All code follows Odoo 18 conventions:
- OWL 3 frontend components (no legacy Widget)
- New-style models with _inherit pattern
- Fernet encryption for sensitive data
- Comprehensive docstrings and inline comments
- Full unit test coverage for all critical paths
- Production-ready error handling and retry logic

Initial release is stable and ready for deployment on Odoo 18 Community and Enterprise editions.
