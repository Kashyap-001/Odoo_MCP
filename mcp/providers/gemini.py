"""
mcp_gateway/mcp/providers/gemini.py

Google Gemini API adapter — raw HTTPS via requests, no vendor SDK.

Dependencies:
  - requests (already a hard Odoo dependency — no extra pip install needed)

Developer notes:
  - Talks directly to generativelanguage.googleapis.com's generateContent REST
    endpoint. Auth via the x-goog-api-key header (no ?key= query param, keeps
    the key out of server access logs).
  - Tool format: {"tools": [{"functionDeclarations": [...]}]}
  - Tool results: {"role": "user", "parts": [{"functionResponse": {...}}]}
  - The SDK's "automatic function calling" concept (which this project always
    disabled anyway) doesn't exist at the REST layer — the API never executes
    functions itself, so there's nothing to disable here.
  - Models: gemini-2.5-flash (free tier), gemini-2.5-pro, gemini-3.5-flash
"""

import logging
import json
import requests
from .base import AbstractProvider, attach_retry_after
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'


class GeminiAdapter(AbstractProvider):

    def build_headers(self, agent) -> dict:
        return {
            'x-goog-api-key': agent._decrypt_api_key(),
            'Content-Type': 'application/json',
        }

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        contents = self._build_contents(messages)

        system_message = next(
            (m.get('content', '') for m in messages if m.get('role') == 'system'), ''
        ) or agent.system_prompt or ''

        payload = {
            'contents': contents,
            'generationConfig': {
                'maxOutputTokens': agent.max_tokens,
                'temperature': agent.temperature,
                'topP': agent.top_p,
            },
        }
        if system_message:
            payload['systemInstruction'] = {'parts': [{'text': system_message}]}
        if tool_specs:
            payload['tools'] = [{'functionDeclarations': [
                {
                    'name': spec['name'],
                    'description': spec['description'],
                    'parameters': spec.get('input_schema', {}),
                }
                for spec in tool_specs
            ]}]
        return payload

    def _build_contents(self, messages: list) -> list:
        """Convert gateway messages to Gemini REST API 'contents' list (plain dicts)."""
        contents = []
        for msg in messages:
            role = msg.get('role')
            if role == 'system':
                continue

            if role in ('user', 'assistant'):
                gemini_role = 'user' if role == 'user' else 'model'
                content_val = msg.get('content') or ''

                tool_calls = msg.get('tool_calls', [])
                if tool_calls:
                    parts = []
                    if content_val:
                        parts.append({'text': content_val})
                    for tc in tool_calls:
                        parts.append({'functionCall': {
                            'name': tc['name'],
                            'args': tc.get('arguments', {}),
                        }})
                    contents.append({'role': gemini_role, 'parts': parts})
                else:
                    contents.append({'role': gemini_role, 'parts': [{'text': content_val or ' '}]})

            elif role == 'tool':
                tool_name = msg.get('name') or msg.get('tool_call_id', 'tool')
                result_content = msg.get('content', '')
                try:
                    result_data = json.loads(result_content) if isinstance(result_content, str) else result_content
                except Exception:
                    result_data = {'result': result_content}
                contents.append({'role': 'user', 'parts': [{'functionResponse': {
                    'name': tool_name,
                    'response': result_data,
                }}]})

        return contents

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        """
        Make a direct HTTPS call to the Gemini generateContent REST endpoint.

        Args:
            agent: mcp.agent record
            messages: Message history
            tool_specs: Tool specifications

        Returns:
            dict: Standardized response
        """
        headers = self.build_headers(agent)
        payload = self.build_payload(messages, tool_specs, agent)
        url = f'{_GEMINI_BASE_URL}/models/{agent.model_name}:generateContent'

        _logger.info('Calling Gemini API with model: %s', agent.model_name)

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.exceptions.ConnectionError as e:
            _logger.error('Gemini connection error: %s', str(e))
            raise UserError(_('Failed to connect to Google Gemini API. Check your internet connection.'))
        except requests.exceptions.Timeout as e:
            _logger.error('Gemini request timed out: %s', str(e))
            raise UserError(_('Google Gemini API request timed out.'))

        if response.status_code in (401, 403):
            _logger.error('Gemini authentication failed: %s', response.text)
            raise UserError(_('Invalid Google API key. Get one at aistudio.google.com'))
        if response.status_code == 429:
            _logger.error('Gemini quota exceeded: %s', response.text)
            raise attach_retry_after(UserError(_('Google Gemini quota exceeded. Try again later or upgrade plan.')), response)
        if response.status_code >= 400:
            try:
                err_msg = response.json().get('error', {}).get('message', response.text)
            except ValueError:
                err_msg = response.text
            _logger.error('Gemini call failed (%s): %s', response.status_code, err_msg)
            raise UserError(_('Google Gemini API error: %s') % err_msg)

        return self.parse_response(response.json())

    def parse_response(self, raw_json: dict) -> dict:
        """Parse a raw Gemini generateContent REST JSON response."""
        try:
            text = None
            tool_calls = []
            stop_reason = 'UNKNOWN'

            candidates = raw_json.get('candidates', [])
            if candidates:
                candidate = candidates[0]
                stop_reason = candidate.get('finishReason', 'UNKNOWN')
                for part in candidate.get('content', {}).get('parts', []):
                    if part.get('text'):
                        text = part['text']
                    if part.get('functionCall'):
                        fc = part['functionCall']
                        tool_calls.append({
                            'id': fc.get('name'),
                            'name': fc.get('name'),
                            'arguments': fc.get('args') or {},
                        })

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

    def get_available_models(self, agent) -> list:
        try:
            response = requests.get(
                f'{_GEMINI_BASE_URL}/models',
                headers={'x-goog-api-key': agent._decrypt_api_key()},
                timeout=10,
            )
            response.raise_for_status()
            models = response.json().get('models', [])
            return [
                m['name'].replace('models/', '')
                for m in models
                if 'gemini' in m.get('name', '').lower()
                and 'preview' not in m.get('name', '').lower()
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
