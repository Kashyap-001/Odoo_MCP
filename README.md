# AI Gateway for Odoo 18

## Overview

AI Gateway is a production-ready Odoo 18 module that integrates Large Language Models (LLMs) directly into your Odoo environment. Administrators can configure multiple AI agents, register tools (Odoo-native and external APIs), control granular access per user/group, and enable end users to chat with agents directly within Odoo. The module provides a secure, audited, and scalable foundation for agentic AI workflows.

**Who it's for:**
- Enterprise customers wanting to automate business processes with AI
- Developers building AI-assisted workflows inside Odoo
- Organizations requiring audit trails and cost tracking for LLM usage

**Key value propositions:**
- No-code AI agent configuration with UI-driven setup
- Seamless Odoo ORM integration — tools can read/write any Odoo model
- Multi-provider support (Anthropic, OpenAI, Gemini, Ollama)
- Granular access control and audit logging
- Token usage and cost tracking per user/agent
- Session management with memory persistence

---

## Features

### Core Capabilities
- **AI Agent Profiles** — Create and manage agent configurations with system prompts, temperature, token limits, and model selection
- **Tool Registry** — Register Odoo models, external APIs, and custom MCP servers as callable tools
- **Multi-Provider Support** — Anthropic Claude, OpenAI GPT, Google Gemini, and Ollama (local models)
- **Tool Categories** — Organize tools by domain (Sales, Finance, HR, etc.)
- **Tool Sets** — Bundle related tools and assign to agents

### Access & Security
- **Group-Based Access Control** — Restrict agent/tool access to specific Odoo groups
- **User-Level Permissions** — Grant or deny tools per individual user
- **Rate Limiting** — Enforce daily and monthly API call quotas per user
- **Session Audit Log** — Full message history with timestamps, tokens, and costs
- **API Key Encryption** — Fernet encryption at rest, never logged or transmitted plaintext

### Agent Intelligence
- **Context Injection** — Automatically inject active Odoo record data into prompts
- **Session Memory** — Persist summaries of past conversations to inform future replies
- **Prompt Templates** — Reusable prompt fragments with variable substitution
- **Tool Call Logging** — Every tool invocation logged before execution (audit trail exists even on failure)
- **Cost Tracking** — Token usage and USD cost calculated per session

### Automation
- **Webhook Triggers** — Automatically fire agents on Odoo model create/write/delete events
- **Jinja2 Message Templates** — Dynamic prompt rendering with record data
- **Session State Machine** — Active → Done → Error state tracking

### UI/UX
- **In-Odoo Chat Interface** — OWL 3 component for conversational agent interaction
- **Form Wizard Integration** — Launch chat from any Odoo form or card
- **Dashboard & Analytics** — View session history, cost trends, agent usage
- **Kanban Agent Cards** — Drag-and-drop reorder, quick-action buttons
- **Message Streaming** — Real-time assistant replies with animated loading

### Developer Features
- **Tool Scan Wizard** — Auto-discover and register Odoo model tools
- **Extensible Provider Architecture** — Add new LLM providers by extending AbstractProvider
- **Custom MCP Server Support** — Call external Model Context Protocol servers
- **Full HTTP API** — /mcp/chat, /mcp/agents, /mcp/tools, /mcp/webhook endpoints
- **Comprehensive Test Suite** — Unit tests for gateway, tools, access, providers, webhooks

---

## Requirements

