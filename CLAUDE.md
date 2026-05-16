# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI Gateway is an Odoo 18 module integrating LLMs (Anthropic, OpenAI, Gemini, Ollama, MiniMax, OpenCode) into Odoo with tool registry, access control, and in-Odoo chat. The module is self-contained — configuration is entirely via the Odoo UI after install.

## Running Tests

```bash
# Odoo integration tests
cd /home/kashyap/odoo/odoo18
./odoo-bin -c your_config.conf --test-enable --modules mcp_gateway

# pytest unit tests (mocks external calls, no DB needed)
cd /home/kashyap/odoo/v18/mcp_gateway
python -m pytest tests/ -v

# Single test
python -m pytest tests/test_gateway.py::TestMcpGateway::test_run_with_mock_provider -v

# With coverage report
python -m pytest tests/ -v --cov=mcp_gateway --cov-report=term-missing

# Lint Python files
flake8 mcp/ models/ --max-line-length=100 --ignore=E501
```

## Common Development Commands

```bash
# Update module in Odoo after code changes
cd /home/kashyap/odoo/odoo18
./odoo-bin -c your_config.conf -u mcp_gateway

# Start Odoo with dev mode (auto-reload on file changes)
./odoo-bin -c your_config.conf --dev=all

# Kill Odoo process
pkill -f "odoo-bin"
```

## Key Models

- **`mcp.agent`** — AI agent configuration (provider, model, API key, tools)
- **`mcp.tool`** — Tool registry (Odoo, external API, or MCP server)
- **`mcp.session`** — Chat session with message history and memory
- **`mcp.access.rule`** — Group/user access control with rate limits
- **`mcp.cost.entry`** — Token usage and cost tracking per session
- **`mcp.prompt.template`** — Reusable prompt fragments with variable substitution

## Architecture

### Three-Layer Flow

```
Odoo UI (chatter) → Gateway Engine (mcp/gateway.py) → LLM Providers + Tool Dispatcher
```

1. User types in chatter → `mcp.session.message_post()` detects user comment and calls `McpGateway.run()`
2. Gateway builds tool specs from agent tools + external MCP servers, injects context/memory into system prompt
3. `_call_provider_with_tools()` loops: call provider → if tool calls, execute and format results → re-call. Up to 20 turns.
4. Final text posted back to chatter as "[AgentName]: reply"

### Agentic Loop (`mcp/gateway.py` lines 437–547)

The core `_call_provider_with_tools()` method runs the multi-turn loop. For each tool call:
- **Odoo tools** → `ToolDispatcher.dispatch()`
- **External MCP tools** (`ext_*` prefixed) → proxied via HTTP JSON-RPC to configured external servers
- Tool results appended to messages in **provider-specific format**:
  - **Anthropic**: `{'role': 'user', 'content': [{'type': 'tool_result', ...}]}`
  - **OpenAI/MiniMax/OpenCode**: `{'role': 'tool', 'tool_call_id': ..., 'content': ...}`
  - **Gemini**: `{'role': 'user', 'parts': [{'functionResponse': ...}]}`
  - **Ollama**: same as OpenAI
- System message stripped from the message array for OpenAI/Ollama/Gemini/MiniMax/OpenCode (they handle it internally); kept for Anthropic via SDK `system` parameter
- Token counts accumulated across all turns

### Provider Base URL Patterns

Each provider has a default base URL:
- **Anthropic**: `https://api.anthropic.com`
- **OpenAI**: `https://api.openai.com/v1`
- **Gemini**: `https://generativelanguage.googleapis.com/v1beta`
- **Ollama**: `http://localhost:11434`
- **MiniMax**: `https://api.minimax.chat/v1`
- **OpenCode Zen**: `https://opencode.ai/zen` (base only, endpoints added per-model-type)

**Important**: When adding custom `api_base_url` in agent config, don't include `/v1` if the provider adapter already appends it — avoid double paths like `/v1/v1/`.

### Provider Interface (`mcp/providers/base.py`)

All providers inherit `AbstractProvider` and implement: `build_headers()`, `build_payload()`, `parse_response()`, `get_available_models()`, `call()`. Each formats tools differently:
- **Anthropic**: official SDK, tools as `[{name, description, input_schema}]`, system via `system` param
- **OpenAI**: official SDK, `tools` array (with `type: 'function'`) and `tool_choice: 'auto'`
- **Gemini**: official SDK, `function_declarations` in protos format
- **OpenCode/MiniMax**: httpx with OpenAI-compatible format
- **Ollama**: direct HTTP, OpenAI-compatible format

Providers are instantiated via `agent._get_provider_instance()` which maps `agent.provider` → adapter class.

### Tool Dispatcher (`mcp/tools/dispatcher.py`)

Routes execution by `tool.tool_type`:
- **`odoo`**: calls Odoo model methods — `search_read`, `create`, `write`, or arbitrary custom method
- **`external`**: HTTP GET/POST/PUT/DELETE to external endpoints with configurable auth
- **`mcp_server`**: POST to custom MCP server `/call` endpoint

### External MCP Servers

