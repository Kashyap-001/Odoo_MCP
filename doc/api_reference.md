# API Reference

## HTTP Endpoints

All endpoints require authentication (user logged in to Odoo).

### POST /mcp/chat

Send message to AI agent and get reply.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "method": "call",
  "params": {
    "model": "mcp.gateway",
    "method": "run",
    "args": [],
    "kwargs": {
      "agent_id": 1,
      "user_message": "Create a lead for John Doe",
      "session_id": null,
      "active_model": "crm.lead",
      "active_res_id": 123
    }
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "result": {
    "reply": "I've created a lead for John Doe with the following details...",
    "session_id": 42,
    "tool_calls": 2,
    "input_tokens": 156,
    "output_tokens": 89,
    "cost_usd": 0.0145,
    "agent_name": "Sales Assistant"
  }
}
```

**Parameters:**
- `agent_id` (int, required): ID of agent to use
- `user_message` (str, required): User's input message
- `session_id` (int, optional): Existing session ID to continue conversation
- `active_model` (str, optional): Odoo model name for context (e.g., "crm.lead")
- `active_res_id` (int, optional): Record ID for context injection

**Errors:**
- `AccessError`: User doesn't have permission for this agent
- `UserError`: Agent misconfigured, rate limited, or provider error
- HTTP 500: Internal server error

**Example (curl):**
```bash
curl -X POST http://localhost:8069/mcp/chat \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {
      "model": "mcp.gateway",
      "method": "run",
      "kwargs": {
        "agent_id": 1,
        "user_message": "Hi there!"
      }
    }
  }' \
  --cookie "session_id=YOUR_SESSION"
```

---

### GET /mcp/agents/available

List all agents available to current user.

**Response:**
```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "name": "Sales Assistant",
      "provider": "anthropic",
      "model_name": "claude-sonnet-4-6",
      "description": "Helps with sales tasks and CRM",
      "avatar_url": "/web/image/mcp.agent/1/avatar",
      "status": "online",
      "session_count": 42,
      "color": "#667eea"
    },
    {
      "id": 2,
      "name": "Support Assistant",
      "provider": "openai",
      "model_name": "gpt-4",
      "description": "Handles helpdesk and support",
      "avatar_url": "",
      "status": "online",
      "session_count": 15,
      "color": "#764ba2"
    }
  ]
}
```

**Example (curl):**
```bash
curl -X GET http://localhost:8069/mcp/agents/available \
  --cookie "session_id=YOUR_SESSION"
```

---

### GET /mcp/tools/available

List all tools available to current user.

**Query Parameters:**
- `agent_id` (int, optional): Filter to tools for specific agent

**Response:**
```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "name": "partner_search",
      "display_name_label": "Search Partners",
      "description": "Search for customers or suppliers",
      "category": "Sales & CRM",
      "is_readonly": true,
      "requires_confirm": false
    },
    {
      "id": 2,
      "name": "sale_order_create",
      "display_name_label": "Create Sales Order",
      "description": "Create a new sales order",
      "category": "Sales & CRM",
      "is_readonly": false,
      "requires_confirm": true
    }
  ]
}
```

**Example (curl):**
```bash
curl -X GET "http://localhost:8069/mcp/tools/available?agent_id=1" \
  --cookie "session_id=YOUR_SESSION"
```

---

### GET /mcp/session/<id>/transcript

Download session transcript as plaintext file.

**Parameters:**
- `id` (int, path): Session ID

**Response:**
- Content-Type: `text/plain`
- Body: Session transcript in plaintext format

**Example (curl):**
```bash
curl -X GET http://localhost:8069/mcp/session/42/transcript \
  --cookie "session_id=YOUR_SESSION" \
  -o session_transcript.txt
```

**Output:**
```
Session: [System-Generated Name]
Agent: Sales Assistant
User: John Smith
Duration: 125.3s
Tokens: 245
Cost: $0.0145
============================================================

[USER] 2024-04-30 14:23:45
Hello, I need to create a sales order

[ASSISTANT] 2024-04-30 14:23:47
I'd be happy to help you create a sales order. Let me search for customers first.

[TOOL_CALL] 2024-04-30 14:23:48
Tool: partner_search
Args: {"name": "customer"}

[TOOL_RESULT] 2024-04-30 14:23:49
Result: [{"id": 1, "name": "John's Company", "email": ...}]

