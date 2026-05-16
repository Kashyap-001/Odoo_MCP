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
from .tools.dispatcher import ToolDispatcher

_logger = logging.getLogger(__name__)

# Fallback models per provider - used when tool result processing fails
FALLBACK_MODELS = {
    'opencode': 'minimax-m2.5-free',
    'openai': 'gpt-4o-mini',
    'anthropic': 'claude-haiku-4-5-20250501',
    'gemini': 'gemini-1.5-flash',
    'ollama': 'llama3.1',
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
            # minimax, deepseek, qwen, glm, kimi, gpt, etc.
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
            active_model: str = None, active_id: int = None) -> dict:
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

        # ── 4a. Inject current date/time into system prompt (fresh per API call) ──
        # datetime.now() called inline - no intermediate variables
        system_prompt += f"\n\nCURRENT DATE AND TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (server) / {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\nWhen user says 'today', 'tomorrow', etc, calculate from this date, never guess."
        _logger.info("DATE_INJECTION_CHECK: %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        # ── 4b. Add tool usage rules to prevent hallucination ───────────────
        system_prompt += """
IMPORTANT TOOL USAGE RULES:
- You must call a tool for EVERY action request, even if you performed a similar action earlier in this session.
- Each user request is independent. Do not assume a previous tool call satisfies the current request.
- Never report success for an action unless you received a tool result confirming it in THIS turn.
- If the user asks to create, update, delete, or search for anything, you MUST call the appropriate tool. Do not skip the tool call based on previous history."""

        # ── 5. Build tool specs from agent tools + external MCP servers ───
        tool_specs = self._build_tool_specs(agent.effective_tool_ids, agent)

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

        # Always append the new user message
        messages.append({'role': 'user', 'content': user_message})

        # Log user message
        self.env['mcp.session.message'].create({
            'session_id': session.id,
            'role': 'user',
            'content': user_message,
        })

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
        if user.has_group('mcp_gateway.group_mcp_admin'):
            return  # Admins can access all

        rules = self.env['mcp.access.rule'].get_rules_for_user(user)
        if agent.id not in rules['agent_ids'].ids and len(rules['agent_ids']) > 0:
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

    def _inject_datetime(self, system_prompt: str) -> str:
        """
        Inject current date and time into system prompt.

        This is called fresh on every provider call to ensure the model
        knows the actual current date, not a cached/hallucinated date.

        Args:
            system_prompt: Base system prompt

        Returns:
            str: System prompt with datetime injected
        """
        # Get current datetime in UTC and local time
        now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        now_local = datetime.now(pytz.timezone('UTC')).astimezone().strftime('%Y-%m-%d %H:%M:%S')

        datetime_block = f"""
CURRENT DATE AND TIME:
- Server local time: {now_local}
- UTC time: {now_utc}

IMPORTANT: When the user says 'today', 'tomorrow', 'this week', 'next week',
'alast week', or any relative date, ALWAYS calculate from the current date
and time shown above. Never guess or assume a date. Always verify dates
against this reference.
"""

        _logger.debug('Injected datetime into system prompt: %s', now_local)

        return system_prompt + datetime_block

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

    def _get_datetime_guidance(self) -> str:
        """
        Get current date and time guidance string.

        This is called fresh on every provider call.

        Returns:
            str: Datetime guidance block
        """
        now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        now_local = datetime.now(pytz.timezone('UTC')).astimezone().strftime('%Y-%m-%d %H:%M:%S')

        return f"CURRENT DATE AND TIME: Server local: {now_local} / UTC: {now_utc}. When user says 'today', 'tomorrow', etc, calculate from this date."

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

            conversation = '\n'.join([
                f'{m.role}: {m.content}' for m in messages
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
        except Exception as e:
            self._logger.warning('Failed to load external MCP servers: %s', str(e))

        return tools

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

            # Build assistant message from response
            # Note: Don't include tool_calls in the message for providers that don't need it
            # Some providers like MiniMax get confused when tool_calls persists across turns
            assistant_msg = {
                'role': 'assistant',
                'content': response.get('text') or response.get('reply') or '',
            }

            # Only add tool_calls to message for providers that require it in message history
            provider_name = getattr(provider, '__class__', None).__name__ or ''
            if response.get('tool_calls') and 'MiniMax' not in provider_name and 'OpenCode' not in provider_name:
                assistant_msg['tool_calls'] = response.get('tool_calls')

            messages_to_send.append(assistant_msg)

            tool_calls = response.get('tool_calls', [])
            response_text = response.get('text') or response.get('reply') or ''
            total_input += response.get('input_tokens', 0)
            total_output += response.get('output_tokens', 0)

            # Detect possible hallucination: model returned stop with no tool call for action request
            # on first turn of a new request (turn 0 means first API call in this loop)
            if turn == 0 and not tool_calls and response.get('finish_reason') in ('stop', 'end_turn'):
                # Check if this is a fresh session (fewer than 3 messages before user message)
                # Count messages (excluding system and synthetic date exchange)
                non_system_msgs = [m for m in messages_to_send if m.get('role') != 'system']
                is_fresh_session = len(non_system_msgs) <= 3

                # Find the user message in history to check for action words
                user_msg_for_check = None
                for msg in messages:
                    if msg.get('role') == 'user' and msg.get('content'):
                        user_msg_for_check = msg.get('content', '')
                        break

                if user_msg_for_check:
                    user_msg_lower = user_msg_for_check.lower()
                    action_words = ['create', 'make', 'add', 'update', 'delete', 'schedule',
                                    'find', 'search', 'book', 'cancel', 'list', 'show', 'get']
                    has_action_word = any(w in user_msg_lower for w in action_words)

                    if has_action_word:
                        if is_fresh_session:
                            # Fresh session with action request and no tool call - just log WARNING
                            # Do NOT retry automatically per requirements - just log
                            _logger.warning(
                                "HALLUCINATION DETECTED: fresh session with action word '%s' "
                                "but no tool calls returned",
                                user_msg_for_check[:50]
                            )
                        else:
                            _logger.warning(
                                "POSSIBLE HALLUCINATION: model returned stop with no tool call "
                                "for action request: %s",
                                user_msg_for_check[:100]
                            )

            # Detect if we're stuck in a loop (tool calls without final text after tool execution)
            # This happens when provider keeps calling tools instead of returning final answer
            if tool_calls and not response_text and total_tool_calls > 0:
                # This is a loop - provider returned tool calls instead of final answer
                self._logger.warning('Provider stuck in tool call loop at turn %d, trying fallbacks', turn)

                # Get original user message for fallback injection
                original_user_msg = None
                for msg in messages:
                    if msg.get('role') == 'user' and msg.get('content'):
                        original_user_msg = msg.get('content')
                        break

                # Try 1: Fallback model (only on first loop detection)
                if turn == 1:  # Only try on first loop detection
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
            for i, tool_call in enumerate(tool_calls):
                tool_name = tool_call['name']
                arguments = tool_call['arguments']
                tool_call_id = tool_call.get('id') or f'tc_{turn}_{i}'

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
                elif not tool:
                    result = json.dumps({'success': False, 'error': f'Tool not found: {tool_name}'})
                    self._logger.warning('Tool not found: %s', tool_name)
                else:
                    # Check access to tool
                    rules = self.env['mcp.access.rule'].get_rules_for_user(self.user)
                    if len(rules['tool_ids']) > 0 and tool.id not in rules['tool_ids'].ids:
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
                        self.env['mcp.session.message'].create({
                            'session_id': session.id if session else None,
                            'role': 'tool_result',
                            'content': result,
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

                # Log tool result
                self.env['mcp.session.message'].create({
                    'session_id': session.id if session else None,
                    'role': 'tool_result',
                    'content': result,
                    'tool_name': tool_name,
                    'tool_call_id': tool_call_id,
                })

                # Append tool result in provider-specific format
                # Append to messages_to_send so it goes in the next provider call
                self._append_tool_result(provider, messages_to_send, tool_call_id, tool_name, result, message_format)

                # If tool executed successfully, stop the loop and return success message
                # This prevents the model from calling the same tool multiple times
                try:
                    result_data = json.loads(result)
                    if isinstance(result_data, dict) and result_data.get('success') is True:
                        self._logger.info('Tool %s succeeded, stopping loop', tool_name)
                        # Format success message for user
                        tool_result_text = result_data.get('result', {})
                        if isinstance(tool_result_text, dict):
                            event_id = tool_result_text.get('id', 'unknown')
                            event_name = tool_result_text.get('name', tool_name)
                            success_msg = f"Successfully created: {event_name} (ID: {event_id})"
                        else:
                            success_msg = f"Action completed: {tool_name}"

                        # Return immediately with success - don't let model loop
                        return {
                            'text': success_msg,
                            'tool_calls': [],
                        }, [], total_tool_calls, total_input, total_output, tool_name
                except (json.JSONDecodeError, TypeError):
                    pass  # Not JSON, continue normal flow

            # Loop back — messages_to_send now contains tool results for next turn

        # Safety: if we hit max turns, return what we have
        self._logger.warning('Max tool-call turns (%d) reached, returning partial response', max_turns)
        return response, tool_calls, total_tool_calls, total_input, total_output, last_tool_name

    def _append_tool_result(self, provider, messages, tool_call_id, tool_name, result, message_format='openai'):
        """
        Append a tool result to the messages array in provider-specific format.

        Anthropic:    {'role': 'user', 'content': [{'type': 'tool_result', ...}]}
        OpenAI:       {'role': 'tool', 'tool_call_id': ..., 'content': ...}
        Gemini:       {'role': 'user', 'parts': [{'functionResponse': ...}]}

        Args:
            provider: The provider adapter instance
            messages: Message list to append to (modified in place)
            tool_call_id: Provider-specific tool call ID
            tool_name: Tool name
            result: JSON string result from tool execution
            message_format: Target format ('anthropic', 'openai', 'gemini')
        """
        # Use message_format instead of provider name for consistent format handling
        if message_format == 'anthropic':
            messages.append({
                'role': 'user',
                'content': [{
                    'type': 'tool_result',
                    'tool_use_id': tool_call_id,
                    'content': result,
                }],
            })
        elif message_format == 'openai':
            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'content': result,
            })
        elif message_format == 'gemini':
            messages.append({
                'role': 'user',
                'parts': [{
                    'functionResponse': {
                        'name': tool_name,
                        'response': {'result': result},
                    }
                }],
            })
        else:
            # Default to OpenAI format
            messages.append({
                'role': 'tool',
                'tool_call_id': tool_call_id,
                'content': result,
            })

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
