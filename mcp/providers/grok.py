import logging
import json
import requests
from .base import AbstractProvider, attach_retry_after
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_GROK_BASE_URL = 'https://api.x.ai/v1'
_GROK_FALLBACK_MODELS = ['grok-4.3', 'grok-4.20-0309-reasoning', 'grok-4.20-0309-non-reasoning', 'grok-build-0.1']


class GrokAdapter(AbstractProvider):
    """Grok (xAI) adapter — OpenAI-compatible REST API at api.x.ai/v1, called
    directly via requests (no vendor SDK needed for an OpenAI-compatible endpoint)."""

    def build_headers(self, agent) -> dict:
        return {
            'Authorization': f'Bearer {agent._decrypt_api_key()}',
            'Content-Type': 'application/json',
        }

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        functions = [
            {
                'name': spec['name'],
                'description': spec['description'],
                'parameters': spec.get('input_schema', {}),
            }
            for spec in tool_specs
        ]

        system_message = next((m.get('content', '') for m in messages if m.get('role') == 'system'), '')
        sdk_messages = [m for m in messages if m['role'] != 'system']

        payload_messages = sdk_messages
        if sdk_messages and sdk_messages[0]['role'] != 'system' and (system_message or agent.system_prompt):
            payload_messages = [{'role': 'system', 'content': system_message or agent.system_prompt}] + sdk_messages

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
        try:
            choice = raw_json.get('choices', [{}])[0]
            message = choice.get('message', {})
            text = message.get('content')
            stop_reason = choice.get('finish_reason', 'unknown')
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
            _logger.error('Grok response parse error: %s', str(e))
            raise UserError(_('Failed to parse Grok response: %s') % str(e))

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        headers = self.build_headers(agent)
        payload = self.build_payload(messages, tool_specs, agent)
        base_url = agent.api_base_url or _GROK_BASE_URL
        _logger.info('Calling Grok API with model: %s', payload.get('model'))

        try:
            response = requests.post(f'{base_url}/chat/completions', headers=headers, json=payload, timeout=60)
        except requests.exceptions.ConnectionError as e:
            _logger.error('Grok connection error: %s', str(e))
            raise UserError(_('Failed to connect to Grok API. Check your internet connection.'))
        except requests.exceptions.Timeout as e:
            _logger.error('Grok request timed out: %s', str(e))
            raise UserError(_('Grok API request timed out.'))

        if response.status_code == 401:
            raise UserError(_('Invalid Grok API key. Please check your API key at console.x.ai.'))
        if response.status_code == 429:
            raise attach_retry_after(UserError(_('Grok rate limit exceeded. Please try again later.')), response)
        if response.status_code == 400:
            raise UserError(_('Grok API rejected the request (400). Check your model name — "%s" may not be available on your plan. Try grok-2 or grok-beta.') % agent.model_name)
        if response.status_code >= 400:
            try:
                err_msg = response.json().get('error', {}).get('message', response.text)
            except ValueError:
                err_msg = response.text
            _logger.error('Grok call failed (%s): %s', response.status_code, err_msg)
            raise UserError(_('Grok API error: %s') % err_msg)

        return self.parse_response(response.json())

    def get_available_models(self, agent) -> list:
        base_url = agent.api_base_url or _GROK_BASE_URL
        try:
            response = requests.get(
                f'{base_url}/models',
                headers={'Authorization': f'Bearer {agent._decrypt_api_key()}'},
                timeout=10,
            )
            response.raise_for_status()
            models = response.json().get('data', [])
            grok_models = [m['id'] for m in models if 'grok' in m.get('id', '').lower()]
            return grok_models if grok_models else _GROK_FALLBACK_MODELS
        except Exception as e:
            _logger.warning('Failed to fetch Grok models: %s', str(e))
            return _GROK_FALLBACK_MODELS

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