[ASSISTANT] 2024-04-30 14:23:50
I found a customer. Now I'll create the sales order...
```

---

### POST /mcp/webhook/<token>

External trigger endpoint — invokes the agent configured on the matching `mcp.webhook.trigger`.
There is no ORM auto-trigger (no create/write/delete hook); this is HTTP-only, meant to be
called from an external scheduler like n8n (Schedule Trigger → HTTP Request node).

**Parameters:**
- `token` (str, path): Webhook token (auto-generated per trigger)

**Request Body (all fields optional):**
```json
{
  "model": "crm.lead",
  "record_id": 123
}
```
`model`/`record_id` are both optional and only used to attach a specific record for context
injection — post `{}` for a recordless/scheduled run. There is no `event`/`fields` payload; the
agent's `message_template` is the prompt sent, not derived from posted field data.

**Response:**
```json
{
  "status": "success",
  "session_id": 99,
  "reply": "New lead created. I'll schedule a follow-up..."
}
```
On failure: `{"status": "error", "error": "..."}` (invalid token, record not found, or any
exception during the run — caught and returned as JSON, not an HTTP error status).

**Example (curl, recordless/scheduled):**
```bash
curl -X POST http://localhost:8069/mcp/webhook/abc123xyz789 \
  -H "Content-Type: application/json" -d '{}'
```

**Example (curl, with record context):**
```bash
curl -X POST http://localhost:8069/mcp/webhook/abc123xyz789 \
  -H "Content-Type: application/json" \
  -d '{"model": "crm.lead", "record_id": 42}'
```

If `outbound_url` is set on the trigger, the reply is also POSTed there (`Authorization: Bearer
<outbound_secret>` if set) after the run completes — see `models/mcp_webhook_trigger.py`.

---

## Python API (Internal)

### McpGateway.run()

Main orchestration method.

```python
from mcp.gateway import McpGateway

gateway = McpGateway(env, user)
result = gateway.run(
    agent_id=1,
    user_message='What are my open invoices?',
    session_id=None,
    active_model='res.partner',
    active_res_id=123,
)

# Returns:
# {
#   'reply': 'You have 3 open invoices totaling $15,000...',
#   'session_id': 42,
#   'tool_calls': 1,
#   'input_tokens': 150,
#   'output_tokens': 75,
#   'cost_usd': 0.00225,
# }
```

### Agent Model Methods

```python
# Get provider instance
provider = agent._get_provider_instance()

# Check agent status
agent.status  # 'online' | 'offline'

# Get effective tools (including from tool sets)
tools = agent.effective_tool_ids

# Test connection
agent.action_test_connection()

# View sessions
agent.action_view_sessions()
```

### Tool Dispatch

```python
from mcp.tools.dispatcher import ToolDispatcher
import json

tool = env['mcp.tool'].search([('name','=','partner_search')], limit=1)
dispatcher = ToolDispatcher()

result_json = dispatcher.dispatch(
    tool,
    {'name': 'John', 'limit': 5},
    env,
    env.user
)

result = json.loads(result_json)
# {
#   'success': True,
#   'result': [...],
# }
# OR
# {
#   'success': False,
#   'error': 'Error message'
# }
```

### Access Control

```python
# Get merged rules for user
rules = env['mcp.access.rule'].get_rules_for_user(user)

# rules = {
#   'agent_ids': RecordSet,
#   'tool_ids': RecordSet,
#   'can_view_sessions': Boolean,
#   'can_export_sessions': Boolean,
#   'rate_limit_day': Integer,
#   'rate_limit_month': Integer,
# }

# Check if user can use agent
if agent.id in rules['agent_ids'].ids:
    print("User can use this agent")

# Check if user can use tool
if tool.id in rules['tool_ids'].ids:
    print("User can use this tool")
```

### Session Management

```python
# Create session
session = env['mcp.session'].create({
    'agent_id': 1,
    'user_id': user.id,
    'source': 'chat',  # 'chat' | 'webhook'
})

# Add message
env['mcp.session.message'].create({
    'session_id': session.id,
    'role': 'user',
    'content': 'Hello',
})

# Get transcript
transcript = session._format_transcript()

