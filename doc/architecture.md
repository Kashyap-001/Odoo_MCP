# Architecture Guide

## System Overview

The AI Gateway is a three-layer system orchestrating AI agent interactions within Odoo.

```
┌─────────────────────────────────────────────────────────────┐
│ UI Layer (OWL Components + Wizards)                        │
│ • Chat Widget — Real-time messaging interface              │
│ • Chat Wizard — Session management                         │
│ • Tool Scanner — Auto-discovery UI                         │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP/RPC
┌────────────────────┴────────────────────────────────────────┐
│ API Layer (HTTP Controllers + RPC)                         │
│ • POST /mcp/chat — Main chat endpoint                       │
│ • GET /mcp/agents/available — Agent discovery              │
│ • GET /mcp/tools/available — Tool discovery                │
│ • GET /mcp/session/<id>/transcript — Export               │
│ • POST /mcp/webhook/<token> — External triggers            │
└────────────────────┬────────────────────────────────────────┘
                     │ Python
┌────────────────────┴────────────────────────────────────────┐
│ Core Layer (Orchestration & Models)                        │
│                                                             │
│ McpGateway (11-Step Process):                             │
│  1. Load agent, validate access                            │
│  2. Check rate limits                                      │
│  3. Load/create session                                    │
│  4. Inject context from active record                      │
│  5. Inject memory from past sessions                       │
│  6. Build tool specifications                              │
│  7. Build message history                                  │
│  8. Call provider with retry logic (2x, backoff)          │
│  9. Execute tool calls with BEFORE/AFTER logging          │
│  10. Save session with tokens                             │
│  11. Summarize for memory                                  │
│                                                             │
│ Supporting Models:                                         │
│  • mcp.agent — LLM configuration                           │
│  • mcp.session — Conversation audit log                    │
│  • mcp.session.message — Message history                   │
│  • mcp.tool — Tool registry                                │
│  • mcp.access.rule — Permission control                    │
│  • mcp.cost.entry — Usage tracking                         │
└────────────────────┬────────────────────────────────────────┘
                     │ Provider Interface
┌────────────────────┴────────────────────────────────────────┐
│ Provider Layer (LLM Adapters)                              │
│                                                             │
│ AbstractProvider (Base Class):                             │
│  • build_headers() — Authentication                        │
│  • build_payload() — Message formatting                    │
│  • parse_response() — Format standardization               │
│  • get_available_models() — Model list                     │
│  • format_tool_calls() — Assistant history entry shape      │
│  • format_tool_result() — Tool-result message shape         │
│  (all 6 are @abstractmethod — TypeError if any is missing) │
│                                                             │
│ Implementations:                                           │
│  • AnthropicProvider (Claude API)                          │
│  • OpenAIProvider (GPT API)                                │
│  • GeminiProvider (Gemini API)                             │
│  • OllamaProvider (Local models)                           │
│  • GrokProvider (xAI API)                                  │
│  • OpenCodeProvider (OpenCode API)                         │
│                                                             │
│ All providers:                                             │
│  • Return standardized dict: {text, stop_reason,          │
│    tool_calls, input_tokens, output_tokens}              │
│  • Implement retry logic (2 max, exponential backoff)      │
│  • Support tool calling (function definition)              │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP
┌────────────────────┴────────────────────────────────────────┐
│ External Systems                                           │
│ • Claude API (Anthropic)                                   │
│ • OpenAI GPT (OpenAI)                                      │
│ • Gemini API (Google)                                      │
│ • Ollama Local (local-llm)                                 │
│ • Grok API (xAI)                                           │
│ • OpenCode API                                             │
│ • External HTTP APIs (via tool dispatch)                   │
│ • Odoo ORM (built-in tools)                               │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow Example

### Chat Message Flow

```
User → Chat Widget → POST /mcp/chat
  ↓
Controller validates request
  ↓
McpGateway.run() starts:
  • Load agent (mcp.agent)
  • Check access (mcp.access.rule)
  • Check rate limit
  • Load session (mcp.session)
  ↓
Inject context:
  • System prompt + active record fields
  • Memory summaries from past sessions
  ↓
Provider call:
  • Format messages (each provider handles its own wire format via
    format_tool_calls()/format_tool_result() — no more format-specific
    if/elif branching in the gateway)
  • Include tool definitions
  • Retry on 500/429/timeout (2 retries in AbstractProvider.call(),
    plus an outer 3-retry/2s-4s-6s-backoff wrapper in gateway.py for
    providers that return an in-band error string instead of raising)
  ↓
Tool loop (if stop_reason="tool_call"):
  • Dispatcher routes to Odoo ORM / HTTP / MCP
  • Log BEFORE execution (audit trail)
  • Log AFTER execution with result
  ↓
Save session:
  • mcp.session_message for each message
  • mcp.cost_entry for token usage
  • mcp.agent_memory for summary
  ↓
Return reply → Chat Widget → User
```

## Tool Dispatch Paths

### Path 1: Odoo ORM Tools

```
mcp.tool (tool_type='odoo')
  → dispatcher.dispatch()
    → model = env[odoo_model]
    → method = getattr(model, odoo_method)
    → result = method(**arguments)
    → return JSON
```

### Path 2: External HTTP Tools

```
mcp.tool (tool_type='external')
  → dispatcher.dispatch()
    → url = build_url(external_url, arguments)
    → headers = build_auth_headers(auth_type, auth_value)
    → response = requests.get/post/etc(url, headers, body)
    → result = extract_path(response.json(), output_path)
    → return JSON
