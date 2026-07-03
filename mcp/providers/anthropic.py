"""
mcp_gateway/mcp/providers/anthropic.py

Anthropic Claude API adapter using official anthropic Python SDK.

Key classes:
  AnthropicAdapter — Adapter for Anthropic's Claude models

Dependencies:
  - anthropic package (pip install anthropic)
  - base.AbstractProvider

Developer notes:
  - Uses official Anthropic SDK for reliable API calls
  - SDK handles authentication and error handling
  - Tool format: Anthropic tool_use blocks in content array
  - Models: claude-opus, claude-sonnet-4-6, claude-haiku
"""

import logging
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AnthropicAdapter(AbstractProvider):
    """
    Anthropic Claude API adapter using official SDK.

    Implements provider interface for Anthropic's Claude models.
    Supports tool_use blocks for function calling.

    Example:
        agent = env['mcp.agent'].search([('provider','=','anthropic')], limit=1)
        provider = AnthropicAdapter(env)
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
        Build request for Anthropic API using SDK format.

        Converts generic messages/tools to Anthropic SDK format.

        Args:
            messages: Message history
            tool_specs: Tool definitions
            agent: mcp.agent with LLM parameters

        Returns:
            dict: Request payload for SDK
        """
        # Build tools array in Anthropic format
        tools = []
        for spec in tool_specs:
            tools.append({
                'name': spec['name'],
                'description': spec['description'],
                'input_schema': spec.get('input_schema', {}),
            })

        # Build messages - filter out system prompt (handled separately by SDK)
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

        return {
            'model': agent.model_name,
            'max_tokens': agent.max_tokens,
            'temperature': agent.temperature,
            'top_p': agent.top_p,
            'system': system_message or agent.system_prompt or '',
            'messages': sdk_messages,
            'tools': tools if tools else None,
        }

    def parse_response(self, raw_json: dict) -> dict:
        """
        Parse Anthropic SDK response.

        Extracts text, tool calls, and token usage from response.

        Args:
            raw_json: Full response from SDK

        Returns:
            dict: Standardized response with text, tool_calls, tokens
        """
        try:
            text = None
            tool_calls = []
            stop_reason = raw_json.get('stop_reason', 'unknown')

            # Extract text and tool calls from content array
            for content in raw_json.get('content', []):
                if hasattr(content, 'type'):
                    if content.type == 'text':
                        text = content.text
                    elif content.type == 'tool_use':
                        tool_calls.append({
                            'id': content.id,
                            'name': content.name,
                            'arguments': content.input if hasattr(content, 'input') else {},
                        })
                elif isinstance(content, dict):
                    if content.get('type') == 'text':
                        text = content.get('text')
                    elif content.get('type') == 'tool_use':
                        tool_calls.append({
                            'id': content.get('id'),
                            'name': content.get('name'),
                            'arguments': content.get('input', {}),
                        })

            # Get usage
            usage = raw_json.get('usage', {})
            input_tokens = usage.get('input_tokens', 0) if hasattr(usage, 'get') else 0
            output_tokens = usage.get('output_tokens', 0) if hasattr(usage, 'get') else 0

            return {
                'text': text,
                'stop_reason': stop_reason,
                'tool_calls': tool_calls,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
            }
        except Exception as e:
            _logger.error('Anthropic response parse error: %s', str(e))
            raise UserError(_('Failed to parse Anthropic response: %s') % str(e))

    def format_tool_calls(self, tool_calls: list) -> list:
        """OpenAI-canonical shape — gateway.py's normalize_history_for_format() converts
        this into native tool_use blocks before build_payload() runs. Returning [] here
        (previous behavior) meant that conversion had nothing to convert, silently
        dropping the assistant's tool_use block from history and breaking any
        multi-tool-call conversation."""
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
        """Anthropic tool result: user message with tool_result content block."""
        return {
            'role': 'user',
            'content': [{
                'type': 'tool_result',
                'tool_use_id': tool_call_id,
                'content': result,
            }],
        }

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        """
        Make an API call using Anthropic SDK.

        Args:
            agent: mcp.agent record
            messages: Message history
            tool_specs: Tool specifications

        Returns:
            dict: Standardized response
        """
        try:
            import anthropic

            api_key = agent._decrypt_api_key()
            client = anthropic.Anthropic(api_key=api_key)

            payload = self.build_payload(messages, tool_specs, agent)

            _logger.info('Calling Anthropic SDK with model: %s', payload.get('model'))

            response = client.messages.create(**payload)

            resp_dict = response.model_dump() if hasattr(response, 'model_dump') else response
            return self.parse_response(resp_dict)

        except anthropic.AuthenticationError as e:
            _logger.error('Anthropic authentication failed: %s', str(e))
            raise UserError(_('Invalid Anthropic API key. Please check your API key.'))
        except anthropic.RateLimitError as e:
            _logger.error('Anthropic rate limit exceeded: %s', str(e))
            raise UserError(_('Anthropic rate limit exceeded. Please try again later.'))
        except anthropic.APIConnectionError as e:
            _logger.error('Anthropic connection error: %s', str(e))
            raise UserError(_('Failed to connect to Anthropic API. Check your internet connection.'))
        except Exception as e:
            _logger.error('Anthropic call failed: %s', str(e))
            raise UserError(_('Anthropic API error: %s') % str(e))

    def get_available_models(self, agent) -> list:
        """
        Fetch available Claude models from Anthropic.

        Returns known latest models.

        Args:
            agent: mcp.agent record (for consistency)

        Returns:
            list: Available model IDs
        """
        # Anthropic SDK doesn't have a models list endpoint, return known models
        # Source: platform.claude.com/docs/en/docs/about-claude/models/all-models (2026-07-01)
        return [
            'claude-fable-5',
            'claude-opus-4-8',
            'claude-sonnet-5',
            'claude-haiku-4-5',
            'claude-opus-4-7',
            'claude-opus-4-6',
            'claude-sonnet-4-6',
            'claude-sonnet-4-5',
        ]