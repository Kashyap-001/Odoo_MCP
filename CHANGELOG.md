# CHANGELOG

## [18.0.2.0.4] - Improvements Pass

### Added
- Agent model dropdown backed by `mcp.model.option` (Many2one, auto-discovered from provider API where possible) replacing free-text model selection
- Grok (xAI) and OpenCode providers — 6 providers total
- Built-in tool library grown from 14 to 23 tools across the same 3 default categories
- Session page: search box, kebab menu (pin/rename/export transcript/delete)
- Two-way webhooks: inbound `/mcp/webhook/<token>` plus an optional outbound POST back to n8n with the AI's reply after each run
- Prompt templates with placeholder substitution
- Live ECharts creation (`create_echart` tool) with a live chart preview rendered directly in chat bubbles; companion `mcp_charts` module (gallery, Style Editor, public share links)
- Structured response types beyond plain text: table, stats, list, fields, cards, image, html, chart, mixed
- `AbstractProvider` gained two required abstract methods — `format_tool_calls()` and `format_tool_result()` — replacing per-format `if/elif` branches in `gateway.py`; every provider must implement both or the class is uninstantiable

### Changed
- Provider API retries: 2 → 3 attempts, backoff 1s flat → `2s * (attempt + 1)`
- Tool execution failures now return structured `{_type: "error"}` JSON blocks instead of plain text, so the frontend renders a proper error card
- Write-tool confirmations (`create_record`/`update_record`/`delete_record`/`create_echart`) now return structured field/table cards with real post-write data instead of a bare sentence

### Removed
- Dead ORM auto-trigger fields on webhook triggers (`trigger_model`, `trigger_on`, `trigger_fields`, `domain`) — never implemented, HTTP-only via n8n is the supported pattern (migration `18.0.2.0.4`)
- `mcp.issue` internal bug tracker model and its views (migration `18.0.2.0.3`)
- `code_search`/`code_read` tools (unused)

### Fixed
- Hallucination-retry safety net was checking the wrong response key (`finish_reason` instead of every provider's actual `stop_reason`) and had never fired since the first commit; also fixed a second bug where it scanned for the wrong (synthetic date-injection) user message instead of the latest real one
- `AnthropicAdapter.format_tool_calls()` returned `[]` unconditionally, breaking any native-Anthropic session on its second tool call
- `GeminiAdapter` didn't implement the (then-named) abstract `parse_response` method, making the whole adapter uninstantiable — every real Gemini call crashed before Gemini-specific tool-calling bugs even mattered
- SSRF on `set_binary_field` (URL/IP validation added), stored XSS via unsanitized `_type:"html"` LLM content
- Agents defaulted to zero tools on creation; now default to all active tools when both `tool_ids` and `tool_set_ids` are empty, matching `mcp.access.rule`'s existing "empty = allow all" convention
- Sidebar/search sessions silently disappearing under load (shared `limit: 60` preview query starved older sessions of a preview row)
- Several binary-attachment-as-bytes-vs-JSON crashes (`read_attachment`, `execute_orm`'s `read_excel`, `get_attachments`)

## [18.0.2.0.3] - RBAC & Session UI
- Group-based menu restrictions across all 18 menu items (User/Manager/Admin tiers)
- Default access rules seeded for User and Manager groups
- Admin UI rewrite for access rules (Who/What/Permissions tabs)
- Removed `mcp.issue` internal bug tracker (migration `18.0.2.0.3`)
- Tool Scan Wizard fixes: skip transient models, drop invalid `write`/`action_confirm` scan targets, fix missing `json` import
- Stdio MCP server support (JSON-RPC over subprocess, with read timeouts)

## [18.0.2.0.2] - Grok Provider Rename
- Renamed `minimax` provider to `grok` (migration `18.0.2.0.2` updates existing `mcp.agent`/`mcp.model.option` rows)

## [18.0.2.0.1] - Model Selection Field Migration
- `model_name` selection changed from free-text Selection to a Many2one against `mcp.model.option` (migration `18.0.2.0.1` drops and recreates the column)

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
