"""
mcp_gateway/mcp/providers/minimax.py

MiniMax AI API adapter using OpenAI SDK (OpenAI-compatible).

Key classes:
  MiniMaxAdapter — Adapter for MiniMax models

Dependencies:
  - openai package (pip install openai)
  - base.AbstractProvider

Developer notes:
  - MiniMax uses OpenAI-compatible API format
  - Base URL: https://api.minimax.chat/v1
  - Uses official openai SDK for reliable API calls
"""

import logging
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MiniMaxAdapter(AbstractProvider):
    """
    MiniMax AI API adapter using OpenAI SDK.

    Implements provider interface for MiniMax models.
    Uses OpenAI-compatible API format via openai Python SDK.

    Example:
        agent = env['mcp.agent'].search([('provider','=','minimax')], limit=1)
        provider = MiniMaxAdapter(env)
        result = provider.call(agent, messages, tool_specs)
    """

    DEFAULT_BASE_URL = 'https://api.minimax.chat/v1'
    DEFAULT_ENDPOINT = '/text/chatcompletion_v2'

    def build_headers(self, agent) -> dict:
        """
        Build MiniMax API headers - not needed with SDK.

        Args:
            agent: mcp.agent record

        Returns:
            dict: Minimal headers
        """
        return {'Content-Type': 'application/json'}

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        """
        Build request payload for MiniMax API using OpenAI SDK format.

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

        payload = {
            'model': agent.model_name or 'abab6.5s-chat',
            'messages': messages,
            'max_tokens': agent.max_tokens,
            'temperature': agent.temperature,
        }
        if functions:
            payload['tools'] = functions
        return payload

    def parse_response(self, raw_json: dict) -> dict:
        """
        Parse MiniMax SDK response to standard format.

        Args:
            raw_json: Full response from SDK

        Returns:
            dict: Standardized response with text, tool_calls, tokens
        """
        try:
            choice = raw_json.get('choices', [{}])[0]
            message = choice.get('message', {})
            text = message.get('content') or ''
            stop_reason = choice.get('finish_reason', 'unknown')

            # Extract tool calls
            tool_calls = []
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
            _logger.error('MiniMax response parse error: %s', str(e))
            raise UserError(_('Failed to parse MiniMax response: %s') % str(e))

    def call(self, agent, messages: list, tool_specs: list = None) -> dict:
        """
        Call MiniMax API using httpx with OpenAI-compatible format.

        Args:
            agent: mcp.agent record
            messages: List of message dicts
            tool_specs: Optional list of tool specifications

        Returns:
            dict: Standardized response

        Raises:
            UserError: on API errors
        """
        if tool_specs is None:
            tool_specs = []

        try:
            from openai import OpenAI

            api_key = agent._decrypt_api_key()
            # MiniMax uses OpenAI-compatible API, but with custom endpoint
            base_url = (agent.api_base_url or self.DEFAULT_BASE_URL).rstrip('/')

            # Use OpenAI SDK - it handles auth properly
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=60.0,
                max_retries=0,
            )

            payload = self.build_payload(messages, tool_specs, agent)

            _logger.info('Calling MiniMax via OpenAI SDK with model: %s', payload.get('model'))

            # OpenAI SDK will call /v1/chat/completions by default
            # But MiniMax needs /text/chatcompletion_v2 - need custom request
            # Use SDK's client directly for custom endpoint

            # Build request similar to SDK but with MiniMax endpoint
            import httpx

            url = f"{base_url}/text/chatcompletion_v2"
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            }

            _logger.info('MiniMax request to: %s', url)

            with httpx.Client(timeout=60.0) as http_client:
                response = http_client.post(url, headers=headers, json=payload)

            _logger.info('MiniMax response: status=%s, body=%s', response.status_code, response.text[:300])

            if response.status_code == 401:
                raise UserError(_('Invalid MiniMax API key. Please check your API key.'))

            response.raise_for_status()
            response_data = response.json()

            # Check MiniMax-specific error in response
            base_resp = response_data.get('base_resp', {})
            if base_resp.get('status_code') and base_resp.get('status_code') != 0:
                raise UserError(_('MiniMax API error: %s') % base_resp.get('status_msg', 'Unknown error'))

            return self.parse_response(response_data)

        except httpx.HTTPStatusError as e:
            _logger.error('MiniMax HTTP error: %s', str(e))
            raise UserError(_('MiniMax API error: %s') % str(e))
        except Exception as e:
            error_str = str(e)
            if 'authentication' in error_str.lower() or 'api key' in error_str.lower():
                _logger.error('MiniMax authentication failed: %s', error_str)
                raise UserError(_('Invalid MiniMax API key. Please check your API key.'))
            elif 'rate_limit' in error_str.lower():
                _logger.error('MiniMax rate limit exceeded: %s', error_str)
                raise UserError(_('MiniMax rate limit exceeded. Please try again later.'))
            else:
                _logger.error('MiniMax call failed: %s', error_str)
                raise UserError(_('MiniMax API error: %s') % error_str)

    def get_available_models(self, agent) -> list:
        """
        Get available MiniMax models.

        Args:
            agent: mcp.agent record

        Returns:
            list: Available model IDs
        """
        try:
            import httpx

            api_key = agent._decrypt_api_key()
            base_url = (agent.api_base_url or self.DEFAULT_BASE_URL).rstrip('/')
            endpoint = agent.api_base_url and '' or self.DEFAULT_ENDPOINT

            url = f"{base_url}{endpoint}"

            payload = {
                'model': 'abab6.5s-chat',
                'messages': [{'role': 'user', 'content': 'Hi'}],
                'max_tokens': 1,
            }

            # Try multiple auth formats
            auth_formats = [
                {'Authorization': f'Bearer {api_key}'},
                {'Authorization': api_key},
                {'x-api-key': api_key},
            ]

            for auth_headers in auth_formats:
                headers = {**auth_headers, 'Content-Type': 'application/json'}
                _logger.info('get_available_models: trying auth format: %s', list(auth_headers.keys()))

                try:
                    with httpx.Client(timeout=10.0) as client:
                        response = client.post(url, headers=headers, json=payload)

                    if response.status_code == 401:
                        continue

                    response.raise_for_status()
                    response_data = response.json()

                    # Check for error in response
                    base_resp = response_data.get('base_resp', {})
                    if base_resp.get('status_code') and base_resp.get('status_code') != 0:
                        _logger.info('Auth failed: %s', base_resp.get('status_msg'))
                        continue

                    # Success!
                    return [
                        'abab6.5s-chat',
                        'abab6.5g-chat',
                        'abab5.5s-chat',
                        'abab5.5g-chat',
                        'abab4-chat',
                    ]
                except Exception as e:
                    _logger.info('Auth format failed: %s', str(e))
                    continue

        except Exception as e:
            _logger.warning('MiniMax connection test failed: %s', str(e))

        return [
            'abab6.5s-chat',
            'abab6.5g-chat',
            'abab5.5s-chat',
        ]