# Export transcript
result = session.action_export_transcript()
# Returns: base64-encoded file download action
```

---

## Data Models

### mcp.agent
Agent configuration with LLM provider settings.

**Fields:**
- `name` — Agent display name
- `provider` — LLM provider ('anthropic', 'openai', 'gemini', 'ollama', 'grok', 'opencode')
- `api_key` — Encrypted API key
- `model_name` — Model identifier
- `system_prompt` — System/initial prompt
- `temperature` — Temperature (0-1)
- `max_tokens` — Max output tokens
- `enable_memory` — Track session summaries
- `cost_per_1k_input` — Input token cost
- `cost_per_1k_output` — Output token cost
- `effective_tool_ids` — Tools available to this agent
- `session_count` — Number of sessions (computed)
- `status` — 'online' or 'offline' (computed)

### mcp.session
Immutable audit log of conversation.

**Fields:**
- `agent_id` — Agent used
- `user_id` — User who initiated
- `state` — 'done' or 'error'
- `source` — 'chat' or 'webhook'
- `input_tokens` — Tokens sent
- `output_tokens` — Tokens received
- `estimated_cost_usd` — Cost (computed)
- `duration_seconds` — Session length (computed)
- `message_ids` — Message records
- `error_message` — If state='error'

### mcp.session.message
Individual message in conversation.

**Fields:**
- `session_id` — Parent session
- `role` — 'user'|'assistant'|'tool_call'|'tool_result'|'system'
- `content` — Message text or JSON
- `tool_name` — Tool name (if tool_call/tool_result)
- `tool_call_id` — Correlate call ↔ result
- `token_count` — Tokens used

### mcp.tool
Tool registry entry.

**Fields:**
- `name` — Identifier (snake_case)
- `display_name_label` — UI name
- `description` — Human description
- `category_id` — Tool category
- `tool_type` — 'odoo'|'external'|'mcp_server'
- `input_schema` — JSON Schema for inputs
- `output_sample` — Example output
- `is_readonly` — Read-only operation
- `requires_confirm` — Needs approval to run
- `active` — Enabled/disabled

### mcp.access.rule
Permission control per group.

**Fields:**
- `name` — Rule name
- `group_id` — User group
- `agent_ids` — Accessible agents
- `tool_ids` — Accessible tools
- `can_view_sessions` — Permission
- `can_export_sessions` — Permission
- `rate_limit_day` — Daily call limit
- `rate_limit_month` — Monthly call limit

### mcp.cost.entry
Token usage tracking.

**Fields:**
- `session_id` — Session reference
- `input_tokens` — Input token count
- `output_tokens` — Output token count
- `cost_per_1k_input` — Rate
- `cost_per_1k_output` — Rate
- `estimated_cost_usd` — Total cost

### mcp.webhook.trigger
HTTP-only trigger config (no ORM auto-trigger — see [Webhook Usage](#webhook-usage)).

**Fields:**
- `agent_id` — Agent to invoke
- `message_template` — Prompt text sent to the agent
- `token` — Inbound webhook token (auto-generated)
- `outbound_url` / `outbound_secret` — Optional POST-back to n8n after the run
- `last_triggered` / `trigger_count` — Usage tracking

### mcp.echart
AI- or user-created ECharts chart definition (`mcp_charts` module).

**Fields:**
- `name` — Chart title
- `options` — Full ECharts option JSON (dataset.source + series/xAxis/yAxis etc.)
- `data_code` — Optional Python snippet (safe_eval sandbox) that computes `options` from live ORM data
- `is_public` / `public_token` / `public_url` — Public share link fields

---

## Error Codes

| Code | Meaning | Cause |
|------|---------|-------|
| 200 | Success | Normal response |
| 400 | Bad Request | Missing required params |
| 401 | Unauthorized | Not logged in |
| 403 | Forbidden | No permission for agent/tool |
| 404 | Not Found | Agent/session doesn't exist |
| 429 | Rate Limited | Daily/monthly limit exceeded |
| 500 | Server Error | Provider error or timeout |

---

## Rate Limits

Per mcp.access.rule (grouped by user):

- **Daily limit**: Max calls per 24-hour window
- **Monthly limit**: Max calls per 30-day window
- **Provider limits**: Depends on provider (Anthropic, OpenAI, etc.)

To increase:
1. Go to **AI Gateway → Configuration → Access Rules**
2. Edit rule for user's group
3. Increase `rate_limit_day` or `rate_limit_month`
4. Save

---

## Webhook Usage

### Setup

1. Create webhook trigger: **AI Gateway → Webhook Triggers → Create**
2. Select agent to call, write a `message_template`
3. Click **Generate Token**, copy the inbound webhook URL
4. (Optional) set `outbound_url`/`outbound_secret` to have the reply POSTed to n8n after the run

### Usage

Call the webhook URL from any scheduler (n8n Schedule Trigger → HTTP Request node is the
supported pattern — there is no Odoo-side create/write hook):

```bash
curl -X POST https://myodoo.com/mcp/webhook/abc123def456 \
  -H "Content-Type: application/json" -d '{}'
```

Pass `{"model": "crm.lead", "record_id": 42}` instead of `{}` to inject a specific record's
fields into context. The agent runs synchronously and the reply is returned in the response body.