External MCP servers are configured in `mcp.external.server` (HTTP transport only — stdio is for Claude Desktop, not in-process). At chat runtime:
- `_get_external_mcp_tools()` calls `tools/list` via JSON-RPC 2.0 for each active HTTP server
- Tool names prefixed `ext_{server_name}_{tool_name}` to avoid conflicts
- `_call_external_mcp_tool()` proxies calls via `tools/call` JSON-RPC with auth headers
- Auth types supported: Bearer token, API Key header

### MCP HTTP Endpoint (`controllers/mcp_protocol_controller.py`)

Exposes Odoo as an MCP server for external AI clients (e.g., Claude Desktop web). Routes:
- `POST /mcp/rpc` — JSON-RPC 2.0 endpoint implementing `tools/list` and `tools/call`
- `GET /mcp/tools` — REST-style tools list
- `POST /mcp/tools/call` — REST-style tool call
- Auth via Odoo session (`auth='user'`)

### Audit Trail

Every tool call is logged **BEFORE** execution (`role='tool_call'`) and **AFTER** execution (`role='tool_result'`) in `mcp.session.message`. The audit trail survives crashes because the log is written before dispatch.

### Access Control

`mcp.access.rule.get_rules_for_user()` merges all matching rules (OR logic). Admins (`group_mcp_admin`) bypass all checks.

### API Key Encryption

API keys are Fernet-encrypted on write via `create()`/`write()` overrides on `mcp.agent`. Decrypted only at provider call time via `_decrypt_api_key()`. The key lives in `ir.config_parameter` as `mcp_gateway.fernet_key`. External MCP server auth credentials use the same encryption via `mcp.external.server` model.

### Dynamic Tool Guidance

The system prompt automatically includes dynamically generated tool guidance via `_build_tool_guidance()` in `gateway.py`. This includes categories from tool descriptions, external tool servers, selection rules, and a quick reference of available tools. Tool descriptions should follow the F1 standard with category prefixes like `[SALES & CRM]`, `[FINANCE]`, `[INVENTORY & OPERATIONS]`.

### Tool Testing

Each tool has a "Test Tool" button in its form view. The tool list view has a "Test All Tools" button that runs all active tools and reports pass/fail status with response times.

### Tool Call Fallback System

When providers get stuck in a tool call loop (returning tool calls instead of final answers), the gateway uses a three-layer fallback:
1. **Retry with fallback model**: Uses `FALLBACK_MODELS` dict to retry with a simpler model (`minimax-m2.5-free` for OpenCode, `gpt-4o-mini` for OpenAI, etc.)
2. **Compatibility mode**: If fallback model fails, injects tool result as plain text user message instead of provider-specific `tool_result` format
3. **Error display**: Only shows error message if all fallbacks fail

This handles MiniMax/OpenCode issues where the model returns errors like "tool call result does not follow tool call" when processing tool results.

## Odoo 18 Specifics

- XML views use `<list>` not `<tree>`, domain expressions directly on fields (no deprecated `attrs`/`states`)
- Use `widget="badge"` for status selection fields — `widget="statusbar"` causes OwlError
- Use `_()` for all user-facing strings
- Transient models for wizards (default `_transient=True`)

## Adding a New Provider

1. Create `mcp/providers/{name}.py` extending `AbstractProvider`
2. Implement: `build_headers()`, `build_payload()`, `parse_response()`, `call()`, `get_available_models()`
3. Register in `mcp/providers/__init__.py` PROVIDER_MAP and `models/mcp_agent.py` provider selection
4. Add to `_append_tool_result()` if tool result format differs from OpenAI's `role: 'tool'`

## Adding a New Built-in Tool

Define in `mcp/tools/builtin_tools.py` BUILTIN_TOOLS array with complete JSON Schema `input_schema`, then register in `data/default_tools.xml`. Tool names must be snake_case (enforced by `@api.constrains` in `mcp_tool.py`).

## Supported Providers

- **Anthropic** — Claude models (Sonnet, Haiku, Opus)
- **OpenAI** — GPT-4o, GPT-4o-mini, GPT-4 Turbo
- **Google Gemini** — Gemini Pro/Flash models
- **Ollama** — Local models (Llama, Mistral, etc.)
- **MiniMax** — MiniMax chat models
- **OpenCode Zen** — Multi-provider gateway at `https://opencode.ai/zen`:
  - `/v1/chat/completions` — MiniMax, Qwen, GLM, Kimi, DeepSeek
  - `/v1/responses` — GPT 5.x series
  - `/v1/messages` — Claude models
  - Free tier: minimax-m2.5-free, deepseek-v4-flash-free, big-pickle, ring-2.6-1t-free, nemotron-3-super-free

## Common Issues

- **"No agents available"** — No access rule grants your user an agent. Create an access rule in AI Gateway → Access Rules.
- **"Connection failed"** — Agent's API key is wrong or provider URL unreachable. Use "Test Connection" button to debug.
- **"Rate limit exceeded"** — Daily/monthly quota exceeded. Check AI Gateway → Sessions to see usage, or ask admin to increase limit.
- **"Tool not found" / "Access denied"** — User doesn't have permission. Ask admin to add tool to an access rule.
- **Ollama timeout** — Ensure `ollama serve` is running locally.

## Dependencies

```bash
pip install requests cryptography anthropic openai google-generativeai httpx
```