- **Odoo 18 Community or Enterprise** (v18.0.1.0+)
- **Python 3.12+**
- **Python packages:** `requests`, `cryptography`
- **At least one AI provider:**
  - Anthropic API key (https://console.anthropic.com), OR
  - OpenAI API key (https://platform.openai.com/api-keys), OR
  - Google Gemini API key (https://aistudio.google.com), OR
  - Running Ollama instance (http://localhost:11434)

---

## Installation

### Step 1: Clone / Copy Module
```bash
# Copy mcp_gateway into your Odoo addons directory
cp -r mcp_gateway /path/to/odoo/addons/
cd /path/to/odoo
```

### Step 2: Install Dependencies
```bash
pip install requests cryptography
```

### Step 3: Restart Odoo
```bash
# Kill existing Odoo process
pkill -f "odoo-bin"

# Start Odoo with the mcp_gateway module
odoo-bin -c /path/to/odoo.conf --addons-path=/path/to/addons
```

### Step 4: Enable Developer Mode
In your Odoo browser tab, click your user avatar (top-right) → "Settings" → toggle "Developer mode" ON.

### Step 5: Install Module
Navigate to **Apps** → search for `mcp_gateway` → click module → **Install**.

### Step 6: Access AI Gateway Menu
After install completes, you'll see **AI Gateway** in the main menu (left sidebar). Click it to see the full interface.

---

## Quick Start (5 Minutes)

### 1. Create Your First Agent
1. Go to **AI Gateway** → **Agents** → **+ New**
2. **Name:** e.g., `Sales Assistant`
3. **Provider:** Choose `Anthropic` (requires API key from https://console.anthropic.com)
4. **Model name:** Auto-populated with `claude-sonnet-4-6`
5. **API Key:** Paste your Anthropic API key
6. Click **Test Connection** — should show green notification "Connection successful!"
7. **Save**

### 2. Enable Tools
1. Still in your agent, scroll down to the **Tools** tab
2. Click **Tool Sets** dropdown → Select a bundle like "Sales & CRM"
3. Alternatively, click **Add a line** under **Direct Tools** and pick individual tools
4. **Save**

### 3. Set Up Access
1. Navigate to **AI Gateway** → **Access Rules** → **+ New**
2. **Name:** e.g., `Sales Team Access`
3. **Groups:** Select your Odoo user group(s) (e.g., "Sales / User")
4. **Agents:** Select the agent you just created
5. **Rate Limit (per day):** e.g., 50 calls/day (leave blank for unlimited)
6. **Save**

### 4. Start Chatting
1. Go back to **AI Gateway** → **Agents**
2. Find your agent card → Click **Chat** button
3. A wizard opens with the agent pre-selected
4. Type a message and hit Enter (or click Send)
5. Watch the agent respond with tool calls shown as gray pills

### 5. View Session History
1. Go to **AI Gateway** → **Sessions**
2. Click any session to see the full transcript, tokens used, and cost

---

## Configuration

### Encryption Key (FERNET_KEY)

All API keys are encrypted at rest using Fernet symmetric encryption. Odoo automatically:
- Generates a 32-byte key on first install
- Stores it in `ir.config_parameter` as `mcp_gateway.fernet_key`
- Never logs or exposes the plaintext key

If you need to rotate the key or transfer between servers:
```python
# In Odoo console
from cryptography.fernet import Fernet
new_key = Fernet.generate_key()
env['ir.config_parameter'].set_param('mcp_gateway.fernet_key', new_key.decode())
```

### Rate Limiting

Set per access rule (e.g., Sales Team can call agents max 50 times/day):
- **rate_limit_day:** Enforced 24-hour rolling window. Checked before every call.
- **rate_limit_month:** Enforced calendar month. Useful for quota planning.
- Leave both at `0` for unlimited.

When exceeded, user sees: `"Rate limit of 50 calls/day exceeded. Reset at 2025-05-01 14:32."`

### Webhook Triggers

Automatically invoke agents on model events:

1. Go to **AI Gateway** → **Webhook Triggers** → **+ New**
2. **Name:** e.g., `Auto-email on new lead`
3. **Agent:** Select your agent
4. **Trigger Model:** e.g., `crm.lead`
5. **Trigger On:** `On create`
6. **Domain:** e.g., `[['country_id.code','=','US']]` (optional filter)
7. **Message Template:** 
   ```
   New lead: {record.name}
   Phone: {record.phone}
   Email: {record.email_from}
   Please draft an initial outreach email.
   ```
8. Click **Generate Token** to create a webhook secret
9. **Save**

Now whenever a US lead is created, the agent automatically generates an outreach email in a new session.

### Cost Tracking

Configure per-agent LLM pricing:
1. Go to **AI Gateway** → **Agents** → your agent → **Access & Limits** tab
2. **Cost per 1K input tokens:** e.g., `0.003` for Claude Sonnet
3. **Cost per 1K output tokens:** e.g., `0.015` for Claude Sonnet
4. **Save**

Token usage and cost are automatically logged in `mcp.cost.entry` after each session.

---

## Architecture Overview

AI Gateway follows a **three-layer architecture:**

```
┌─────────────────────────────────────────────┐
│  Odoo UI Layer (OWL 3 Components)           │
│  - Chat wizard, dashboard, forms            │
└────────────────┬────────────────────────────┘
                 │ HTTP JSON-RPC
┌────────────────▼────────────────────────────┐
│  Gateway Engine (mcp/gateway.py)            │
│  - Agentic loop: prompt → provider → tools  │
│  - Access check, rate limit, context inj.   │
│  - Memory & cost tracking                   │
└────────────────┬────────────────────────────┘
                 │
      ┌──────────┼──────────┬──────────┐
      │          │          │          │
    ┌─▼──┐   ┌──▼──┐   ┌───▼──┐   ┌──▼───┐
    │ORM │   │HTTP │   │MCP   │   │Local │
    │    │   │API  │   │Serv. │   │Ollama│
    └────┘   └─────┘   └──────┘   └──────┘
```

**Full request lifecycle:**

1. User types message in chat wizard
2. `POST /mcp/chat` → checks access rules
3. Gateway loads agent, provider, tool specs
4. Injects system prompt (with context + memory)
5. Calls LLM provider (Anthropic, OpenAI, etc.)
6. Provider returns reply + tool calls
7. For each tool call:
   - Log the call (audit trail)
   - Dispatch to ORM, HTTP, or MCP server
   - Capture result
   - Send back to provider for next turn
8. Session complete → log tokens, cost, summary
9. Return final reply to user

See [doc/architecture.md](doc/architecture.md) for detailed diagrams.

---

## Security Notes

- **API Key Encryption:** All provider API keys are encrypted with Fernet at rest. Only decrypted in memory during provider calls. Never logged.
- **Access Control:** Enforced at three levels:
  - Group/user membership checked against access rules
  - Tool-level permissions (which tools user can invoke)
  - Record-level domain filters on Odoo ORM tools
- **Audit Trail:** Every message, tool call, and result is logged in `mcp.session` with timestamp, user, duration, and cost.
- **Mutating Tools:** Require explicit user confirmation before executing (e.g., creating a sale order).
- **Rate Limiting:** Prevents runaway API costs via daily/monthly quotas.

---

## Troubleshooting

### "No agents available"
**Cause:** No access rule grants your user an agent.  
**Fix:**
1. Ask your Odoo admin to create an access rule for your group
2. Or log in as admin, go to AI Gateway → Access Rules → Create rule for your group

### "Connection failed" / "Invalid API key"
**Cause:** Agent's API key is wrong or provider URL is unreachable.  
**Fix:**
1. Go to AI Gateway → Agents → your agent
2. Click **Test Connection** to see the exact error
3. Verify API key is copied correctly (no extra spaces)
4. Ensure Odoo server has internet access to provider
5. Try a different model_name (e.g., `gpt-4o` vs `gpt-4-turbo`)

### Provider timeout (message says "Timeout after 15s")
**Cause:** LLM provider is slow or unreachable.  
**Fix:**
1. Try again — sometimes providers are temporarily slow
2. Go to AI Gateway → Tools → the tool causing timeout
3. Increase **Timeout Seconds** (e.g., from 15 to 30)
4. If using Ollama locally, ensure it's running: `ollama serve`

### Rate limit hit (message says "Rate limit of N calls/day exceeded")
**Cause:** Your access rule's daily quota was exceeded.  
**Fix:**
1. Ask your admin to increase the rate_limit_day on your access rule
2. Or wait until the next day (24-hour rolling window)
3. Check AI Gateway → Sessions to see recent calls

### "Tool not found" / "Access denied to tool X"
**Cause:** You don't have permission to use that tool.  
**Fix:**
1. Ask admin to add the tool to an access rule for your group
2. Or check if the tool is disabled (set active=False)

### OWL component doesn't load (chat widget missing)
**Cause:** Module not installed, or browser cache stale.  
**Fix:**
1. Hard-refresh browser: `Ctrl+Shift+R` (or `Cmd+Shift+R` on Mac)
2. Verify module is installed: Go to Apps → search `mcp_gateway` → should show green checkmark
3. Check browser console (F12) for JS errors

### Sessions not appearing in history
**Cause:** Session record rule filtering them out.  
**Fix:**
1. Only sessions you created are visible to non-admin users
2. Ask admin to view all sessions if needed (managers/admins can see all)

---

## Contributing

Please see [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and contribution guidelines.

---

## License

LGPL-3. See LICENSE file in module root.

---

## Support & Feedback

For bugs, feature requests, or questions:
- Create an issue in the module repository
- Include Odoo version, Python version, and steps to reproduce
- Attach relevant error logs from Odoo server console

Happy automating with AI! 🚀
