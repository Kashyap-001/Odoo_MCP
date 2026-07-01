import logging
import json
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_GROK_BASE_URL = 'https://api.x.ai/v1'
_GROK_FALLBACK_MODELS = ['grok-3', 'grok-3-mini', 'grok-2', 'grok-beta']


class GrokAdapter(AbstractProvider):
    """Grok (xAI) adapter — OpenAI-compatible API at api.x.ai/v1."""

    def build_headers(self, agent) -> dict:
        return {'Content-Type': 'application/json'}

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
        try:
            from openai import OpenAI

            api_key = agent._decrypt_api_key()
            base_url = agent.api_base_url or _GROK_BASE_URL
            client = OpenAI(api_key=api_key, base_url=base_url)

            payload = self.build_payload(messages, tool_specs, agent)
            _logger.info('Calling Grok API with model: %s', payload.get('model'))

            response = client.chat.completions.create(**payload)
            resp_dict = response.model_dump() if hasattr(response, 'model_dump') else response
            return self.parse_response(resp_dict)

        except Exception as e:
            error_str = str(e)
            if 'authentication' in error_str.lower() or 'api key' in error_str.lower() or '401' in error_str:
                raise UserError(_('Invalid Grok API key. Please check your API key at console.x.ai.'))
            elif 'rate_limit' in error_str.lower() or '429' in error_str:
                raise UserError(_('Grok rate limit exceeded. Please try again later.'))
            else:
                _logger.error('Grok call failed: %s', error_str)
                raise UserError(_('Grok API error: %s') % error_str)

    def get_available_models(self, agent) -> list:
        try:
            from openai import OpenAI

            api_key = agent._decrypt_api_key()
            base_url = agent.api_base_url or _GROK_BASE_URL
            client = OpenAI(api_key=api_key, base_url=base_url)

            models = client.models.list()
            grok_models = [m.id for m in models.data if 'grok' in m.id.lower()]
            return grok_models if grok_models else _GROK_FALLBACK_MODELS
        except Exception as e:
            _logger.warning('Failed to fetch Grok models: %s', str(e))
            return _GROK_FALLBACK_MODELS