```

### Path 3: MCP Server Tools

```
mcp.tool (tool_type='mcp_server')
  → dispatcher.dispatch()
    → POST {mcp_server_url}/call
    → body = {tool_name, arguments}
    → result = response.json()
    → return JSON
```

Stdio-based MCP servers (spawned as a subprocess instead of called over HTTP) use the same
dispatch path but speak newline-delimited JSON-RPC over stdin/stdout, with a `select()`-based
read timeout so a hung external process can't block a worker forever.

### create_echart — a special-cased terminal tool

`create_echart` is an Odoo ORM tool like Path 1, but its reply is built deterministically in
`gateway.py`'s `_build_terminal_block` (not left to the model to phrase): it re-reads the
resulting `mcp.echart` record and returns a `{_type: "chart", chart_id, options}` structured
block, which the frontend renders as a live ECharts instance in the chat bubble rather than a
static image or text summary.

## Access Control Model

### Rule Evaluation (get_rules_for_user)

```
Admin? → YES → Full access (all agents, all tools)
  ↓ NO
User groups? → Search mcp.access.rule matching groups
  ↓
For each rule:
  • agent_ids: Which agents this group can access
  • tool_ids: Which tools this group can use
  • can_view_sessions: Permission to list sessions
  • can_export_sessions: Permission to download transcripts
  • rate_limit_day: Max calls per 24h (0=unlimited)
  • rate_limit_month: Max calls per 30d (0=unlimited)
  ↓
Merge all matching rules: OR logic (any rule grants access)
  ↓
Return merged permissions dict
```

### Rate Limiting

```
Daily limit check:
  cutoff_time = now - 24h
  recent = sessions where user_id=user AND create_date >= cutoff_time
  if len(recent) >= rate_limit_day:
    raise UserError("Rate limit exceeded")

Monthly limit check:
  Similar logic with 30 day window
```

## Session Audit Trail

Every conversation is immutable audit log:

```
mcp.session
  ├─ agent_id — Which agent was used
  ├─ user_id — Who initiated the session
  ├─ state — 'done' | 'error'
  ├─ input_tokens — Tokens sent to provider
  ├─ output_tokens — Tokens received from provider
  ├─ estimated_cost_usd — Token cost calculation
  └─ message_ids → mcp.session.message
       ├─ role — 'user' | 'assistant' | 'tool_call' | 'tool_result' | 'system'
       ├─ content — The actual text/data
       ├─ tool_name — For tool_call/tool_result roles
       ├─ tool_call_id — Correlate tool_call → tool_result
       └─ create_date — Timestamp

Access control:
  • Users can only view their own sessions
  • Managers can view all sessions
  • Exports only allowed if can_export_sessions=True
  • Sessions are never deleted (append-only)
```

## Memory System

Optional (enable via agent.enable_memory=True):

```
After each session:
  1. Load last 5 sessions for this user+agent
  2. Call provider to summarize each → 3-sentence summary
  3. Store in mcp.agent.memory
  
On next conversation:
  1. Fetch last 5 memories
  2. Append to system prompt as "PAST INTERACTION SUMMARIES"
  3. Provider uses context to maintain continuity
  
Benefit: Agent remembers user preferences/context across chats
Cost: ~10 extra tokens per call (summarization)
```

## Encryption Strategy

API keys never stored/logged in plaintext:

```
On Create/Write (api_key):
  → plaintext_key = params['api_key']
  → fernet_key = _get_fernet_key()  # Auto-generated per instance
  → encrypted = Fernet(fernet_key).encrypt(plaintext_key)
  → Save encrypted blob to DB
  → Discard plaintext from memory

On Read (when needed):
  → encrypted_blob = record.api_key
  → fernet_key = _get_fernet_key()
  → plaintext = Fernet(fernet_key).decrypt(encrypted_blob)
  → Use plaintext for API call
  → Key never stored in logs

Storage:
  • Fernet key stored in ir.config_parameter (single per Odoo instance)
  • All agent api_key fields use same encryption key
  • DB backup includes encrypted keys only
```

## Error Handling

### Provider Errors (with Retry)

```
try:
  call provider
except requests.Timeout:
  if retry_count < 2:
    sleep(backoff_time)
    retry
  else:
    raise UserError("Provider timeout after 2 retries")
except requests.HTTPError (500):
  if retry_count < 2:
    retry
  else:
    raise UserError("Provider server error")
except requests.HTTPError (429):
  if retry_count < 2:
    sleep(backoff_time * 2)
    retry
  else:
    raise UserError("Rate limited by provider")
except Exception:
  raise UserError("Provider error: " + str(e))
```

### Tool Dispatch Errors

```
Tool execution never raises exception:
  try:
    result = execute_tool()
  except Exception as e:
    result = JSON: {success: False, error: str(e)}
    
Log both:
  BEFORE: tool_call message (for audit)
  AFTER: tool_result message (success or error)
  
If tool fails, continue → provider gets error JSON
Provider can retry, correct args, or inform user
```

### Access Control Errors

```
gateway.run() checks:
  1. Agent exists
  2. User in allowed groups (AccessError if denied)
  3. User rate limit (UserError if exceeded)
  
These errors are fatal (raised immediately)
Tool execution errors are not (returned as error JSON)
```
