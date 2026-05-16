"""
mcp_gateway/mcp/providers/gemini.py

Google Gemini API adapter using official google-generativeai Python SDK.

Key classes:
  GeminiAdapter — Adapter for Google's Gemini models

Dependencies:
  - google-generativeai package (pip install google-generativeai)
  - base.AbstractProvider

Developer notes:
  - Uses official Google Generative AI SDK for reliable API calls
  - SDK handles authentication and error handling
  - Tool format: tools array with functionDeclarations
  - Models: gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-flash
"""

import logging
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class GeminiAdapter(AbstractProvider):
    """
    Google Gemini API adapter using official SDK.

    Implements provider interface for Google's Gemini models.
    Supports function calling via functionDeclarations.

    Example:
        agent = env['mcp.agent'].search([('provider','=','gemini')], limit=1)
        provider = GeminiAdapter(env)
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
        Build request for Gemini API using SDK format.

        Converts generic messages/tools to Gemini SDK format.

        Args:
            messages: Message history
            tool_specs: Tool definitions
            agent: mcp.agent with LLM parameters

        Returns:
            dict: Request payload for SDK
        """
        # Build contents array (messages)
        contents = []
        for msg in messages:
            if msg['role'] == 'system':
                continue  # System prompt handled separately
            role = 'user' if msg['role'] == 'user' else 'model' if msg['role'] == 'assistant' else 'user'
            contents.append({
                'role': role,
                'parts': [{'text': msg['content']}],
            })

        # Build tools array in Gemini format (functionDeclarations)
        tools = None
        if tool_specs:
            from google.generativeai import protos
            tools = protos.Tool(function_declarations=[
                {
                    'name': spec['name'],
                    'description': spec['description'],
                    'parameters': spec.get('input_schema', {}),
                }
                for spec in tool_specs
            ])

        # Extract system message from messages parameter (may include datetime injection)
        system_message = ''
        for msg in messages:
            if msg.get('role') == 'system':
                system_message = msg.get('content', '')
                break

        return {
            'contents': contents,
            'generation_config': {
                'max_output_tokens': agent.max_tokens,
                'temperature': agent.temperature,
                'top_p': agent.top_p,
            },
            'system_instruction': {'parts': [{'text': system_message or agent.system_prompt}]} if (system_message or agent.system_prompt) else None,
            'tools': [tools] if tools else None,
        }

    def parse_response(self, raw_json: dict) -> dict:
        """
        Parse Gemini SDK response.

        Extracts text, tool calls, and token usage from response.

        Args:
            raw_json: Full response from SDK

        Returns:
            dict: Standardized response with text, tool_calls, tokens
        """
        try:
            text = None
            tool_calls = []

            candidates = raw_json.get('candidates', [])
            if candidates:
                candidate = candidates[0]
                stop_reason = candidate.get('finishReason', 'UNKNOWN')

                content = candidate.get('content', {})
                for part in content.get('parts', []):
                    if 'text' in part:
                        text = part['text']
                    elif 'functionCall' in part:
                        fc = part['functionCall']
                        tool_calls.append({
                            'id': fc.get('name', ''),
                            'name': fc.get('name', ''),
                            'arguments': fc.get('args', {}),
                        })
            else:
                stop_reason = 'UNKNOWN'

            # Token usage
            usage = raw_json.get('usageMetadata', {})
            input_tokens = usage.get('promptTokenCount', 0)
            output_tokens = usage.get('candidatesTokenCount', 0)

            return {
                'text': text,
                'stop_reason': stop_reason,
                'tool_calls': tool_calls,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
            }
        except Exception as e:
            _logger.error('Gemini response parse error: %s', str(e))
            raise UserError(_('Failed to parse Gemini response: %s') % str(e))

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        """
        Make an API call using Google Generative AI SDK.

        Args:
            agent: mcp.agent record
            messages: Message history
            tool_specs: Tool specifications

        Returns:
            dict: Standardized response
        """
        try:
            import google.generativeai as genai

            api_key = agent._decrypt_api_key()
            genai.configure(api_key=api_key)

            # Get the model - system_instruction must be passed to constructor, not generate_content
            model = genai.GenerativeModel(
                model_name=agent.model_name,
                system_instruction=agent.system_prompt or None,
            )

            # Build contents
            contents = []
            for msg in messages:
                if msg['role'] == 'system':
                    continue
                role = 'user' if msg['role'] == 'user' else 'model'
                contents.append({
                    'role': role,
                    'parts': [{'text': msg['content']}],
                })

            # Build generation config
            generation_config = {
                'max_output_tokens': agent.max_tokens,
                'temperature': agent.temperature,
                'top_p': agent.top_p,
            }

            # Handle tools if present
            tools = None
            if tool_specs:
                from google.generativeai import protos
                tools = [protos.Tool(function_declarations=[
                    {
                        'name': spec['name'],
                        'description': spec['description'],
                        'parameters': spec.get('input_schema', {}),
                    }
                    for spec in tool_specs
                ])]

            _logger.info('Calling Gemini SDK with model: %s', agent.model_name)

            # Generate content
            response = model.generate_content(
                contents=contents,
                generation_config=generation_config,
                tools=tools,
            )

            # Parse response
            return self._parse_sdk_response(response)

        except Exception as e:
            error_str = str(e)
            if 'API_KEY' in error_str or 'authentication' in error_str.lower():
                _logger.error('Gemini authentication failed: %s', error_str)
                raise UserError(_('Invalid Google API key. Please check your API key.'))
            elif 'rate_limit' in error_str.lower():
                _logger.error('Gemini rate limit exceeded: %s', error_str)
                raise UserError(_('Google Gemini rate limit exceeded. Please try again later.'))
            else:
                _logger.error('Gemini call failed: %s', error_str)
                raise UserError(_('Google Gemini API error: %s') % error_str)

    def _parse_sdk_response(self, response) -> dict:
        """
        Parse Gemini SDK response object.

        Args:
            response: GenerativeModel response object

        Returns:
            dict: Standardized response
        """
        try:
            text = None
            tool_calls = []

            # Check candidates
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                stop_reason = candidate.finish_reason.name if hasattr(candidate, 'finish_reason') else 'UNKNOWN'

                if hasattr(candidate, 'content') and candidate.content:
                    content = candidate.content
                    if hasattr(content, 'parts') and content.parts:
                        for part in content.parts:
                            if hasattr(part, 'text') and part.text:
                                text = part.text
                            if hasattr(part, 'function_call') and part.function_call:
                                fc = part.function_call
                                tool_calls.append({
                                    'id': fc.name,
                                    'name': fc.name,
                                    'arguments': dict(fc.args) if hasattr(fc, 'args') else {},
                                })
            else:
                stop_reason = 'UNKNOWN'

            # Get usage
            usage = {}
            if hasattr(response, 'usage_metadata'):
                usage = response.usage_metadata
            input_tokens = usage.get('prompt_token_count', 0) if usage else 0
            output_tokens = usage.get('candidates_token_count', 0) if usage else 0

            return {
                'text': text,
                'stop_reason': stop_reason,
                'tool_calls': tool_calls,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
            }
        except Exception as e:
            _logger.error('Gemini SDK response parse error: %s', str(e))
            raise UserError(_('Failed to parse Gemini response: %s') % str(e))

    def get_available_models(self, agent) -> list:
        """
        Fetch available Gemini models from Google.

        Returns known latest models.

        Args:
            agent: mcp.agent record (for consistency)

        Returns:
            list: Available model IDs
        """
        try:
            import google.generativeai as genai

            api_key = agent._decrypt_api_key()
            genai.configure(api_key=api_key)

            # List available models
            models = genai.list_models()
            return [m.name.replace('models/', '') for m in models
                    if 'gemini' in m.name.lower() and 'preview' not in m.name.lower()]
        except Exception as e:
            _logger.warning('Failed to fetch Gemini models: %s', str(e))
            return [
                'gemini-2.0-flash-exp',
                'gemini-2.0-flash',
                'gemini-1.5-flash',
                'gemini-1.5-pro',
            ]