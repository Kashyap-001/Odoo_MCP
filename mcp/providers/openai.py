"""
mcp_gateway/mcp/providers/openai.py

OpenAI GPT API adapter — raw HTTPS via requests, no vendor SDK.

Key classes:
  OpenAIAdapter — Adapter for OpenAI's GPT-4, GPT-4 Turbo, GPT-4o models

Dependencies:
  - requests (already a hard Odoo dependency — no extra pip install needed)
  - base.AbstractProvider

Developer notes:
  - Talks directly to the Chat Completions API; build_payload() already returns
    the exact JSON body this REST endpoint expects
  - Tool format: OpenAI tools array with function objects
  - Models: gpt-4, gpt-4-turbo, gpt-4o
"""

import logging
import json
import requests
from .base import AbstractProvider, attach_retry_after
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = 'https://api.openai.com/v1/chat/completions'
_OPENAI_MODELS_URL = 'https://api.openai.com/v1/models'


class OpenAIAdapter(AbstractProvider):
    """
    OpenAI GPT API adapter using official SDK.

    Implements provider interface for OpenAI's GPT models.
    Supports function calling via tools array.

    Example:
        agent = env['mcp.agent'].search([('provider','=','openai')], limit=1)
        provider = OpenAIAdapter(env)
        result = provider.call(agent, messages, tool_specs)
    """

    def build_headers(self, agent) -> dict:
        """
        Build headers for a direct OpenAI Chat Completions API call.

        Args:
            agent: mcp.agent record (with decrypted api_key)

        Returns:
            dict: HTTP headers including Bearer auth
        """
        return {
            'Authorization': f'Bearer {agent._decrypt_api_key()}',
            'Content-Type': 'application/json',
        }

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        """
        Build request for OpenAI API using SDK format.

        Converts generic messages/tools to OpenAI SDK format.

        Args:
            messages: Message history
            tool_specs: Tool definitions
            agent: mcp.agent with LLM parameters

        Returns:
            dict: Request payload for SDK
        """
        # Build functions array in OpenAI format
        functions = []
        for spec in tool_specs:
            functions.append({
                'name': spec['name'],
                'description': spec['description'],
                'parameters': spec.get('input_schema', {}),
            })

        # Filter out system prompt - handled by SDK
        sdk_messages = []
        for msg in messages:
            if msg['role'] != 'system':
                sdk_messages.append({
                    'role': msg['role'],
                    'content': msg['content'],
                })

        # Extract system message from messages parameter (may include datetime injection)
        system_message = ''
        for msg in messages:
            if msg.get('role') == 'system':
                system_message = msg.get('content', '')
                break

        # Insert system prompt as first message if not present
        payload_messages = sdk_messages
        if sdk_messages and sdk_messages[0]['role'] != 'system' and (system_message or agent.system_prompt):
            payload_messages = [
                {'role': 'system', 'content': system_message or agent.system_prompt},
            ] + sdk_messages

        payload = {
            'model': agent.model_name,
            'max_tokens': agent.max_tokens,
            'temperature': agent.temperature,
            'top_p': agent.top_p,
            'messages': payload_messages,
        }
        if functions:
            payload['tools'] = functions
        return payload

    def parse_response(self, raw_json: dict) -> dict:
        """
        Parse OpenAI SDK response.

        Extracts text, tool calls, and token usage from response.

        Args:
            raw_json: Full response from SDK

        Returns:
            dict: Standardized response with text, tool_calls, tokens
        """
        try:
            choice = raw_json.get('choices', [{}])[0]
            message = choice.get('message', {})
            text = message.get('content')
            stop_reason = choice.get('finish_reason', 'unknown')
            tool_calls = []

            # Extract tool calls if present
            function_call = message.get('function_call')
            if function_call:
                raw_args = function_call.get('arguments', '{}')
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                tool_calls.append({
                    'id': function_call.get('name', ''),
                    'name': function_call.get('name', ''),
                    'arguments': parsed_args,
                })

            # Also check tool_calls array
            for tc in message.get('tool_calls', []):
                func = tc.get('function', {})
                raw_args = func.get('arguments', '{}')
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                tool_calls.append({
                    'id': tc.get('id', ''),
                    'name': func.get('name', ''),
                    'arguments': parsed_args,
                })

            return {
                'text': text,
                'stop_reason': stop_reason,
                'tool_calls': tool_calls,
                'input_tokens': raw_json.get('usage', {}).get('prompt_tokens', 0),
                'output_tokens': raw_json.get('usage', {}).get('completion_tokens', 0),
            }
        except Exception as e:
            _logger.error('OpenAI response parse error: %s', str(e))
            raise UserError(_('Failed to parse OpenAI response: %s') % str(e))

    def format_tool_calls(self, tool_calls: list) -> list:
        """OpenAI spec: assistant message must carry tool_calls so results match by ID."""
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
        """OpenAI tool result: role=tool message with matching tool_call_id."""
        return {'role': 'tool', 'tool_call_id': tool_call_id, 'content': result}

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        """
        Make a direct HTTPS call to the OpenAI Chat Completions API.

        Args:
            agent: mcp.agent record
            messages: Message history
            tool_specs: Tool specifications

        Returns:
            dict: Standardized response
        """
        headers = self.build_headers(agent)
        payload = self.build_payload(messages, tool_specs, agent)

        _logger.info('Calling OpenAI API with model: %s', payload.get('model'))

        try:
            response = requests.post(_OPENAI_CHAT_URL, headers=headers, json=payload, timeout=60)
        except requests.exceptions.ConnectionError as e:
            _logger.error('OpenAI connection error: %s', str(e))
            raise UserError(_('Failed to connect to OpenAI API. Check your internet connection.'))
        except requests.exceptions.Timeout as e:
            _logger.error('OpenAI request timed out: %s', str(e))
            raise UserError(_('OpenAI API request timed out.'))

        if response.status_code == 401:
            _logger.error('OpenAI authentication failed: %s', response.text)
            raise UserError(_('Invalid OpenAI API key. Please check your API key.'))
        if response.status_code == 429:
            _logger.error('OpenAI rate limit exceeded: %s', response.text)
            raise attach_retry_after(UserError(_('OpenAI rate limit exceeded. Please try again later.')), response)
        if response.status_code >= 400:
            try:
                err_msg = response.json().get('error', {}).get('message', response.text)
            except ValueError:
                err_msg = response.text
            _logger.error('OpenAI call failed (%s): %s', response.status_code, err_msg)
            raise UserError(_('OpenAI API error: %s') % err_msg)

        return self.parse_response(response.json())

    def get_available_models(self, agent) -> list:
        """
        Fetch available GPT models from OpenAI via a direct HTTPS call.

        Args:
            agent: mcp.agent record (for auth)

        Returns:
            list: Available model IDs
        """
        try:
            response = requests.get(
                _OPENAI_MODELS_URL,
                headers={'Authorization': f'Bearer {agent._decrypt_api_key()}'},
                timeout=10,
            )
            response.raise_for_status()
            models = response.json().get('data', [])
            return [m['id'] for m in models if 'gpt' in m.get('id', '').lower()]
        except Exception as e:
            _logger.warning('Failed to fetch OpenAI models: %s', str(e))
            # Source: developers.openai.com/api/docs/models (2026-07-01)
            return [
                'gpt-5.5',
                'gpt-5.4',
                'gpt-5.4-mini',
                'gpt-5.4-nano',
                'gpt-4o',
                'gpt-4o-mini',
            ]