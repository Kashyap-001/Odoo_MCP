"""
mcp_gateway/mcp/gateway.py

Core agentic loop engine orchestrating LLM calls, tool execution, and session management.

Key classes:
  McpGateway — Main orchestrator for agent-user interactions

Dependencies:
  - mcp.agent, mcp.session, mcp.session.message models
  - mcp.providers — LLM provider adapters
  - mcp.tools.dispatcher — Tool execution router
  - mcp.access.rule — Access control
  - Imports json for context/memory serialization
  - Imports logging for audit trail

Developer notes:
  - Stateless: initialized with env and user for each run
  - Never stores API keys or sensitive data beyond method scope
  - All tool calls logged BEFORE and AFTER execution (audit compliance)
  - Supports optional context injection from active record
  - Memory injection from past sessions (if agent.enable_memory=True)
  - Full retry logic with exponential backoff on provider errors
"""

import logging
import json
from datetime import datetime, timedelta
import pytz
from odoo import _, fields
from odoo.exceptions import AccessError, UserError
from odoo.tools import html_sanitize
from .tools.dispatcher import ToolDispatcher

_logger = logging.getLogger(__name__)

# Fallback models per provider - used when tool result processing fails
FALLBACK_MODELS = {
    'opencode': 'minimax-m2.5-free',
    'openai': 'gpt-4o-mini',
    'anthropic': 'claude-haiku-4-5-20250501',
    'gemini': 'gemini-1.5-flash',
    'ollama': 'llama3.1',
    'grok': 'grok-3-mini',
}


def detect_message_format(provider_type, model_id):
    """
    Detect the correct message format for a provider+model.
    Returns one of: 'anthropic', 'openai', 'gemini'
    """
    model_id = (model_id or '').lower().strip()

    if provider_type == 'opencode':
        if model_id.startswith('claude-'):
            return 'anthropic'
        elif model_id.startswith('gemini-'):
            return 'gemini'
        else:
            # deepseek, qwen, glm, kimi, gpt, minimax model names (opencode models), etc.
            return 'openai'

    if provider_type == 'anthropic':
        return 'anthropic'

    if provider_type == 'gemini':
        return 'gemini'

    # openai, ollama, custom, and all others
    return 'openai'


def normalize_history_for_format(messages, target_format):
    """
    Convert conversation history to target_format.
    Handles cross-model sessions where history was created
    by a different model family.

    target_format: 'anthropic', 'openai', or 'gemini'
    """
    import json

    normalized = []
    for msg in messages:
        role = msg.get('role', '')
        content = msg.get('content')

        # --- CONVERT TO OPENAI FORMAT ---
        if target_format == 'openai':

            # Anthropic user message with tool_result blocks
            # → OpenAI tool message(s)
            if role == 'user' and isinstance(content, list):
                tool_results = [b for b in content
                    if isinstance(b, dict)
                    and b.get('type') == 'tool_result']
                text_blocks = [b for b in content
                    if isinstance(b, dict)
                    and b.get('type') == 'text']

                for tr in tool_results:
                    result_content = tr.get('content', '')
                    if isinstance(result_content, list):
                        result_content = ' '.join(
                            b.get('text', '')
                            for b in result_content
                            if isinstance(b, dict))
                    normalized.append({
                        'role': 'tool',
                        'tool_call_id': tr.get('tool_use_id', ''),
                        'content': str(result_content)
                    })
                if text_blocks:
                    text = ' '.join(b.get('text', '')
                                   for b in text_blocks)
                    if text.strip():
                        normalized.append({
                            'role': 'user',
                            'content': text
                        })
                if not tool_results and not text_blocks:
                    normalized.append(msg)
                continue

            # Anthropic assistant message with tool_use blocks
            # → OpenAI assistant message with tool_calls
            if role == 'assistant' and isinstance(content, list):
                tool_calls = []
                text_parts = []
                for block in content:
                    if (isinstance(block, dict)
                            and block.get('type') == 'tool_use'):
                        tool_calls.append({
                            'id': block.get('id', ''),
                            'type': 'function',
                            'function': {
                                'name': block.get('name', ''),
                                'arguments': json.dumps(
                                    block.get('input', {}))
                            }
                        })
                    elif (isinstance(block, dict)
                          and block.get('type') == 'text'):
                        text_parts.append(
                            block.get('text', ''))
                new_msg = {
                    'role': 'assistant',
                    'content': ' '.join(text_parts) or None
                }
                if tool_calls:
                    new_msg['tool_calls'] = tool_calls
                normalized.append(new_msg)
                continue

            # Already OpenAI format — keep as-is
            normalized.append(msg)

        # --- CONVERT TO ANTHROPIC FORMAT ---
        elif target_format == 'anthropic':

            # OpenAI tool message → Anthropic user tool_result
            if role == 'tool':
                tool_call_id = msg.get('tool_call_id', '')
                normalized.append({
                    'role': 'user',
                    'content': [{
                        'type': 'tool_result',
                        'tool_use_id': tool_call_id,
                        'content': str(content or '')
                    }]
                })
                continue

            # OpenAI assistant with tool_calls
            # → Anthropic assistant with tool_use content blocks
            if (role == 'assistant'
                    and msg.get('tool_calls')):
                content_blocks = []
                if content:
                    content_blocks.append({
                        'type': 'text',
                        'text': str(content)
                    })
                for tc in msg.get('tool_calls', []):
                    fn = tc.get('function', {})
                    try:
                        input_data = json.loads(
                            fn.get('arguments', '{}'))
                    except Exception:
                        input_data = {}
                    content_blocks.append({
                        'type': 'tool_use',
                        'id': tc.get('id', ''),
                        'name': fn.get('name', ''),
                        'input': input_data
                    })
                normalized.append({
                    'role': 'assistant',
                    'content': content_blocks
                })
                continue

            # Already Anthropic format or plain message
            normalized.append(msg)

        # --- GEMINI FORMAT ---
        elif target_format == 'gemini':
            # For now pass through — Gemini sessions are
            # typically fresh. Add conversion if needed.
            normalized.append(msg)

        else:
            normalized.append(msg)

    return normalized


_STDIO_MCP_READ_TIMEOUT = 15  # seconds — bounds how long a hung/slow external stdio MCP server can block a worker


def _readline_with_timeout(pipe, timeout=_STDIO_MCP_READ_TIMEOUT):
    """`pipe.readline()` blocks forever if the child never writes another line —
    select() first so a hung/crashed stdio MCP server can't hang the whole
    request (and orphan the subprocess, since `finally: proc.kill()` never
    runs if readline() itself never returns)."""
    import select
    ready, _, _ = select.select([pipe], [], [], timeout)
    if not ready:
        raise TimeoutError(f'No response from MCP server within {timeout}s')
    return pipe.readline()


def _sanitize_html_blocks(data):
    """Sanitize any {"_type": "html", "content": ...} block's content in place
    (including ones nested inside a {"_type": "mixed", "blocks": [...]} array).

    The model's structured reply is untrusted (a prompt-injected tool result or
    a manipulated response could ask for raw <img onerror=...> etc.) — every
    other _type is rendered via t-esc or a controlled markdown pass on the
    frontend, but 'html' is rendered with a raw t-out, so it must be sanitized
    before it's ever stored/displayed.
    """
    if not isinstance(data, dict):
        return data
    if data.get('_type') == 'html' and isinstance(data.get('content'), str):
        data['content'] = html_sanitize(data['content'])
    elif data.get('_type') == 'mixed' and isinstance(data.get('blocks'), list):
        for block in data['blocks']:
            _sanitize_html_blocks(block)
    return data


def format_tool_specs_for_api(tool_specs, target_format):
    """
    Convert tool specifications to the target API format.

    OPENAI format:
        {
          "type": "function",
          "function": {
            "name": "...",
            "description": "...",
            "parameters": { JSON Schema }
          }
        }

    ANTHROPIC format:
        {
          "name": "...",
          "description": "...",
          "input_schema": { JSON Schema }
        }

    GEMINI format:
        {
          "functionDeclarations": [
            {
              "name": "...",
              "description": "...",
              "parameters": { JSON Schema }
            }
          ]
        }

    Args:
        tool_specs: List of tool specs in Odoo internal format
        target_format: Target format ('anthropic', 'openai', 'gemini')

    Returns:
        List of formatted tool specs
    """
    formatted_tools = []

    for spec in tool_specs:
        name = spec.get('name', '')
        description = spec.get('description', '')
        input_schema = spec.get('input_schema', {})

        if target_format == 'anthropic':
            # Anthropic format: name, description, input_schema
            formatted_tools.append({
                'name': name,
                'description': description,
                'input_schema': input_schema,
            })
        elif target_format == 'gemini':
            # Gemini format: functionDeclarations array
            formatted_tools.append({
                'functionDeclarations': [{
                    'name': name,
                    'description': description,
                    'parameters': input_schema,
                }]
            })
        else:
            # OpenAI format: type, function with name, description, parameters
            formatted_tools.append({
                'type': 'function',
                'function': {
                    'name': name,
                    'description': description,
                    'parameters': input_schema,
                }
            })

    return formatted_tools


