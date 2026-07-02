"""
mcp_gateway/mcp/providers/openai.py

OpenAI GPT API adapter using official openai Python SDK.

Key classes:
  OpenAIAdapter — Adapter for OpenAI's GPT-4, GPT-4 Turbo, GPT-4o models

Dependencies:
  - openai package (pip install openai)
  - base.AbstractProvider

Developer notes:
  - Uses official OpenAI SDK for reliable API calls
  - SDK handles authentication and error handling
  - Tool format: OpenAI tools array with function objects
  - Models: gpt-4, gpt-4-turbo, gpt-4o
"""

import logging
import json
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


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
        Build headers - not needed with SDK as it handles auth internally.

        Args:
            agent: mcp.agent record

        Returns:
            dict: Minimal headers
        """
        return {
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
        Make an API call using OpenAI SDK.

        Args:
            agent: mcp.agent record
            messages: Message history
            tool_specs: Tool specifications

        Returns:
            dict: Standardized response
        """
        try:
            from openai import OpenAI

            api_key = agent._decrypt_api_key()
            client = OpenAI(api_key=api_key)

            payload = self.build_payload(messages, tool_specs, agent)

            _logger.info('Calling OpenAI SDK with model: %s', payload.get('model'))

            response = client.chat.completions.create(**payload)

            # model_dump() works since response is SDK object; fallback for dict
            resp_dict = response.model_dump() if hasattr(response, 'model_dump') else response
            return self.parse_response(resp_dict)

        except Exception as e:
            error_str = str(e)
            if 'authentication' in error_str.lower() or 'api key' in error_str.lower():
                _logger.error('OpenAI authentication failed: %s', error_str)
                raise UserError(_('Invalid OpenAI API key. Please check your API key.'))
            elif 'rate_limit' in error_str.lower():
                _logger.error('OpenAI rate limit exceeded: %s', error_str)
                raise UserError(_('OpenAI rate limit exceeded. Please try again later.'))
            else:
                _logger.error('OpenAI call failed: %s', error_str)
                raise UserError(_('OpenAI API error: %s') % error_str)

    def get_available_models(self, agent) -> list:
        """
        Fetch available GPT models from OpenAI.

        Uses SDK to list models.

        Args:
            agent: mcp.agent record (for consistency)

        Returns:
            list: Available model IDs
        """
        try:
            from openai import OpenAI

            api_key = agent._decrypt_api_key()
            client = OpenAI(api_key=api_key)

            models = client.models.list()
            return [m.id for m in models.data if 'gpt' in m.id.lower()]
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