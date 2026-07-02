"""
mcp_gateway/mcp/providers/gemini.py

Google Gemini API adapter using the NEW google-genai Python SDK.

Dependencies:
  - google-genai package (pip install google-genai)
  - NOT google-generativeai (deprecated)

Developer notes:
  - Client: genai.Client(api_key=...)
  - Call: client.models.generate_content(model=..., contents=..., config=...)
  - Tool format: types.Tool(function_declarations=[types.FunctionDeclaration(...)])
  - Tool results: Content(role='user', parts=[Part(function_response=FunctionResponse(...))])
  - Models: gemini-2.5-flash (free tier), gemini-2.5-pro, gemini-3.5-flash
"""

import logging
import json
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class GeminiAdapter(AbstractProvider):

    def build_headers(self, agent) -> dict:
        return {'Content-Type': 'application/json'}

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        # Not used — call() builds directly with SDK types
        return {}

    def _build_contents(self, messages: list):
        """Convert gateway messages to google-genai Contents list."""
        from google.genai import types

        contents = []
        for msg in messages:
            role = msg.get('role')
            if role == 'system':
                continue

            if role in ('user', 'assistant'):
                sdk_role = 'user' if role == 'user' else 'model'
                content_val = msg.get('content') or ''

                # Assistant message may carry tool_calls
                tool_calls = msg.get('tool_calls', [])
                if tool_calls:
                    parts = []
                    if content_val:
                        parts.append(types.Part(text=content_val))
                    for tc in tool_calls:
                        parts.append(types.Part(
                            function_call=types.FunctionCall(
                                name=tc['name'],
                                args=tc.get('arguments', {}),
                            )
                        ))
                    contents.append(types.Content(role=sdk_role, parts=parts))
                else:
                    contents.append(types.Content(
                        role=sdk_role,
                        parts=[types.Part(text=content_val or ' ')],
                    ))

            elif role == 'tool':
                # Tool result — Gemini expects role='user' with function_response
                tool_name = msg.get('name') or msg.get('tool_call_id', 'tool')
                result_content = msg.get('content', '')
                try:
                    result_data = json.loads(result_content) if isinstance(result_content, str) else result_content
                except Exception:
                    result_data = {'result': result_content}
                contents.append(types.Content(
                    role='user',
                    parts=[types.Part(
                        function_response=types.FunctionResponse(
                            name=tool_name,
                            response=result_data,
                        )
                    )]
                ))

        return contents

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        try:
            from google import genai
            from google.genai import types

            api_key = agent._decrypt_api_key()
            client = genai.Client(api_key=api_key)

            contents = self._build_contents(messages)

            # System instruction
            system_message = next(
                (m.get('content', '') for m in messages if m.get('role') == 'system'), ''
            ) or agent.system_prompt or ''

            # Tools
            tools = None
            if tool_specs:
                tools = [types.Tool(function_declarations=[
                    types.FunctionDeclaration(
                        name=spec['name'],
                        description=spec['description'],
                        parameters=spec.get('input_schema', {}),
                    )
                    for spec in tool_specs
                ])]

            config = types.GenerateContentConfig(
                system_instruction=system_message or None,
                max_output_tokens=agent.max_tokens,
                temperature=agent.temperature,
                top_p=agent.top_p,
                tools=tools,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            _logger.info('Calling Gemini (google-genai) with model: %s', agent.model_name)

            response = client.models.generate_content(
                model=agent.model_name,
                contents=contents,
                config=config,
            )

            return self.parse_response(response)

        except Exception as e:
            error_str = str(e)
            if 'api_key' in error_str.lower() or 'api key' in error_str.lower() or '401' in error_str or 'INVALID_ARGUMENT' in error_str:
                raise UserError(_('Invalid Google API key. Get one at aistudio.google.com'))
            elif 'quota' in error_str.lower() or '429' in error_str:
                raise UserError(_('Google Gemini quota exceeded. Try again later or upgrade plan.'))
            else:
                _logger.error('Gemini call failed: %s', error_str)
                raise UserError(_('Google Gemini API error: %s') % error_str)

    def parse_response(self, response) -> dict:
        # Overrides AbstractProvider.parse_response — required or GeminiAdapter
        # remains abstract and can't be instantiated at all (was silently the
        # case here: `response` is the raw google-genai SDK object, not the
        # `raw_json: dict` the base class docstring describes, since this
        # provider calls the SDK directly instead of build_payload+requests).
        try:
            text = None
            tool_calls = []
            stop_reason = 'UNKNOWN'

            if response.candidates:
                candidate = response.candidates[0]
                stop_reason = candidate.finish_reason.name if hasattr(candidate, 'finish_reason') else 'UNKNOWN'

                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text') and part.text:
                            text = part.text
                        if hasattr(part, 'function_call') and part.function_call:
                            fc = part.function_call
                            tool_calls.append({
                                'id': fc.name,
                                'name': fc.name,
                                'arguments': dict(fc.args) if fc.args else {},
                            })

            # Also check top-level function_calls shortcut
            if not tool_calls and hasattr(response, 'function_calls') and response.function_calls:
                for fc in response.function_calls:
                    tool_calls.append({
                        'id': fc.name,
                        'name': fc.name,
                        'arguments': dict(fc.args) if fc.args else {},
                    })

            usage = response.usage_metadata
            input_tokens = getattr(usage, 'prompt_token_count', 0) if usage else 0
            output_tokens = getattr(usage, 'candidates_token_count', 0) if usage else 0

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

    def get_available_models(self, agent) -> list:
        try:
            from google import genai

            api_key = agent._decrypt_api_key()
            client = genai.Client(api_key=api_key)

            models = client.models.list()
            return [
                m.name.replace('models/', '')
                for m in models
                if 'gemini' in (m.name or '').lower()
                and 'preview' not in (m.name or '').lower()
            ]
        except Exception as e:
            _logger.warning('Failed to fetch Gemini models: %s', str(e))
            # Source: ai.google.dev/gemini-api/docs/models (2026-07-01)
            # gemini-2.5-flash and gemini-2.5-flash-lite have free API tier
            return [
                'gemini-3.5-flash',
                'gemini-2.5-pro',
                'gemini-2.5-flash',
                'gemini-2.5-flash-lite',
                'gemini-3.1-flash-lite',
            ]

    def format_tool_calls(self, tool_calls: list) -> list:
        """Gemini history: tool_calls as list of {name, arguments: dict}."""
        import json as _json
        return [
            {
                'name': tc.get('name', ''),
                'arguments': (
                    tc.get('arguments')
                    if isinstance(tc.get('arguments'), dict)
                    else _json.loads(tc.get('arguments') or '{}')
                ),
            }
            for tc in tool_calls
        ]

    def format_tool_result(self, tool_call_id: str, tool_name: str, result: str) -> dict:
        """Gemini tool result: _build_contents converts role=tool + name to functionResponse Part."""
        return {'role': 'tool', 'name': tool_name, 'tool_call_id': tool_call_id, 'content': result}