class McpGateway:
    """
    AI Agent Gateway — orchestrates agentic loop.

    Main entry point for agent-user interactions. Handles:
      1. Access control and rate limiting
      2. Session creation and management
      3. Provider calls with retry logic
      4. Tool dispatch and execution
      5. Token usage and cost tracking
      6. Session summarization for memory

    Usage:
        gateway = McpGateway(env, user)
        result = gateway.run(agent_id=1, user_message='Hi', session_id=None)
        # Returns: {'reply': '...', 'session_id': 1, 'tool_calls': 2, ...}
    """

    def __init__(self, env, user):
        """
        Initialize gateway for a user.

        Args:
            env: Odoo environment (for database access)
            user: res.users executing the session
        """
        self.env = env
        self.user = user
        self._logger = logging.getLogger(self.__class__.__module__)

    def run(self, agent_id: int, user_message: str, session_id: int = None,
            active_model: str = None, active_id: int = None,
            staged_attachment_id: int = None) -> dict:
        """
        Execute full agentic loop: message → provider → tools → reply.

        Main method. Orchestrates the entire flow from user message to final reply.

        Args:
            agent_id (int): ID of agent to use
            user_message (str): User's input message
            session_id (int): Optional existing session to continue
            active_model (str): Optional Odoo model to inject context from
            active_id (int): Optional record ID to inject context from

        Returns:
            dict: {
              'reply': str (final assistant message),
              'session_id': int (session record ID),
              'tool_calls': int (number of tool executions),
              'input_tokens': int,
              'output_tokens': int,
              'cost_usd': float (estimated cost),
            }

        Raises:
            AccessError: if user doesn't have access to agent
            UserError: if agent unconfigured or provider error

        Example:
            result = gateway.run(
                agent_id=1,
                user_message='Create a lead for John Doe',
                active_model='crm.lead',
                active_id=None,
            )
            print(result['reply'])
        """

        # ── 1. Load agent and validate ──────────────────────────────
        agent = self.env['mcp.agent'].browse(agent_id)
        if not agent.exists():
            raise UserError(_('Agent not found: %d') % agent_id)
        self._check_access(agent, self.user)

        # ── 2. Check rate limit ─────────────────────────────────────
        rules = self.env['mcp.access.rule'].get_rules_for_user(self.user)
        if rules['rate_limit_day'] > 0:
            self._check_rate_limit(self.user, rules['rate_limit_day'])
        if rules['rate_limit_month'] > 0:
            self._check_monthly_limit(self.user, rules['rate_limit_month'])

        # ── 3. Load or create session ───────────────────────────────
        if session_id:
            session = self.env['mcp.session'].browse(session_id)
            if not session.exists():
                raise UserError(_('Session not found: %d') % session_id)
        else:
            session = self.env['mcp.session'].create({
                'agent_id': agent_id,
                'user_id': self.user.id,
                'source': 'chat',
            })

        # ── 4. Build system prompt with context + memory ─────────────
        system_prompt = agent.system_prompt or ''
        system_prompt = self._inject_context(agent, system_prompt, active_model, active_id)
        if agent.enable_memory:
            system_prompt = self._inject_memory(agent, self.user, system_prompt)

        # ── 4a. Inject current user info so AI always knows who it's talking to ──
        user = self.env.user
        user_info = f"\n\nCURRENT USER: {user.name}"
        if user.email:
            user_info += f" <{user.email}>"
        if user.company_id:
            user_info += f" | Company: {user.company_id.name}"
        dept = getattr(user, 'department_id', None)
        if dept:
            user_info += f" | Department: {dept.name}"
        system_prompt += user_info

        # ── 4b. Inject current date/time into system prompt (fresh per API call) ──
        # datetime.now() called inline - no intermediate variables
        system_prompt += f"\n\nCURRENT DATE AND TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (server) / {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\nWhen user says 'today', 'tomorrow', etc, calculate from this date, never guess."
        _logger.info("DATE_INJECTION_CHECK: %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        # ── 4c. Add tool usage rules to prevent hallucination ───────────────
        system_prompt += """
CRITICAL TOOL USAGE RULES (DO NOT HALLUCINATE):
- You MUST call a tool for EVERY query or action request — no exceptions. Even if you performed a similar action earlier in this session.
- NEVER answer from memory, training data, or session history. The database changes constantly. Always run a fresh tool call.
- NEVER answer questions about records, counts, balances, names, or any Odoo data without querying first. Your training data is outdated.
- NEVER call the same tool twice with the same arguments in one turn. If you already have the data, use it and proceed.
- If you need field names for a model, call get_model_schema ONCE — do not guess, do not call it again on the same model.
- Never report success for an action unless you received a confirming tool result IN THIS TURN.
- Each user request is independent. A previous tool call never satisfies the current request.
- If the user asks to create, update, delete, find, list, show, count, or check anything — call the tool. No exceptions."""

        # ── 4c. Inject structured JSON response format ──────────────────────
        system_prompt += """

RESPONSE FORMAT — MANDATORY:
You MUST always reply with a single valid JSON object. Never reply with plain text.
Choose the _type that best fits your answer:

{"_type": "text", "content": "Your plain-text answer here."}

{"_type": "table", "title": "Products (38)", "subtitle": "Filtered by: active=true", "headers": ["Name", "Price", "Code"], "rows": [["Laptop", "$1,200", "LAP-01"]]}

{"_type": "fields", "title": "Sale Order #42", "subtitle": "Confirmed · Customer: Acme · $4,500", "data": {"Customer": "Acme", "Total": "$4,500", "State": "Confirmed"}}

{"_type": "html", "content": "<div class='alert alert-success'>Done.</div>"}

{"_type": "image", "url": "/web/image/product.template/1/image_1920", "alt": "Product image"}

{"_type": "attachment", "url": "/web/content/123", "filename": "invoice.pdf", "mimetype": "application/pdf"}

{"_type": "cards", "title": "Products (5)", "items": [{"title": "Laptop", "subtitle": "$1,200", "image_url": "/web/image/product.template/1/image_1920", "fields": {"Stock": 42, "Code": "LAP-01"}}]}

{"_type": "stats", "title": "Invoice Overview", "items": [{"label": "Total", "value": "47", "color": "primary"}, {"label": "Overdue", "value": "8", "color": "danger"}, {"label": "Amount Due", "value": "$125,400", "color": "success"}]}

{"_type": "list", "title": "Product Types", "items": ["Goods (consu)", "Service", "Combo"], "ordered": false}

{"_type": "mixed", "blocks": [{"_type": "text", "content": "Here is the summary:"}, {"_type": "table", "title": "Orders", "headers": ["Name", "Total"], "rows": [["SO001", "$500"]]}]}

Selection rules — use the FIRST type that fits, in this order:
1. cards: products, contacts, or any set where images matter — always prefer over table when records have images
2. table: 2+ records with the same fields — ALWAYS include row count in title e.g. "Invoices (12)"
3. fields: exactly ONE record's details — include key status in subtitle e.g. "Confirmed · $4,500"
4. stats: answer contains one or more numbers/counts/totals — use when asked "how many", "total", "count", "overview", "summary of numbers". Each item needs label + value + color (primary/success/danger/warning/muted).
5. list: 3–15 simple text items with no columns — use for "what are the", "list the", "what types", "what options", "what stages". Use ordered:true for steps/sequences.
6. mixed: summary sentence + data (text block + table or fields)
7. html: coloured status badges, alerts, progress indicators
8. image: when showing a single image
9. attachment: when sharing a file download
10. text: conversational reply, confirmation, or explanation with no data — content must be plain prose, NO markdown bold (**), NO markdown tables (|), NO bullet lists with *. If you have structured data, use the correct type.

Standards that apply to ALL types:
- table/cards: ALWAYS include count in title: "Products (38)", never just "Products"
- fields: ALWAYS include a subtitle with the 2-3 most important status fields
- NEVER include markdown, code fences, or any text outside the JSON object
- The entire response must be a single JSON object starting with { and ending with }

IMAGE FIELDS: When search_read returns image_1920, image_128, etc., the value is already a URL string. Use it as image_url in cards. Never include image fields in table or fields types."""

        # ── 5. Build tool specs — filtered by user's access rules ────────
        _is_admin = (
            self.user.id in (1, 2)
            or self.user.has_group('base.group_system')
            or self.user.has_group('mcp_gateway.group_mcp_admin')
            or self.user._is_admin()
        )
        if _is_admin:
            _effective_tools = agent.effective_tool_ids
        else:
            _rules = self.env['mcp.access.rule'].get_rules_for_user(self.user)
            if _rules.get('rules_matched') and not _rules.get('all_tools_allowed'):
                _allowed = _rules['tool_ids'].ids
                _effective_tools = agent.effective_tool_ids.filtered(
                    lambda t: t.id in _allowed
                )
            else:
                _effective_tools = agent.effective_tool_ids
        tool_specs = self._build_tool_specs(_effective_tools, agent)

        # ── 5a. Inject dynamic tool guidance into system prompt ────────────
        tool_guidance = self._build_tool_guidance(tool_specs)
        if tool_guidance:
            if system_prompt:
                system_prompt += '\n\n' + tool_guidance
            else:
                system_prompt = tool_guidance

        # ── 6. Build message history ──────────────────────────────────
        # If continuing an existing session, load prior messages
        if session_id and session.session_message_ids:
            messages = self._build_message_history(session, system_prompt)
            # Clean ghost/empty turns from previous turn before starting new turn
            messages = self.clean_conversation_history(messages)
        else:
            messages = []
            if system_prompt:
                messages.append({'role': 'system', 'content': system_prompt})

        # Inject synthetic date exchange as first user/assistant pair after system
        # This ensures model uses correct date (some providers ignore system prompt)
        synthetic_date_msg = {'role': 'user', 'content': "What is today's date?"}
        synthetic_response = {
            'role': 'assistant',
            'content': f"Today is {datetime.now().strftime('%A, %B %d, %Y')}. The current time is {datetime.now().strftime('%H:%M')}. I will use this date for all scheduling."
        }
        # Insert after system message, at beginning of conversation
        insert_idx = 1
        messages.insert(insert_idx, synthetic_response)
        messages.insert(insert_idx, synthetic_date_msg)

        # If user uploaded a file via the chat UI, prepend context note to the message
        if staged_attachment_id:
            try:
                _att_rows = self.env['ir.attachment'].search_read(
                    [('id', '=', staged_attachment_id)], ['name', 'mimetype', 'file_size']
                )
                if _att_rows:
                    _att = _att_rows[0]
                    _SPREADSHEET_TYPES = {
                        'application/vnd.ms-excel',
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        'application/vnd.oasis.opendocument.spreadsheet',
                    }
                    if _att['mimetype'] in _SPREADSHEET_TYPES:
                        _read_hint = (
                            f' To read the spreadsheet rows use execute_orm: '
                            f'`return read_excel({staged_attachment_id})` — returns list of row lists, handles .xls/.xlsx/.ods automatically.'
                        )
                    else:
                        _read_hint = ''
                    _att_note = (
                        f'[User uploaded file: "{_att["name"]}" (mimetype: {_att["mimetype"]}, '
                        f'attachment_id: {staged_attachment_id}).{_read_hint} '
                        f'To link it to a record after processing use execute_orm: '
                        f'env["ir.attachment"].browse({staged_attachment_id}).write({{"res_model": "model.name", "res_id": record_id}})]'
                    )
                    user_message = _att_note + '\n\n' + user_message
            except Exception:
                pass  # don't break chat if attachment lookup fails

        # Always append the new user message
        messages.append({'role': 'user', 'content': user_message})

        # Log user message
        self.env['mcp.session.message'].create({
            'session_id': session.id,
            'role': 'user',
            'content': user_message,
        })

        # Auto-title new session from first user message
        if not session_id:
            _title = user_message.strip()
            if len(_title) > 60:
                _title = _title[:57].rsplit(' ', 1)[0].strip() + '…'
            session.name = _title

        # ── 7. Detect message format for this provider/model ─────────────────────
        message_format = detect_message_format(agent.provider, agent.model_name)
        _logger.debug('Using message format: %s for provider=%s model=%s',
                     message_format, agent.provider, agent.model_name)

        # NOTE: Tool spec format conversion is handled by each provider in build_payload()
        # Do NOT convert tool specs here - each provider handles its own format

        # ── 7. Agentic loop (multi-turn: provider → tools → provider → ... → reply) ──
        try:
            provider = agent._get_provider_instance()
            response, tool_calls, total_tool_calls, total_input, total_output, last_tool_name = self._call_provider_with_tools(
                provider, agent, messages, tool_specs, session, message_format
            )
        except Exception as e:
            session.write({'state': 'error', 'error_message': str(e)})
            raise

        assistant_text = response.get('text', '') if response else ''
        if assistant_text:
            cleaned = assistant_text.strip()
            if cleaned.startswith('```'):
                lines = cleaned.split('\n')
                if lines[0].startswith('```'):
                    lines = lines[1:]
                if lines and lines[-1].strip() == '```':
                    lines = lines[:-1]
                cleaned = '\n'.join(lines).strip()
            assistant_text = cleaned

            brace_idx = assistant_text.find('{"_type":')
            if brace_idx >= 0:
                assistant_text = assistant_text[brace_idx:]
                rbrace_idx = assistant_text.rfind('}')
                if rbrace_idx > 0:
                    assistant_text = assistant_text[:rbrace_idx + 1]
        input_tokens = total_input
        output_tokens = total_output

        # Handle empty reply when content is None after tool loop
        if not assistant_text or assistant_text.strip() == '':
            if total_tool_calls > 0:
                # Tools were called but no final text — provide helpful message
                _logger.warning(
                    'Gateway returned empty reply after %d tool calls. Last tool: %s. Response: %s',
                    total_tool_calls, last_tool_name, response
                )
                if last_tool_name:
                    assistant_text = (
                        f"I processed your request but encountered an issue generating the final response. "
                        f"The last action attempted was: {last_tool_name}. "
                        f"Please check if the action completed in Odoo, or try rephrasing your request."
                    )
                else:
                    assistant_text = (
                        "I processed your request but encountered an issue generating the final response. "
                        "Please check if the action completed in Odoo, or try rephrasing your request."
                    )
            else:
                _logger.warning('Gateway returned empty reply with no tool calls. Response: %s', response)
                assistant_text = "I was unable to generate a response. Please try again or rephrase your message."

        # Final guard: if AI returned plain text (ignored format instruction), wrap as {"_type": "text"}
        if assistant_text and not assistant_text.strip().startswith('{'):
            assistant_text = json.dumps({"_type": "text", "content": assistant_text})

        # Sanitize any raw-HTML block before it's ever stored/displayed — the
        # model's own reply is untrusted input (see _sanitize_html_blocks).
        if assistant_text:
            try:
                parsed = json.loads(assistant_text)
                assistant_text = json.dumps(_sanitize_html_blocks(parsed))
            except (json.JSONDecodeError, TypeError):
                pass

        # Log assistant reply
        self.env['mcp.session.message'].create({
            'session_id': session.id,
            'role': 'assistant',
            'content': assistant_text or '',
            'token_count': output_tokens,
        })

        # ── 9. Save session and update token counts ─────────────────
        session.write({
            'state': 'done',
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
        })

        # ── 10. Cost tracking ───────────────────────────────────────
        try:
            self.env['mcp.cost.entry'].create({
                'session_id': session.id,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'cost_per_1k_input': agent.cost_per_1k_input,
                'cost_per_1k_output': agent.cost_per_1k_output,
            })
        except Exception as e:
            _logger.warning('Cost tracking failed: %s', str(e))

        # ── 11. Memory summarization (if enabled) ────────────────────
        if agent.enable_memory and len(self.env['mcp.session.message'].search(
            [('session_id', '=', session.id)]
        )) > 0:
            try:
                summary = self._summarize_session(agent, session)
                self.env['mcp.agent.memory'].create({
                    'agent_id': agent.id,
                    'user_id': self.user.id,
                    'session_id': session.id,
                    'summary': summary,
                })
            except Exception as e:
                self._logger.warning('Session memory summarization failed: %s', str(e))

        # Calculate cost
        input_cost = (input_tokens / 1000) * agent.cost_per_1k_input
        output_cost = (output_tokens / 1000) * agent.cost_per_1k_output
        total_cost = input_cost + output_cost

        return {
            'reply': assistant_text or '',
            'session_id': session.id,
            'tool_calls': total_tool_calls,
            'input_tokens': total_input,
            'output_tokens': total_output,
            'cost_usd': total_cost,
        }

    def _check_access(self, agent, user):
        """
        Verify user has access to agent via access rules.

        Raises:
            AccessError: if user not in allowed groups or not explicitly granted
        """
        if user.id == 1 or user.id == 2 or user.has_group('base.group_system') or user.has_group('mcp_gateway.group_mcp_admin') or user._is_admin():
            return  # Admins can access all

        rules = self.env['mcp.access.rule'].get_rules_for_user(user)
        if not rules.get('rules_matched', False):
            raise AccessError(_('You do not have access to agent: %s') % agent.name)

        if not rules.get('all_agents_allowed', False) and agent.id not in rules['agent_ids'].ids:
            raise AccessError(_('You do not have access to agent: %s') % agent.name)

    def _check_rate_limit(self, user, limit: int):
        """
        Check if user has exceeded daily rate limit.

        Raises:
            UserError: if rate limit exceeded
        """
        cutoff_time = fields.Datetime.to_string(
            fields.Datetime.from_string(fields.Datetime.now()) - timedelta(hours=24)
        )
        recent_sessions = self.env['mcp.session'].search([
            ('user_id', '=', user.id),
            ('create_date', '>=', cutoff_time),
            ('state', '!=', 'error'),
        ])

        if len(recent_sessions) >= limit:
            raise UserError(
                _('Rate limit of %d calls/day exceeded. Reset in 24 hours.') % limit
            )

    def _check_monthly_limit(self, user, limit: int):
        """
        Check if user has exceeded monthly rate limit.

        Raises:
            UserError: if monthly rate limit exceeded
        """
        cutoff_time = fields.Datetime.to_string(
            fields.Datetime.from_string(fields.Datetime.now()) - timedelta(days=30)
        )
        recent_sessions = self.env['mcp.session'].search([
            ('user_id', '=', user.id),
            ('create_date', '>=', cutoff_time),
            ('state', '!=', 'error'),
        ])

        if len(recent_sessions) >= limit:
            raise UserError(
                _('Monthly rate limit of %d calls exceeded. Reset in 30 days.') % limit
            )

    def _inject_context(self, agent, system_prompt: str, model: str = None,
                        record_id: int = None) -> str:
        """
        Inject active record context into system prompt.

        Fetches context_fields from agent and appends record data.

        Args:
            agent: mcp.agent with context_fields JSON
            system_prompt: Base system prompt
            model: Odoo model name (e.g., 'crm.lead')
            record_id: Record ID to fetch context from

        Returns:
            str: System prompt with injected context
        """
        if not model or not record_id or not agent.context_fields:
            return system_prompt

        try:
            fields = json.loads(agent.context_fields or '[]')
            if not fields:
                return system_prompt

            record = self.env[model].browse(record_id)
            if not record.exists():
                return system_prompt

            context_data = record.read(fields)[0] if record.read(fields) else {}
            context_block = f'\n\nACTIVE RECORD CONTEXT ({model}#{record_id}):\n'
            for field, value in context_data.items():
                context_block += f'  {field}: {value}\n'

            return system_prompt + context_block
        except Exception as e:
            self._logger.warning('Context injection failed: %s', str(e))
            return system_prompt

    def _inject_memory(self, agent, user, system_prompt: str) -> str:
        """
        Inject past session summaries into system prompt.

        Fetches last 5 memories and appends to system prompt.

        Args:
            agent: mcp.agent
            user: res.users
            system_prompt: Base system prompt

        Returns:
            str: System prompt with injected memory
        """
        try:
            memories = self.env['mcp.agent.memory'].search([
                ('agent_id', '=', agent.id),
                ('user_id', '=', user.id),
            ], order='create_date DESC', limit=5)

            if not memories:
                return system_prompt

            memory_block = '\n\nPAST INTERACTION SUMMARIES:\n'
            for i, mem in enumerate(memories, 1):
                memory_block += f'{i}. {mem.summary}\n'

            return system_prompt + memory_block
        except Exception as e:
            self._logger.warning('Memory injection failed: %s', str(e))
            return system_prompt

    def clean_conversation_history(self, messages: list) -> list:
        """
        Clean conversation history by removing ghost/empty turns.

        Removes:
        - Assistant messages with no content AND no tool_calls (ghost turns)
        - Tool result messages referencing stale tool_call_ids

        Args:
            messages: List of message dicts

        Returns:
            Cleaned message list
        """
        cleaned = []
        # Collect all valid tool_call_ids from tool_calls in the history
        valid_tool_call_ids = set()
        for msg in messages:
            if msg.get('tool_calls'):
                for tc in msg.get('tool_calls', []):
                    tc_id = tc.get('id')
                    if tc_id:
                        valid_tool_call_ids.add(tc_id)

        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content')
            tool_calls = msg.get('tool_calls')
            tool_call_id = msg.get('tool_call_id')

            # Remove assistant messages with no content AND no tool_calls
            if role == 'assistant' and not content and not tool_calls:
                _logger.debug('Removing ghost assistant message')
                continue

            # Remove tool result messages with stale tool_call_id
            if role in ('tool', 'tool_result') and tool_call_id and tool_call_id not in valid_tool_call_ids:
                _logger.debug('Removing stale tool result: %s', tool_call_id)
                continue

            cleaned.append(msg)

        return cleaned

    def _summarize_session(self, agent, session) -> str:
        """
        Generate 3-sentence LLM summary of session.

        Calls agent to summarize its own conversation.

        Args:
            agent: mcp.agent
            session: mcp.session to summarize

        Returns:
            str: Summary text
        """
        try:
            messages = self.env['mcp.session.message'].search([
                ('session_id', '=', session.id),
            ], order='create_date ASC')

            def _plain(content):
                try:
                    p = json.loads(content or '')
                    if isinstance(p, dict):
                        return p.get('content') or p.get('text') or ''
                except Exception:
                    pass
                return content or ''

            conversation = '\n'.join([
                f'{m.role}: {_plain(m.content)}' for m in messages
                if m.role in ('user', 'assistant') and _plain(m.content)
            ])

            summarize_prompt = [
                {'role': 'user', 'content': f'Summarize this conversation in 3 sentences:\n\n{conversation}'},
            ]

            provider = agent._get_provider_instance()
            result = provider.call(agent, summarize_prompt, [])
            return result.get('text', 'Session completed.')[:500]
        except Exception as e:
            self._logger.warning('Session summarization failed: %s', str(e))
            return 'Session completed.'

    def _build_tool_specs(self, tools, agent=None) -> list:
        """
        Build tool specifications from Odoo tools and external MCP servers.

        Args:
            tools: Recordset of mcp.tool (local Odoo tools)
            agent: Optional agent record (used to get external server config)

        Returns:
            list: Tool specs with name, description, input_schema
        """
        specs = [tool.get_tool_spec() for tool in tools if tool.active]

        # Also load tools from external MCP servers
        external_tools = self._get_external_mcp_tools()
        for ext_tool in external_tools:
            specs.append({
                'name': ext_tool.get('name', ''),
                'description': ext_tool.get('description', ''),
                'input_schema': ext_tool.get('input_schema', {}),
            })

        return specs

    def _build_tool_guidance(self, tool_specs: list) -> str:
        """
        Build dynamic tool guidance section for system prompt.

        Generates a guidance section based on the actual loaded tools,
        including categories, selection rules, and external tool handling.

        Args:
            tool_specs: List of tool specifications (from _build_tool_specs)

        Returns:
            str: Tool guidance section for system prompt
        """
        if not tool_specs:
            return ''

        # Extract categories from tool descriptions
        categories = set()
        external_tools = []
        local_tools_count = 0

        for tool in tool_specs:
            name = tool.get('name', '')
            desc = tool.get('description', '')

            if name.startswith('ext_'):
                # External tool - extract server name
                external_tools.append(name)
            else:
                local_tools_count += 1
                # Extract category from description if present (e.g., [SALES])
                if '[' in desc:
                    start = desc.find('[')
                    end = desc.find(']')
                    if start >= 0 and end > start:
                        cat = desc[start+1:end]
                        if '/' in cat:
                            cat = cat.split('/')[0].strip()
                        categories.add(cat)

        # Build guidance text
        guidance = "You have access to tools in these categories:\n"
        if categories:
            guidance += ', '.join(sorted(categories))
        else:
            guidance += 'Odoo Operations'

        if external_tools:
            guidance += f"\nExternal tools: {', '.join([t.split('_')[1] if '_' in t else t for t in external_tools[:3]])}"

        guidance += """

CRITICAL - Always Call Tools for Data:
- For ANY data lookup (invoices, customers, orders, products, etc.), you MUST call the appropriate tool
- NEVER answer from memory or assumptions - always verify with a tool call
- If user asks about invoices, accounts, partners, or any data, call the tool FIRST
- Even if you think you know the answer, verify with a tool call to ensure accuracy
- Stale/inaccurate answers from memory are worse than no answer

Tool selection rules:
- Always use a tool when the user's request maps to a tool's purpose
- Read tool descriptions carefully - they include exact trigger phrases like "find customer", "create order", "check stock"
- If unsure between two tools, pick the more specific one
- For [EXTERNAL] tools, use only when the task clearly requires data or actions outside Odoo
- After every tool call, summarise the result in plain language for the user
- Never fabricate data - if a tool returns empty, say so clearly

Quick reference - available local tools:
"""
        # Add quick tool reference
        for tool in tool_specs[:10]:  # First 10 tools
            name = tool.get('name', '')
            desc = tool.get('description', '')
            # Extract first sentence of description
            if desc:
                first_sentence = desc.split('.')[0][:80]
                guidance += f"- {name}: {first_sentence}...\n"

        if len(tool_specs) > 10:
            guidance += f"- ... and {len(tool_specs) - 10} more tools\n"

        if any(t.get('name') == 'execute_orm' for t in tool_specs):
            guidance += """
SALE ORDER METHODS — common name confusion:
- action_quotation_sent(ids)  → sets state='sent' ONLY, does NOT send any email
- action_quotations_send()    → opens UI wizard, NOT usable programmatically
- To actually send quotation emails programmatically:
    template = env.ref('sale.email_template_edi_sale')
    for order in env['sale.order'].browse(ids):
        template.send_mail(order.id, force_send=True)

SALE ORDER CONFIRM WORKFLOW:
- Create order lines BEFORE calling action_confirm (lines added after may not link correctly)
- action_confirm() → state: draft → sale (confirmed), NOT draft → sent

INVOICE WORKFLOW (programmatic, no wizard needed):
  1. Create:  inv_id = env['account.move'].create({'move_type':'out_invoice','partner_id':X,'invoice_line_ids':[(0,0,{'product_id':Y,'quantity':1,'price_unit':Z})]}).id
  2. Post:    env['account.move'].browse(inv_id).action_post()
  3. Pay:     env['account.payment.register'].with_context(active_model='account.move',active_ids=[inv_id]).create({}).action_create_payments()
- NOTE: account.invoice no longer exists — it is account.move with move_type='out_invoice'

PURCHASE ORDER WORKFLOW:
- Create: env['purchase.order'].create({'partner_id':X,'order_line':[(0,0,{'product_id':Y,'product_qty':1,'price_unit':Z})]}).id
- Confirm: env['purchase.order'].browse(po_id).button_confirm()  → state: draft → purchase
- Receive goods: find stock.picking linked to PO via po.picking_ids, then call picking.button_validate()

HR EMPLOYEE WORKFLOW:
- Create: env['hr.employee'].create({'name':X,'job_id':Y,'department_id':Z})
- Archive: record.write({'active': False})  or  record.toggle_active()
- Leave request: env['hr.leave'].create({'holiday_status_id':X,'employee_id':Y,'date_from':Z,'date_to':W,'holiday_type':'employee'})
- Leave approve: env['hr.leave'].browse(id).action_approve()

REPORT/PDF GENERATION — return URL, never render_qweb_pdf:
- DO NOT use env.ref().render_qweb_pdf() or _render_qweb_pdf() — generates huge binary
- DO NOT guess report external IDs — they vary across Odoo versions
- Return the download URL as a string: f'/report/pdf/{report_name}/{record_id}'
- Common Odoo 18 report names:
    Invoice/Bill:    account.report_invoice        (account.move)
    Sale Order:      sale.report_saleorder         (sale.order)
    Purchase Order:  purchase.report_purchaseorder (purchase.order)
    Delivery Slip:   stock.report_deliveryslip     (stock.picking)
- Example: return '/report/pdf/account.report_invoice/2'
"""

        if any(t.get('name') == 'create_echart' for t in tool_specs):
            guidance += """
CHART CREATION — create_echart (single call, no separate read_group):
Use ECharts DATASET format: source = list-of-lists where first row is headers.
Bar/Line:
  result = env['sale.order'].read_group(domain=[['date_order','>=','2026-06-01'],['state','not in',['cancel']]], fields=['amount_total:sum'], groupby=['date_order:day'])
  source = [['Date','Sales']] + [[str(r.get('date_order:day','')), r.get('amount_total',0)] for r in result]
  return {'title':{'text':'Sales'},'dataset':{'source':source},'xAxis':{'type':'category'},'yAxis':{'type':'value'},'series':[{'type':'bar'}]}
Pie:
  result = env['sale.order'].read_group(domain=[], fields=['amount_total:sum'], groupby=['partner_id'])
  source = [['Customer','Amount']] + [[r['partner_id'][1], r.get('amount_total',0)] for r in result if r.get('partner_id')]
  return {'dataset':{'source':source},'series':[{'type':'pie','radius':'60%','encode':{'itemName':'Customer','value':'Amount'}}]}
NEVER use xAxis.data or series.data — always use dataset.source.
"""

        return guidance

    def _get_external_mcp_tools(self) -> list:
        """
        Get tools from all active external MCP servers.

        Loads server configs from mcp.external.server model and
        fetches tools from each server via HTTP MCP protocol.

        Returns:
            list: List of tool specs from external servers
        """
        tools = []
        try:
            servers = self.env['mcp.external.server'].search([('active', '=', True)])
            for server in servers:
                if server.server_type == 'http' and server.url:
                    try:
                        import httpx
                        headers = {'Content-Type': 'application/json'}
                        auth_credential = server.get_decrypted_auth_value()
                        if server.auth_type == 'bearer' and auth_credential:
                            headers['Authorization'] = f'Bearer {auth_credential}'
                        elif server.auth_type == 'api_key' and auth_credential:
                            headers['X-API-Key'] = auth_credential

                        payload = {
                            'jsonrpc': '2.0',
                            'id': 1,
                            'method': 'tools/list',
                            'params': {},
                        }

                        with httpx.Client(timeout=10) as client:
                            resp = client.post(server.url, json=payload, headers=headers)
                            if resp.status_code == 200:
                                data = resp.json()
                                if 'result' in data and 'tools' in data['result']:
                                    for t in data['result']['tools']:
                                        tools.append({
                                            'name': f"ext_{server.name}_{t['name']}",
                                            'description': f"[{server.name}] {t.get('description', '')}",
                                            'input_schema': t.get('inputSchema', {}),
                                            'server_name': server.name,
                                            'original_name': t['name'],
                                        })
                    except Exception as e:
                        self._logger.warning('Failed to fetch tools from %s: %s', server.name, str(e))
                elif server.server_type == 'stdio' and server.command:
                    try:
                        for t in self._fetch_stdio_mcp_tools(server):
                            tools.append({
                                'name': f"ext_{server.name}_{t['name']}",
                                'description': f"[{server.name}] {t.get('description', '')}",
                                'input_schema': t.get('inputSchema', {}),
                                'server_name': server.name,
                                'original_name': t['name'],
                            })
                    except Exception as e:
                        self._logger.warning('Failed to fetch stdio tools from %s: %s', server.name, str(e))
        except Exception as e:
            self._logger.warning('Failed to load external MCP servers: %s', str(e))

        return tools

    def _fetch_stdio_mcp_tools(self, server) -> list:
        """Launch a stdio MCP server, fetch its tool list, then kill the process."""
        import subprocess
        import shlex
        import os

        command = server.command.strip()
        args_raw = server.args or ''
        try:
            extra_args = json.loads(args_raw) if args_raw.strip().startswith('[') else shlex.split(args_raw)
        except Exception:
            extra_args = []

        env_extra = {}
        if server.env_vars:
            try:
                env_extra = json.loads(server.env_vars)
            except Exception:
                pass

        proc = subprocess.Popen(
            [command] + extra_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **env_extra},
        )
        try:
            def _send(msg):
                proc.stdin.write((json.dumps(msg) + '\n').encode())
                proc.stdin.flush()

            def _recv():
                line = _readline_with_timeout(proc.stdout)
                return json.loads(line) if line else {}

            _send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "odoo-mcp-gateway", "version": "1.0"},
            }})
            _recv()  # consume initialize response
            _send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            _send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            data = _recv()
            return data.get('result', {}).get('tools', [])
        finally:
            proc.kill()
            proc.wait()

    def _call_provider_with_tools(self, provider, agent, messages, tool_specs, session=None, message_format='openai'):
        """
        Call provider repeatedly until a final text response is produced.

        Each round: provider responds → if tool calls exist, execute them,
        format results in provider-specific structure, append to messages,
        and call again. Loop until stop_reason is 'end_turn' or 'stop'.

        Args:
            message_format: Format for messages ('anthropic', 'openai', 'gemini')

        Returns:
            (response dict, tool_calls list, total_input_tokens, total_output_tokens, last_tool_name)
        """
        max_turns = 20  # safety limit
        total_tool_calls = 0
        total_input = 0
        total_output = 0
        last_tool_name = None  # Track the last tool called for error handling
        tool_call_counts = {}  # {(tool_name, args_json): int} — track same-call repetitions

        # Determine if we should strip system message (for providers that handle system internally)
        # NOTE: Don't strip system messages - the providers extract system from messages parameter
        # and handle it themselves. Stripping would remove the datetime-injected system message.
        provider_name = getattr(provider, '__class__', None).__name__ or ''
        # Anthropic handles system separately via 'system' parameter in payload - don't strip
        # All other providers extract from messages, so don't strip either
        messages_to_send = messages

        for turn in range(max_turns):
            # Note: Datetime is already injected in system_prompt in run() method
            # Log first 200 chars of system prompt for debugging (if present)
            for msg in messages_to_send:
                if msg.get('role') == 'system':
                    _logger.debug('System prompt (first 200): %s', msg.get('content', '')[:200])
                    break

            # Retry logic for provider calls (handles transient errors from some providers)
            max_retries = 2
            response = None
            # Clean history before each API call to remove ghost/empty turns
            messages_to_send = self.clean_conversation_history(messages_to_send)
            # Normalize history to target format (handles cross-model sessions)
            messages_to_send = normalize_history_for_format(messages_to_send, message_format)
            for attempt in range(max_retries + 1):
                try:
                    response = provider.call(agent, messages_to_send, tool_specs)

                    # Check if response contains an error from provider
                    # Some providers return error info in the response text
                    if response.get('text', '').startswith('[Provider error:'):
                        self._logger.warning('Provider returned error on attempt %d: %s', attempt + 1, response.get('text'))
                        if attempt < max_retries:
                            # Remove the last message (tool result) and retry
                            if messages_to_send and messages_to_send[-1].get('role') == 'tool':
                                messages_to_send = messages_to_send[:-1]
                            import time
                            time.sleep(1 * (attempt + 1))  # 1s, 2s backoff
                            continue
                    break
                except Exception as e:
                    if attempt < max_retries:
                        self._logger.warning('Provider call failed on attempt %d: %s', attempt + 1, str(e))
                        import time
                        time.sleep(1 * (attempt + 1))
                    else:
                        raise

            if not response:
                continue

            assistant_msg = {
                'role': 'assistant',
                'content': response.get('text') or response.get('reply') or '',
            }

            # Each provider owns its own history shape — no format-based branching needed.
            if response.get('tool_calls'):
                formatted = provider.format_tool_calls(response.get('tool_calls', []))
                if formatted:
                    assistant_msg['tool_calls'] = formatted

            messages_to_send.append(assistant_msg)

            tool_calls = response.get('tool_calls', [])
            response_text = response.get('text') or response.get('reply') or ''
            total_input += response.get('input_tokens', 0)
            total_output += response.get('output_tokens', 0)

            # Detect hallucination: model returned stop with no tool call for action request,
            # and no tool has been called at all yet this session turn.
            if not tool_calls and total_tool_calls == 0 and response.get('finish_reason') in ('stop', 'end_turn'):
                # Find the user message in history to check for action words
                user_msg_for_check = None
                for msg in messages:
                    if msg.get('role') == 'user' and msg.get('content'):
                        user_msg_for_check = msg.get('content', '')
                        break

                if user_msg_for_check:
                    user_msg_lower = user_msg_for_check.lower()
                    action_words = [
                        'create', 'make', 'add', 'update', 'delete', 'schedule',
                        'find', 'search', 'book', 'cancel', 'list', 'show', 'get',
                        'how many', 'count', 'fetch', 'retrieve', 'give me', 'what are',
                        'tell me', 'display', 'view', 'open', 'check', 'look up',
                        'report', 'total', 'sum', 'average', 'which', 'who', 'when', 'where',
                    ]
                    has_action_word = any(w in user_msg_lower for w in action_words)

                    if has_action_word:
                        _logger.warning(
                            "HALLUCINATION DETECTED: action request '%s' returned no tool call — retrying with correction.",
                            user_msg_for_check[:80]
                        )
                        correction = {
                            'role': 'user',
                            'content': (
                                "You answered from memory without calling any tool. "
                                "That is not allowed — you MUST call the appropriate tool now. "
                                "Do not answer from training data or session history. "
                                "Query the Odoo database using a tool to get current, accurate information."
                            )
                        }
                        messages_to_send.append(correction)
                        try:
                            retry_resp = provider.call(agent, messages_to_send, tool_specs)
                            if retry_resp and retry_resp.get('tool_calls'):
                                _logger.info("Hallucination correction retry succeeded — tool calls returned.")
                                response = retry_resp
                                tool_calls = retry_resp.get('tool_calls', [])
                                response_text = retry_resp.get('text') or retry_resp.get('reply') or ''
                                total_input += retry_resp.get('input_tokens', 0)
                                total_output += retry_resp.get('output_tokens', 0)
                                # Rebuild messages_to_send with correct assistant msg
                                messages_to_send.pop()  # remove correction
                                messages_to_send.pop()  # remove stale no-tool assistant msg
                                assistant_msg = {'role': 'assistant', 'content': response_text}
                                if retry_resp.get('tool_calls'):
                                    formatted = provider.format_tool_calls(retry_resp.get('tool_calls', []))
                                    if formatted:
                                        assistant_msg['tool_calls'] = formatted
                                messages_to_send.append(assistant_msg)
                            else:
                                _logger.warning("Hallucination correction retry also returned no tool calls.")
                                messages_to_send.pop()  # remove correction, proceed with original
                        except Exception as retry_err:
                            _logger.warning("Hallucination correction retry failed: %s", str(retry_err))
                            messages_to_send.pop()  # remove correction on failure

            # Detect if we're stuck in a loop (tool calls without final text after tool execution)
            # Only fire after many tool calls — normal multi-step queries make 2-5 tool calls legitimately
            if tool_calls and not response_text and total_tool_calls >= 8:
                # This is a loop - provider returned tool calls instead of final answer
                self._logger.warning('Provider stuck in tool call loop at turn %d, trying fallbacks', turn)

                # Get original user message for fallback injection
                original_user_msg = None
                for msg in messages:
                    if msg.get('role') == 'user' and msg.get('content'):
                        original_user_msg = msg.get('content')
                        break

                # Try 1: Fallback model (only on first few detections to avoid repeated cost)
                if turn <= 3:  # Try fallback model on first few stuck detections
                    fallback_response = self._retry_with_fallback_model(
                        provider, agent, messages_to_send, tool_specs, agent.model_name
                    )
                    if fallback_response and (fallback_response.get('text') or fallback_response.get('reply')):
                        self._logger.info('Fallback model succeeded')
                        # Process fallback response normally
                        tool_calls = fallback_response.get('tool_calls', [])
                        response_text = fallback_response.get('text') or fallback_response.get('reply') or ''
                        total_input += fallback_response.get('input_tokens', 0)
                        total_output += fallback_response.get('output_tokens', 0)
                        if not tool_calls and response_text:
                            return fallback_response, [], total_tool_calls, total_input, total_output, last_tool_name

                # Try 2: Compatibility mode with plain text injection
                # Use the last tool result that was added
                last_tool_result = None
                for msg in reversed(messages_to_send):
                    if msg.get('role') == 'user' and isinstance(msg.get('content'), list):
                        for item in msg.get('content', []):
                            if isinstance(item, dict) and item.get('type') == 'tool_result':
                                last_tool_result = item.get('content')
                                break

                if last_tool_result and original_user_msg:
                    # Get the tool name from the last tool call
                    last_tool_name = tool_calls[0].get('name', 'unknown') if tool_calls else 'unknown'

                    # Inject fallback message
                    self._inject_fallback_message(
                        messages_to_send, last_tool_name, last_tool_result, original_user_msg
                    )

                    # Make one more call with injected message
                    try:
                        final_response = provider.call(agent, messages_to_send, tool_specs)
                        final_text = final_response.get('text') or final_response.get('reply') or ''
                        if final_text:
                            self._logger.info('Compatibility mode succeeded')
                            return final_response, [], total_tool_calls + 1, total_input + final_response.get('input_tokens', 0), total_output + final_response.get('output_tokens', 0), last_tool_name
                    except Exception as e:
                        self._logger.warning('Compatibility mode failed: %s', str(e))

                # If all fallbacks failed, continue to next turn or break

            if not tool_calls:
                # No more tool calls — this is the final response
                return response, [], total_tool_calls, total_input, total_output, last_tool_name

            total_tool_calls += len(tool_calls)

            # Track the last tool name for error handling
            if tool_calls:
                last_tool_name = tool_calls[-1].get('name', 'unknown')

            # Execute each tool call and collect results
            _TERMINAL_TOOLS = frozenset(['create_echart', 'create_record', 'update_record', 'delete_record'])
            _terminal_results = []  # collect (tool_name, parsed_result) for all terminal successes

            for i, tool_call in enumerate(tool_calls):
                tool_name = tool_call['name']
                arguments = tool_call['arguments']
                tool_call_id = tool_call.get('id') or f'tc_{turn}_{i}'
                # Normalize stuck-detection key for search-type tools so varying args still count
                _args_dict = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
                if tool_name == 'get_model_schema':
                    _args_key = (tool_name, json.dumps({'model': _args_dict.get('model', '')}, sort_keys=True))
                else:
                    _args_key = (tool_name, json.dumps(_args_dict, sort_keys=True, default=str))
                tool_call_counts[_args_key] = tool_call_counts.get(_args_key, 0) + 1

                # Log tool call BEFORE execution (audit trail)
                self.env['mcp.session.message'].create({
                    'session_id': session.id if session else None,
                    'role': 'tool_call',
                    'content': json.dumps(arguments),
                    'tool_name': tool_name,
                    'tool_call_id': tool_call_id,
                })

                # Find tool in database
                tool = None
                is_external = False
                original_name = tool_name
                server_name = None

                if tool_name.startswith('ext_'):
                    # External MCP tool — parse name and route to external server
                    is_external = True
                    parts = tool_name.split('_', 2)
                    if len(parts) >= 3:
                        server_name = parts[1]
                        original_name = parts[2]
                else:
                    tool = self.env['mcp.tool'].search([('name', '=', tool_name)], limit=1)

                if is_external:
                    # Route to external MCP server
                    result = self._call_external_mcp_tool(server_name, original_name, arguments)
                else:
                    # Check access rules BEFORE tool search (F014)
                    is_admin = self.user.id == 1 or self.user.id == 2 or self.user.has_group('base.group_system') or self.user.has_group('mcp_gateway.group_mcp_admin') or self.user._is_admin()
                    rules = self.env['mcp.access.rule'].get_rules_for_user(self.user)
                    has_general_access = is_admin or (rules.get('rules_matched', False) and rules.get('all_tools_allowed', False))

                    if not tool:
                        # Tool doesn't exist - check if user has general tool access
                        if not has_general_access:
                            result = json.dumps({'success': False, 'error': f'Tool not found: {tool_name}'})
                            self._logger.warning('Tool not found: %s', tool_name)
                        else:
                            result = json.dumps({'success': False, 'error': f'Tool not found: {tool_name}'})
                            self._logger.warning('Tool not found: %s', tool_name)
                    elif not is_admin and not has_general_access and tool.id not in rules['tool_ids'].ids:
                        result = json.dumps({'success': False, 'error': f'Access denied to tool: {tool_name}'})
                        self._logger.warning('User %s not allowed to use tool %s', self.user.name, tool_name)
                    else:
                        # Execute tool
                        try:
                            dispatcher = ToolDispatcher()
                            result = dispatcher.dispatch(tool, arguments, self.env, self.user)
                        except Exception as e:
                            self._logger.error('Tool execution failed for %s: %s', tool_name, str(e))
                            result = json.dumps({'success': False, 'error': str(e)})

                # Check if tool returned an error - if so, stop the loop and return error to user
                # Don't let the model retry a failed tool call (causes infinite loop)
                try:
                    result_data = json.loads(result)
                    if isinstance(result_data, dict) and result_data.get('success') is False:
                        self._logger.info('Tool %s returned error, stopping loop: %s', tool_name, result_data.get('error'))
                        # Log tool result before returning
                        display_content = json.dumps({
                            '_is_structured': True,
                            'tool_name': tool_name,
                            'success': False,
                            'error': result_data.get('error', 'Unknown error'),
                        })
                        self.env['mcp.session.message'].create({
                            'session_id': session.id if session else None,
                            'role': 'tool_result',
                            'content': display_content,
                            'tool_name': tool_name,
                            'tool_call_id': tool_call_id,
                        })
                        # Return the error as the final response instead of continuing loop
                        return {
                            'text': f"Tool execution failed: {result_data.get('error', 'Unknown error')}. {result_data.get('message', '')}",
                            'tool_calls': [],
                        }, [], total_tool_calls, total_input, total_output, tool_name
                except (json.JSONDecodeError, TypeError):
                    pass  # Not JSON, continue normal flow

                # Log tool result — convert to structured JSON for frontend card rendering
                try:
                    _rd = json.loads(result)
                    display_content = self._format_tool_success_html(tool_name, arguments, _rd)
                except Exception:
                    display_content = result
                self.env['mcp.session.message'].create({
                    'session_id': session.id if session else None,
                    'role': 'tool_result',
                    'content': display_content,
                    'tool_name': tool_name,
                    'tool_call_id': tool_call_id,
                })

                # Append tool result in provider-specific format
                # Append to messages_to_send so it goes in the next provider call
                self._append_tool_result(provider, messages_to_send, tool_call_id, tool_name, result, message_format)

                # Collect terminal write tool results — process all parallel calls before returning
                if tool_name in _TERMINAL_TOOLS:
                    try:
                        r = json.loads(result)
                        if not (isinstance(r, dict) and r.get('success') is False):
                            _terminal_results.append((tool_name, r))
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Return after all parallel terminal tool calls have been processed
            if _terminal_results:
                if len(_terminal_results) == 1:
                    t_name, t_result = _terminal_results[0]
                    done_msg = self._format_terminal_tool_message(t_name, t_result)
                else:
                    done_msg = self._format_terminal_tool_messages_bulk(_terminal_results)
                return {
                    'text': done_msg,
                    'tool_calls': [],
                }, [], total_tool_calls, total_input, total_output, _terminal_results[-1][0]

            # Detect stuck-on-same-tool: if any non-terminal tool called 3+ times, force synthesis
            _TERMINAL_TOOLS_SET = frozenset(['create_echart', 'create_record', 'update_record', 'delete_record'])
            MAX_SAME_TOOL_CALLS = 3
            _force_exit = False
            for (_stuck_tool, _stuck_args_json), _stuck_count in tool_call_counts.items():
                if _stuck_tool not in _TERMINAL_TOOLS_SET and _stuck_count >= MAX_SAME_TOOL_CALLS:
                    # Check if real data (search_read/read_record/read_group) was fetched
                    _data_fetched = any(
                        tn in ('search_read', 'read_record', 'read_group')
                        for (tn, _) in tool_call_counts.keys()
                    )
                    self._logger.warning(
                        'Tool %s called %d times — forcing synthesis (data_fetched=%s)',
                        _stuck_tool, _stuck_count, _data_fetched
                    )

                    # Build synthesis messages: convert tool results → user messages to retain data
                    _synth_msgs = []
                    for _m in messages_to_send:
                        _r = _m.get('role', '')
                        if _r == 'tool':
                            _synth_msgs.append({'role': 'user', 'content': f'[Tool data]: {_m.get("content", "")}'})
                        elif _r == 'tool_result':
                            _synth_msgs.append({'role': 'user', 'content': f'[Tool data]: {_m.get("content", "")}'})
                        elif _r == 'user' and isinstance(_m.get('content'), list):
                            _parts = _m['content']
                            if _parts and isinstance(_parts[0], dict) and _parts[0].get('type') == 'tool_result':
                                _synth_msgs.append({'role': 'user', 'content': f'[Tool data]: {_parts[0].get("content", "")}'})
                            else:
                                _synth_msgs.append(_m)
                        elif _r == 'assistant' and _m.get('tool_calls'):
                            _c = _m.get('content', '')
                            if _c:
                                _synth_msgs.append({'role': 'assistant', 'content': _c})
                        else:
                            _synth_msgs.append(_m)

                    # If schema looped but no records fetched, execute search_read ourselves
                    if not _data_fetched and _stuck_tool == 'get_model_schema':
                        try:
                            _stuck_model = json.loads(_stuck_args_json).get('model', '')
                        except Exception:
                            _stuck_model = ''
                        if _stuck_model:
                            # Derive fields from the schema result in message history
                            _auto_fields = ['id', 'name']
                            for _sm in reversed(messages_to_send):
                                if _sm.get('role') == 'tool':
                                    try:
                                        _raw = json.loads(_sm.get('content', ''))
                                        # Dispatcher wraps result in {'success': True, 'result': {...}}
                                        _schema = _raw.get('result', _raw) if isinstance(_raw, dict) else _raw
                                        if isinstance(_schema, dict):
                                            for _fn in _schema.keys():
                                                if any(_fn.startswith(_p) for _p in ('image_', 'qty_', 'virtual_', 'list_price', 'default_code', 'x_')):
                                                    _auto_fields.append(_fn)
                                    except Exception:
                                        pass
                                    break
                            _auto_fields = list(dict.fromkeys(_auto_fields))[:10]
                            try:
                                _sr_tool = self.env['mcp.tool'].search([('name', '=', 'search_read')], limit=1)
                                if _sr_tool:
                                    _sr_args = {'model': _stuck_model, 'fields': _auto_fields, 'limit': 20}
                                    _sr_result = ToolDispatcher().dispatch(_sr_tool, _sr_args, self.env, self.user)
                                    self._logger.info('Auto search_read for %s returned data', _stuck_model)
                                    _synth_msgs.append({
                                        'role': 'user',
                                        'content': f'[Auto-fetched records from {_stuck_model}]: {_sr_result}',
                                    })
                            except Exception as _sre:
                                self._logger.warning('Auto search_read failed: %s', _sre)

                    # Extract the most recent user question (last non-tool user message)
                    _orig_question = ''
                    for _sm in reversed(_synth_msgs):
                        _sc = str(_sm.get('content', ''))
                        if _sm.get('role') == 'user' and not _sc.startswith('[Tool data]') and not _sc.startswith('[Auto-fetched'):
                            _orig_question = _sc
                            break

                    _synth_msgs.append({
                        'role': 'user',
                        'content': (
                            f"STOP CALLING TOOLS. You have already called `{_stuck_tool}` {_stuck_count} times. "
                            f"The database records fetched so far are present above as [Tool data] or [Auto-fetched records]. "
                            f"Respond NOW using ONLY the data already in this conversation. "
                            f"CRITICAL RULE: If the task requires executing an action (updating records, sending emails, running code) "
                            f"that you have NOT yet performed, you MUST honestly say so — do NOT fabricate or pretend the action was done. "
                            f"Instead, tell the user exactly what you found and what action is still needed. "
                            f"Original question: {_orig_question!r}"
                        )
                    })
                    try:
                        forced_resp = provider.call(agent, _synth_msgs, [])
                        forced_text = forced_resp.get('text') or forced_resp.get('reply') or ''
                        total_input += forced_resp.get('input_tokens', 0)
                        total_output += forced_resp.get('output_tokens', 0)
                        if forced_text:
                            self._logger.info('Forced synthesis succeeded after %d repeated %s calls', _stuck_count, _stuck_tool)
                            return forced_resp, [], total_tool_calls, total_input, total_output, _stuck_tool
                    except Exception as _fe:
                        self._logger.warning('Forced synthesis failed: %s', str(_fe))
                    _force_exit = True
                    break
            if _force_exit:
                break  # Exit outer turn loop — prevent looping indefinitely after failed synthesis

            # Loop back — messages_to_send now contains tool results for next turn

        # Safety: if we hit max turns, return what we have
        self._logger.warning('Max tool-call turns (%d) reached, returning partial response', max_turns)
        return response, tool_calls, total_tool_calls, total_input, total_output, last_tool_name

    def _format_terminal_tool_message(self, tool_name, result):
        if tool_name == 'create_echart':
            res = result.get('result', {}) if isinstance(result, dict) else {}
            name = res.get('name', 'Chart')
            chart_id = res.get('id', '')
            return f'Chart "{name}" created successfully (ID: {chart_id}). View it in the MCP Charts app.'
        elif tool_name == 'create_record':
            res = result.get('result', {}) if isinstance(result, dict) else {}
            record_id = res.get('id', '')
            model_name = res.get('model', '')
            display_name = ''
            if record_id and model_name:
                try:
                    display_name = self.env[model_name].browse(record_id).display_name or ''
                except Exception:
                    pass
            name_part = f' "{display_name}"' if display_name else ''
            return f'Record{name_part} created successfully (ID: {record_id}).'
        elif tool_name == 'update_record':
            return 'Record(s) updated successfully.'
        elif tool_name == 'delete_record':
            return 'Record(s) deleted successfully.'
        return 'Operation completed successfully.'

    def _format_terminal_tool_messages_bulk(self, terminal_results):
        """Summary message when multiple terminal tool calls ran in parallel."""
        create_results = [(tn, r) for tn, r in terminal_results if tn == 'create_record']
        other_results = [(tn, r) for tn, r in terminal_results if tn != 'create_record']

        lines = []
        if create_results:
            # Group by model
            by_model = {}
            for _, r in create_results:
                res = r.get('result', {}) if isinstance(r, dict) else {}
                model = res.get('model', 'unknown')
                record_id = res.get('id', '')
                display_name = ''
                if record_id and model != 'unknown':
                    try:
                        display_name = self.env[model].browse(record_id).display_name or ''
                    except Exception:
                        pass
                by_model.setdefault(model, []).append((record_id, display_name))

            for model, records in by_model.items():
                model_label = model
                try:
                    ir_model = self.env['ir.model'].search([('model', '=', model)], limit=1)
                    if ir_model:
                        model_label = ir_model.name
                except Exception:
                    pass
                lines.append(f'{len(records)} record(s) created in {model_label}:')
                for rid, dname in records:
                    label = f'"{dname}"' if dname else f'ID {rid}'
                    lines.append(f'  • {label} (ID: {rid})')

        for tool_name, _ in other_results:
            lines.append(self._format_terminal_tool_message(tool_name, {}))

        return '\n'.join(lines) if lines else 'Operations completed successfully.'

    def _format_tool_success_html(self, tool_name, arguments, result_data):
        """
        Dynamically formats tool execution success result into a structured JSON string.
        """
        try:
            arg_dict = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except Exception:
            arg_dict = {}

        result_val = result_data.get('result')

        # Get active user's company currency symbol
        try:
            company_currency_symbol = self.env.company.currency_id.symbol or '$'
        except Exception:
            company_currency_symbol = '$'

        # Create structured output
        out = {
            '_is_structured': True,
            'tool_name': tool_name,
            'company_currency_symbol': company_currency_symbol,
        }

        # 1. search_read
        if tool_name == 'search_read' and isinstance(result_val, list):
            model = arg_dict.get('model', '')
            field_types = {}
            model_obj = self.env.get(model)
            if model_obj is not None and result_val:
                requested_fields = arg_dict.get('fields') or list((result_val[0] or {}).keys())
                try:
                    fget = model_obj.fields_get(requested_fields, ['type', 'currency_field'])
                    field_types = {
                        fn: {'type': fi.get('type', 'char'), 'currency_field': fi.get('currency_field')}
                        for fn, fi in fget.items()
                    }
                except Exception:
                    pass
            out.update({
                'model': model,
                'count': len(result_val),
                'records': result_val[:10],
                'field_types': field_types,
            })
            return json.dumps(out)

        # 2. read_record
        elif tool_name == 'read_record':
            model = arg_dict.get('model', '')
            res_id = arg_dict.get('res_id', '')
            item = None
            if isinstance(result_val, list) and result_val:
                item = result_val[0]
            elif isinstance(result_val, dict):
                item = result_val
            field_types = {}
            model_obj = self.env.get(model)
            if model_obj is not None and item:
                try:
                    fget = model_obj.fields_get(list(item.keys()), ['type', 'currency_field'])
                    field_types = {
                        fn: {'type': fi.get('type', 'char'), 'currency_field': fi.get('currency_field')}
                        for fn, fi in fget.items()
                    }
                except Exception:
                    pass
            out.update({
                'model': model,
                'res_id': res_id,
                'record': item,
                'field_types': field_types,
            })
            return json.dumps(out)

        # 3. list_models
        elif tool_name == 'list_models' and isinstance(result_val, list):
            out.update({
                'models': result_val[:20],
                'count': len(result_val)
            })
            return json.dumps(out)

        # 4. get_model_schema
        elif tool_name == 'get_model_schema' and isinstance(result_val, dict):
            model = arg_dict.get('model', 'Unknown Model')
            out.update({
                'model': model,
                'schema': result_val
            })
            return json.dumps(out)

        # 5. read_group
        elif tool_name == 'read_group' and isinstance(result_val, list):
            out.update({'result': result_val})
            return json.dumps(out)

        # 6. create_record
        elif tool_name == 'create_record' and isinstance(result_val, dict):
            model = arg_dict.get('model', '')
            new_id = result_val.get('id', 'unknown')
            out.update({
                'model': model,
                'id': new_id
            })
            return json.dumps(out)

        # 6. update_record
        elif tool_name == 'update_record':
            model = arg_dict.get('model', '')
            res_ids = arg_dict.get('res_ids', [])
            out.update({
                'model': model,
                'count': len(res_ids)
            })
            return json.dumps(out)

        # 7. delete_record
        elif tool_name == 'delete_record':
            model = arg_dict.get('model', '')
            res_ids = arg_dict.get('res_ids', [])
            out.update({
                'model': model,
                'count': len(res_ids)
            })
            return json.dumps(out)

        # set_binary_field
        elif tool_name == 'set_binary_field' and isinstance(result_val, dict):
            out.update({
                'model': result_val.get('model'),
                'record_id': result_val.get('record_id'),
                'field': result_val.get('field'),
                'size_bytes': result_val.get('size_bytes'),
            })
            return json.dumps(out)

        # create_records
        elif tool_name == 'create_records' and isinstance(result_val, dict):
            out.update({'model': result_val.get('model'), 'ids': result_val.get('ids', []), 'count': result_val.get('count', 0)})
            return json.dumps(out)

        # update_records
        elif tool_name == 'update_records' and isinstance(result_val, dict):
            out.update({'model': result_val.get('model'), 'count': result_val.get('count', 0)})
            return json.dumps(out)

        # delete_records
        elif tool_name == 'delete_records' and isinstance(result_val, dict):
            out.update({'model': arg_dict.get('model', result_val.get('model', '')), 'count': result_val.get('count', 0)})
            return json.dumps(out)

        # lookup_model_history
        elif tool_name == 'lookup_model_history' and isinstance(result_val, dict):
            out.update({
                'queried': result_val.get('queried'),
                'current_name': result_val.get('current_name'),
                'renamed': result_val.get('renamed', False),
                'note': result_val.get('note', ''),
            })
            return json.dumps(out)

        # accounting_health_summary
        elif tool_name == 'accounting_health_summary' and isinstance(result_val, dict):
            out.update({
                'receivables': result_val.get('receivables', {}),
                'payables': result_val.get('payables', {}),
                'draft_invoice_backlog': result_val.get('draft_invoice_backlog', 0),
                'as_of': result_val.get('as_of', ''),
            })
            return json.dumps(out)

        # import_from_file
        elif tool_name == 'import_from_file' and isinstance(result_val, dict):
            out.update({
                'model': result_val.get('model'),
                'source_file': result_val.get('source_file'),
                'fields': result_val.get('fields', []),
                'total_rows': result_val.get('total_rows', 0),
                'created_count': result_val.get('created_count', 0),
                'ids': result_val.get('ids', []),
                'errors': result_val.get('errors', []),
            })
            return json.dumps(out)

        # post_message
        elif tool_name == 'post_message' and isinstance(result_val, dict):
            out.update({
                'message_id': result_val.get('message_id'),
                'model': result_val.get('model'),
                'record_id': result_val.get('record_id'),
            })
            return json.dumps(out)

        # get_attachments
        elif tool_name == 'get_attachments' and isinstance(result_val, dict):
            out.update({
                'model': result_val.get('model'),
                'record_id': result_val.get('record_id'),
                'attachments': result_val.get('attachments', []),
                'count': result_val.get('count', 0),
            })
            return json.dumps(out)

        # upload_attachment
        elif tool_name == 'upload_attachment' and isinstance(result_val, dict):
            out.update({
                'id': result_val.get('id'),
                'name': result_val.get('name'),
                'model': result_val.get('model'),
                'record_id': result_val.get('record_id'),
            })
            return json.dumps(out)

        # Fallback to simple success message
        out.update({
            'success': True,
            'result': result_val if isinstance(result_val, (dict, list, str, int, float, bool)) else str(result_val)
        })
        return json.dumps(out)

    def _append_tool_result(self, provider, messages, tool_call_id, tool_name, result, message_format='openai'):
        """
        Append a tool result to the messages array in provider-specific format.

        Delegates to provider.format_tool_result() so each provider adapter owns
        its own history shape. The message_format parameter is kept for backwards
        compatibility but is no longer used — the provider instance itself determines
        the correct format.

        Args:
            provider: The provider adapter instance
            messages: Message list to append to (modified in place)
            tool_call_id: Provider-specific tool call ID
            tool_name: Tool name
            result: JSON string result from tool execution
            message_format: Unused — retained for call-site compatibility
        """
        messages.append(provider.format_tool_result(tool_call_id, tool_name, result))

    def _retry_with_fallback_model(self, provider, agent, messages, tool_specs, original_model):
        """
        Retry the API call with a fallback model when tool result processing fails.

        Args:
            provider: Provider adapter instance
            agent: Agent record
            messages: Current message list
            tool_specs: Tool specifications
            original_model: Original model name to restore after fallback

        Returns:
            dict: Response from fallback model call, or None if failed
        """
        provider_type = agent.provider
        fallback_model = FALLBACK_MODELS.get(provider_type)

        if not fallback_model:
            self._logger.warning('No fallback model defined for provider: %s', provider_type)
            return None

        self._logger.info('Retrying with fallback model: %s (was: %s)', fallback_model, original_model)

        # Temporarily switch to fallback model
        original_model_name = agent.model_name
        agent.model_name = fallback_model

        try:
            response = provider.call(agent, messages, tool_specs)
            return response
        except Exception as e:
            self._logger.warning('Fallback model call failed: %s', str(e))
            return None
        finally:
            # Restore original model
            agent.model_name = original_model_name

    def _inject_fallback_message(self, messages, tool_name, tool_result, original_user_message):
        """
        Inject tool result as plain text user message instead of proper tool_result format.
        This is a compatibility mode for providers that can't handle tool_result format.

        Args:
            messages: Message list to modify
            tool_name: Name of the tool that was executed
            tool_result: Result from tool execution
            original_user_message: The original user message for context

        Returns:
            None - messages list is modified in place
        """
        # Parse tool result to get readable content
        try:
            result_data = json.loads(tool_result)
            if result_data.get('success'):
                result_text = json.dumps(result_data.get('result'), indent=2)
            else:
                result_text = result_data.get('error', 'Unknown error')
        except (json.JSONDecodeError, TypeError):
            result_text = tool_result

        # Create plain text injection message
        fallback_msg = (
            f"The '{tool_name}' tool returned the following result:\n"
            f"{result_text}\n\n"
            f"Based on this result, please provide a final answer to the user's question: "
            f"{original_user_message}"
        )

        # Remove any tool_result messages and add plain text instead
        messages_to_keep = [m for m in messages if m.get('role') not in ('tool', 'user') or m.get('role') == 'user']

        # Also remove tool_calls from assistant messages
        for msg in messages_to_keep:
            if 'tool_calls' in msg:
                msg.pop('tool_calls', None)

        messages_to_keep.append({'role': 'user', 'content': fallback_msg})

        # Replace messages
        messages.clear()
        messages.extend(messages_to_keep)

        self._logger.info('Injected fallback message for tool: %s', tool_name)

    def _call_external_mcp_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """
        Call a tool on an external MCP server.

        Args:
            server_name: Name of the external MCP server
            tool_name: Tool name on the external server
            arguments: Tool arguments

        Returns:
            str: JSON result string
        """
        try:
            import httpx

            server = self.env['mcp.external.server'].search([
                ('name', '=', server_name),
                ('active', '=', True),
            ], limit=1)

            if not server:
                return json.dumps({'success': False, 'error': f'External server not found: {server_name}'})

            if server.server_type == 'stdio':
                return self._call_stdio_mcp_tool(server, tool_name, arguments)

            headers = {'Content-Type': 'application/json'}
            auth_credential = server.get_decrypted_auth_value()
            if server.auth_type == 'bearer' and auth_credential:
                headers['Authorization'] = f'Bearer {auth_credential}'
            elif server.auth_type == 'api_key' and auth_credential:
                headers['X-API-Key'] = auth_credential

            payload = {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': tool_name,
                    'arguments': arguments,
                },
            }

            with httpx.Client(timeout=30) as client:
                resp = client.post(server.url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                if 'error' in data:
                    return json.dumps({'success': False, 'error': data['error'].get('message', 'Unknown error')})

                if 'result' in data:
                    result_data = data['result']
                    if result_data.get('isError'):
                        return json.dumps({'success': False, 'error': result_data['content'][0]['text']})
                    return json.dumps({'success': True, 'result': result_data['content'][0]['text']})

                return json.dumps({'success': False, 'error': 'Unexpected response format'})

        except httpx.TimeoutException:
            return json.dumps({'success': False, 'error': 'External MCP server timed out'})
        except Exception as e:
            self._logger.error('External MCP tool call failed: %s', str(e))
            return json.dumps({'success': False, 'error': str(e)})

    def _call_stdio_mcp_tool(self, server, tool_name: str, arguments: dict) -> str:
        """Call a tool on a stdio MCP server via JSON-RPC over subprocess stdin/stdout."""
        import subprocess
        import shlex
        import os

        command = server.command.strip()
        args_raw = server.args or ''
        try:
            extra_args = json.loads(args_raw) if args_raw.strip().startswith('[') else shlex.split(args_raw)
        except Exception:
            extra_args = []

        env_extra = {}
        if server.env_vars:
            try:
                env_extra = json.loads(server.env_vars)
            except Exception:
                pass

        proc = subprocess.Popen(
            [command] + extra_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, **env_extra},
        )
        try:
            def _send(msg):
                proc.stdin.write((json.dumps(msg) + '\n').encode())
                proc.stdin.flush()

            def _recv():
                line = _readline_with_timeout(proc.stdout)
                return json.loads(line) if line else {}

            _send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "odoo-mcp-gateway", "version": "1.0"},
            }})
            _recv()
            _send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            _send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": tool_name,
                "arguments": arguments,
            }})
            data = _recv()

            if 'error' in data:
                return json.dumps({'success': False, 'error': data['error'].get('message', 'Unknown error')})

            result = data.get('result', {})
            if result.get('isError'):
                content = result.get('content', [{}])
                return json.dumps({'success': False, 'error': content[0].get('text', 'Tool error')})

            content = result.get('content', [{}])
            text = content[0].get('text', json.dumps(result)) if content else json.dumps(result)
            return json.dumps({'success': True, 'result': text})

        except Exception as e:
            self._logger.error('Stdio MCP tool call failed (%s/%s): %s', server.name, tool_name, str(e))
            return json.dumps({'success': False, 'error': str(e)})
        finally:
            proc.kill()
            proc.wait()

    def _build_message_history(self, session, system_prompt: str) -> list:
        """
        Build message array from session history.

        Args:
            session: mcp.session
            system_prompt: System prompt to prepend

        Returns:
            list: Messages array [{'role': '...', 'content': '...'}]
        """
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})

        for msg in self.env['mcp.session.message'].search(
            [('session_id', '=', session.id)],
            order='create_date ASC'
        ):
            if msg.role in ['user', 'assistant']:
                messages.append({'role': msg.role, 'content': msg.content})
            elif msg.role == 'tool_result':
                # Tool results handled separately in loop
                pass

        return messages
