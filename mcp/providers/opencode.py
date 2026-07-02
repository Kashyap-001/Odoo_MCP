"""
mcp_gateway/mcp/providers/opencode.py

OpenCode AI API adapter using OpenAI SDK.

Key classes:
  OpenCodeAdapter — Adapter for OpenCode AI models

Dependencies:
  - openai package (pip install openai)
  - base.AbstractProvider

Developer notes:
  - OpenCode Zen uses OpenAI-compatible API format
  - Base URL: https://opencode.ai/zen
  - Endpoints: /v1/chat/completions (minimax/qwen/glm/kimi), /v1/responses (GPT), /v1/messages (Claude)
  - Auth: Bearer token in Authorization header
"""

import logging
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OpenCodeAdapter(AbstractProvider):
    """
    OpenCode AI API adapter using OpenAI SDK.

    Implements provider interface for OpenCode AI models.
    Uses OpenAI-compatible API format via openai Python SDK.

    Example:
        agent = env['mcp.agent'].search([('provider','=','opencode')], limit=1)
        provider = OpenCodeAdapter(env)
        result = provider.call(agent, messages, tool_specs)
    """

    DEFAULT_BASE_URL = 'https://opencode.ai/zen'

    def build_headers(self, agent) -> dict:
        """
        Build headers - not needed with SDK.

        Args:
            agent: mcp.agent record

        Returns:
            dict: Minimal headers
        """
        return {'Content-Type': 'application/json'}

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        """
        Build request payload for OpenCode API (MiniMax models).

        Uses 'functions' format instead of 'tools' (older OpenAI format).

        Args:
            messages: Message history
            tool_specs: Tool definitions (can be in old format or new OpenAI format)
            agent: mcp.agent with LLM parameters

        Returns:
            dict: Request payload for SDK
        """
        # Build functions array - handle both old and new format
        # Old format: {'name': ..., 'description': ..., 'input_schema': ...}
        # New format: {'type': 'function', 'function': {'name': ..., 'description': ..., 'parameters': ...}}
        functions = []
        for spec in tool_specs:
            if 'function' in spec:
                # New OpenAI format - extract from nested function
                func = spec.get('function', {})
                functions.append({
                    'name': func.get('name', ''),
                    'description': func.get('description', ''),
                    'parameters': func.get('parameters', {}),
                })
            else:
                # Old format - use directly
                functions.append({
                    'name': spec.get('name', ''),
                    'description': spec.get('description', ''),
                    'parameters': spec.get('input_schema', {}),
                })

        payload = {
            'model': agent.model_name or 'minimax-m2.5-free',
            'messages': messages,
        }

        # Add optional parameters from agent
        if agent.max_tokens:
            payload['max_tokens'] = agent.max_tokens
        if agent.temperature:
            payload['temperature'] = agent.temperature

        # Use 'tools' format (not deprecated 'functions')
        if functions:
            # Convert to new tools format: [{'type': 'function', 'function': {...}}]
            payload['tools'] = [
                {'type': 'function', 'function': f}
                for f in functions
            ]
            payload['tool_choice'] = 'auto'

        return payload

    def parse_response(self, raw_json: dict) -> dict:
        """
        Parse OpenCode SDK response to standard format.

        Handles both 'tools' (new) and 'function_call' (legacy) formats.
        Also handles error responses from upstream providers.

        Args:
            raw_json: Full response from SDK

        Returns:
            dict: Standardized response with text, tool_calls, tokens
        """
        try:
            _logger.info('OpenCode response: %s', raw_json)

            # Check for error in response (upstream provider errors)
            if 'error' in raw_json:
                error_info = raw_json.get('error', {})
                error_msg = error_info.get('message', 'Unknown error')
                error_code = error_info.get('code', '')
                _logger.error('OpenCode upstream error: %s (code: %s)', error_msg, error_code)

                # Return empty response - the gateway will handle the retry or continue
                # Include error info in the text so the user sees what happened
                return {
                    'text': f'[Provider error: {error_msg}]',
                    'reply': f'[Provider error: {error_msg}]',
                    'stop_reason': 'error',
                    'tool_calls': [],
                    'input_tokens': raw_json.get('usage', {}).get('prompt_tokens', 0) if raw_json.get('usage') else 0,
                    'output_tokens': raw_json.get('usage', {}).get('completion_tokens', 0) if raw_json.get('usage') else 0,
                }

            choices = raw_json.get('choices')
            if not choices:
                _logger.warning('OpenCode response has no choices: %s', raw_json)
                return {
                    'text': '',
                    'reply': '',
                    'stop_reason': 'empty',
                    'tool_calls': [],
                    'input_tokens': 0,
                    'output_tokens': 0,
                }
            choice = choices[0]
            message = choice.get('message', {})
            text = message.get('content') or ''
            stop_reason = choice.get('finish_reason', 'unknown')

            # Extract tool calls - check both 'tool_calls' and 'function_call' formats
            tool_calls = []

            # New format: tool_calls array
            tc_list = message.get('tool_calls')
            if tc_list:
                for tc in tc_list:
                    tool_calls.append({
                        'id': tc.get('id', ''),
                        'name': tc.get('function', {}).get('name', ''),
                        'arguments': tc.get('function', {}).get('arguments', '{}'),
                    })

            # Legacy format: function_call object
            if not tool_calls:
                fc = message.get('function_call')
                if fc:
                    tool_calls.append({
                        'id': 'fc_1',
                        'name': fc.get('name', ''),
                        'arguments': fc.get('arguments', '{}'),
                    })

            return {
                'text': text,
                'reply': text,
                'stop_reason': stop_reason,
                'tool_calls': tool_calls,
                'input_tokens': raw_json.get('usage', {}).get('prompt_tokens', 0),
                'output_tokens': raw_json.get('usage', {}).get('completion_tokens', 0),
            }
        except Exception as e:
            _logger.error('OpenCode response parse error: %s', str(e))
            raise UserError(_('Failed to parse OpenCode response: %s') % str(e))

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        """
        Call OpenCode API using httpx.

        Routes to different endpoints based on model family:
        - claude-* models: POST /v1/messages (Anthropic format)
        - gpt-* models: POST /v1/responses (OpenAI Responses API)
        - others: POST /v1/chat/completions (OpenAI-compatible)

        Args:
            agent: mcp.agent record
            messages: List of message dicts
            tool_specs: List of tool specifications

        Returns:
            dict: Standardized response

        Raises:
            UserError: on API errors
        """
        try:
            import httpx

            api_key = agent._decrypt_api_key()
            base_url = (agent.api_base_url or self.DEFAULT_BASE_URL).rstrip('/')
            model_id = (agent.model_name or '').lower()

            # Route to different endpoints based on model family
            if model_id.startswith('claude-'):
                endpoint = '/v1/messages'
            elif model_id.startswith('gpt-'):
                endpoint = '/v1/responses'
            else:
                # minimax, deepseek, qwen, glm, kimi, etc.
                endpoint = '/v1/chat/completions'

            url = f"{base_url}{endpoint}"

            # Convert messages to format expected by the endpoint
            payload = self._build_payload_for_endpoint(messages, tool_specs, agent, endpoint)

            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            }

            _logger.info('Calling OpenCode with model: %s, endpoint: %s', agent.model_name, endpoint)

            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, headers=headers, json=payload)

            _logger.info('OpenCode response: status=%s, body=%s', response.status_code, response.text[:500])

            if response.status_code == 401:
                # Try to extract the actual error message from the body
                error_msg = "Invalid OpenCode API key. Please check your API key."
                try:
                    error_data = response.json()
                    if 'error' in error_data and 'message' in error_data['error']:
                        error_msg = error_data['error']['message']
                except Exception:
                    pass
                raise UserError(_('OpenCode API error (401): %s') % error_msg)

            response.raise_for_status()
            return self.parse_response(response.json())

        except UserError:
            # Re-raise UserErrors immediately so they aren't caught and rewritten
            raise
        except httpx.HTTPStatusError as e:
            _logger.error('OpenCode HTTP error: %s', str(e))
            raise UserError(_('OpenCode API error: %s') % str(e))
        except Exception as e:
            error_str = str(e)
            if 'authentication' in error_str.lower() or 'api key' in error_str.lower():
                _logger.error('OpenCode authentication failed: %s', error_str)
                raise UserError(_('Invalid OpenCode API key. Please check your API key.'))
            elif 'rate_limit' in error_str.lower():
                _logger.error('OpenCode rate limit exceeded: %s', error_str)
                raise UserError(_('OpenCode rate limit exceeded. Please try again later.'))
            else:
                _logger.error('OpenCode call failed: %s', error_str)
                raise UserError(_('OpenCode API error: %s') % str(e))

    def _build_payload_for_endpoint(self, messages: list, tool_specs: list, agent, endpoint: str) -> dict:
        """
        Build payload in format expected by the specific endpoint.

        Args:
            messages: Message history
            tool_specs: Tool specifications
            agent: Agent record
            endpoint: Target endpoint path

        Returns:
            dict: Formatted payload
        """
        # Build tools in appropriate format for the endpoint
        tools = None
        if tool_specs:
            if endpoint == '/v1/messages':
                # Anthropic format
                tools = [
                    {
                        'name': spec.get('name', ''),
                        'description': spec.get('description', ''),
                        'input_schema': spec.get('input_schema', {}),
                    }
                    for spec in tool_specs
                ]
            else:
                # OpenAI format (/v1/responses or /v1/chat/completions)
                tools = [
                    {
                        'type': 'function',
                        'function': {
                            'name': spec.get('name', ''),
                            'description': spec.get('description', ''),
                            'parameters': spec.get('input_schema', {}),
                        }
                    }
                    for spec in tool_specs
                ]

        # Extract system message
        system_message = ''
        filtered_messages = []
        for msg in messages:
            if msg.get('role') == 'system':
                system_message = msg.get('content', '')
            else:
                filtered_messages.append(msg)

        if endpoint == '/v1/messages':
            # Anthropic format
            return {
                'model': agent.model_name or 'minimax-m2.5-free',
                'max_tokens': agent.max_tokens or 4096,
                'temperature': agent.temperature or 1.0,
                'top_p': agent.top_p or 1.0,
                'system': system_message,
                'messages': filtered_messages,
                'tools': tools,
            }
        elif endpoint == '/v1/responses':
            # OpenAI Responses API format
            return {
                'model': agent.model_name or 'minimax-m2.5-free',
                'input': filtered_messages,
                'max_tokens': agent.max_tokens or 4096,
                'temperature': agent.temperature or 1.0,
                'tools': tools,
            }
        else:
            # OpenAI Chat Completions format
            payload = {
                'model': agent.model_name or 'minimax-m2.5-free',
                'messages': messages,  # Keep original messages including system
                'max_tokens': agent.max_tokens or 4096,
                'temperature': agent.temperature or 1.0,
                'top_p': agent.top_p or 1.0,
            }
            if tools:
                payload['tools'] = tools
                payload['tool_choice'] = 'auto'
            return payload

    def get_available_models(self, agent) -> list:
        """
        Get available OpenCode models.

        Args:
            agent: mcp.agent record

        Returns:
            list: Available model IDs
        """
        try:
            from openai import OpenAI

            api_key = agent._decrypt_api_key()
            base_url = (agent.api_base_url or self.DEFAULT_BASE_URL).rstrip('/')

            client = OpenAI(api_key=api_key, base_url=base_url, timeout=10.0)

            # Try to get models list from API
            models_response = client.models.list()
            models = [m.id for m in models_response.data]

            if models:
                return models

        except Exception as e:
            _logger.warning('Failed to fetch OpenCode models: %s', str(e))

        # Return known OpenCode Zen models (https://opencode.ai/zen)
        # Organized by format: Anthropic (/v1/messages), OpenAI (/v1/responses), OpenAI-compatible (/v1/chat/completions)
        return [
            # Free models
            'minimax-m2.5-free',
            'deepseek-v4-flash-free',
            'big-pickle',
            'ring-2.6-1t-free',
            'nemotron-3-super-free',
            # MiniMax (OpenAI-compatible /v1/chat/completions)
            'minimax-m2.7',
            'minimax-m2.5',
            # Qwen (OpenAI-compatible /v1/chat/completions)
            'qwen3.6-plus',
            'qwen3.5-plus',
            # GLM (OpenAI-compatible /v1/chat/completions)
            'glm-5.1',
            'glm-5',
            # Kimi (OpenAI-compatible /v1/chat/completions)
            'kimi-k2.6',
            'kimi-k2.5',
            # OpenAI models via Zen (/v1/responses)
            'gpt-5.5',
            'gpt-5.5-pro',
            'gpt-5.4',
            'gpt-5.4-pro',
            'gpt-5.4-mini',
            'gpt-5.4-nano',
            'gpt-5.3-codex',
            'gpt-5.3-codex-spark',
            'gpt-5.2',
            'gpt-5.2-codex',
            'gpt-5.1',
            'gpt-5.1-codex',
            'gpt-5.1-codex-max',
            'gpt-5.1-codex-mini',
            'gpt-5',
            'gpt-5-codex',
            'gpt-5-nano',
            'gpt-4o',
            'gpt-4o-mini',
            # Claude models via Zen (/v1/messages)
            'claude-opus-4-7',
            'claude-opus-4-6',
            'claude-opus-4-5',
            'claude-opus-4-1',
            'claude-sonnet-4-6',
            'claude-sonnet-4-5',
            'claude-sonnet-4',
            'claude-haiku-4-5',
            'claude-3-5-haiku',
            # Gemini via Zen (Google format)
            'gemini-3.1-pro',
            'gemini-3-flash',
        ]

    def format_tool_calls(self, tool_calls: list) -> list:
        """Uses OpenAI format: tool_calls list with id/type/function keys."""
        import json as _json
        return [
            {
                'id': tc.get('id', f'tc_{i}'),
                'type': 'function',
                'function': {
                    'name': tc.get('name', ''),
                    'arguments': (
                        tc.get('arguments', '{}')
                        if isinstance(tc.get('arguments'), str)
                        else _json.dumps(tc.get('arguments', {}))
                    ),
                }
            }
            for i, tc in enumerate(tool_calls)
        ]

    def format_tool_result(self, tool_call_id: str, tool_name: str, result: str) -> dict:
        """Uses OpenAI format: role=tool message with tool_call_id."""
        return {'role': 'tool', 'tool_call_id': tool_call_id, 'content': result}