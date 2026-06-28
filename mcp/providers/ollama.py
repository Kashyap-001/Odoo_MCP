"""
mcp_gateway/mcp/providers/ollama.py

Ollama local LLM provider adapter.

Key classes:
  OllamaAdapter — Adapter for locally-running Ollama models

Dependencies:
  - base.AbstractProvider
  - requests for HTTP calls

Developer notes:
  - API: {base_url}/api/chat (default http://localhost:11434)
  - No authentication required
  - Format: OpenAI-compatible (Ollama 0.3+)
  - Tool format: Same as OpenAI (tools array with functions)
  - Models fetched from /api/tags endpoint
  - Default model_name is 'llama3' or 'llama2'
"""

import logging
import json
import requests
from .base import AbstractProvider
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OllamaAdapter(AbstractProvider):
    """
    Ollama local LLM provider adapter.

    Implements provider interface for Ollama running locally.
    Ollama provides OpenAI-compatible API for offline model serving.

    Example:
        agent = env['mcp.agent'].search([('provider','=','ollama')], limit=1)
        provider = OllamaAdapter(env)
        result = provider.call(agent, messages, tool_specs)

    Requirements:
        - Ollama running locally: ollama serve
        - Model pulled: ollama pull llama3 (or other model)
        - API accessible at http://localhost:11434 (or custom base URL)
    """

    def build_headers(self, agent) -> dict:
        """
        Build Ollama API headers.

        Ollama doesn't require authentication.

        Args:
            agent: mcp.agent record

        Returns:
            dict: Headers (Content-Type only)
        """
        return {
            'Content-Type': 'application/json',
        }

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        """
        Build request for Ollama API.

        Uses OpenAI-compatible format (Ollama 0.3+).

        Args:
            messages: Message history
            tool_specs: Tool definitions
            agent: mcp.agent with LLM parameters

        Returns:
            dict: Request payload for POST /api/chat
        """
        # Build functions array (OpenAI-compatible for Ollama)
        functions = []
        for spec in tool_specs:
            functions.append({
                'name': spec['name'],
                'description': spec['description'],
                'parameters': spec.get('input_schema', {}),
            })

        # Extract system message from messages parameter (may include datetime injection)
        system_message = ''
        for msg in messages:
            if msg.get('role') == 'system':
                system_message = msg.get('content', '')
                break

        # Insert system prompt as first message if not present
        payload_messages = messages
        if messages and messages[0]['role'] != 'system' and (system_message or agent.system_prompt):
            payload_messages = [
                {'role': 'system', 'content': system_message or agent.system_prompt},
            ] + messages

        payload = {
            'model': agent.model_name,
            'messages': payload_messages,
            'stream': False,  # Ollama supports streaming, but we handle non-streaming for now
            'temperature': agent.temperature,
            'top_p': agent.top_p,
            'num_predict': agent.max_tokens,
        }
        if functions:
            payload['functions'] = functions
        return payload

    def parse_response(self, raw_json: dict) -> dict:
        """
        Parse Ollama API response.

        Uses OpenAI-compatible response format.

        Args:
            raw_json: Full response from POST /api/chat

        Returns:
            dict: Standardized response with text, tool_calls, tokens
        """
        try:
            message = raw_json.get('message', {})
            text = message.get('content')
            stop_reason = 'stop'  # Ollama always stops normally
            tool_calls = []

            # Extract tool calls if present (OpenAI-compatible format)
            function_call = message.get('function_call')
            if function_call:
                tool_calls.append({
                    'id': function_call.get('name', ''),
                    'name': function_call.get('name', ''),
                    'arguments': json.loads(function_call.get('arguments', '{}') or '{}'),
                })

            # Token estimation (Ollama doesn't provide exact counts in all versions)
            prompt_eval_count = raw_json.get('prompt_eval_count', 0)
            eval_count = raw_json.get('eval_count', 0)

            return {
                'text': text,
                'stop_reason': stop_reason,
                'tool_calls': tool_calls,
                'input_tokens': prompt_eval_count,
                'output_tokens': eval_count,
            }
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            _logger.error('Ollama response parse error: %s', str(e))
            raise UserError(_('Failed to parse Ollama response: %s') % str(e))

    def get_available_models(self, agent) -> list:
        """
        Fetch list of models available on local Ollama instance.

        Calls /api/tags endpoint to get all pulled models.

        Args:
            agent: mcp.agent record (for base URL)

        Returns:
            list: Available model names

        Raises:
            UserError: if Ollama is not running or unreachable
        """
        try:
            base_url = agent.api_base_url
            url = f'{base_url}/api/tags'
            response = requests.get(url, timeout=5)
            response.raise_for_status()

            models = response.json().get('models', [])
            return [m['name'] for m in models]
        except requests.exceptions.ConnectionError:
            raise UserError(
                _('Cannot reach Ollama at %s. Ensure Ollama is running: ollama serve')
                % (agent.api_base_url or 'http://localhost:11434')
            )
        except Exception as e:
            raise UserError(_('Failed to fetch Ollama models: %s') % str(e))

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        """
        Make an API call to local Ollama instance.

        Overrides base.call() to point to local endpoint.

        Args:
            agent: mcp.agent record
            messages: Message history
            tool_specs: Tool specifications

        Returns:
            dict: Standardized response
        """
        import time

        try:
            headers = self.build_headers(agent)
            payload = self.build_payload(messages, tool_specs, agent)
            base_url = agent.api_base_url
            url = f'{base_url}/api/chat'

            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    response = requests.post(url, json=payload, headers=headers, timeout=30)
                    response.raise_for_status()
                    return self.parse_response(response.json())
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    if attempt < max_retries:
                        backoff = 2 ** attempt
                        _logger.warning('Ollama attempt %d failed (timeout/connection): retrying in %ds',
                                      attempt + 1, backoff)
                        time.sleep(backoff)
                    else:
                        raise
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code in (500, 502, 503, 429):
                        if attempt < max_retries:
                            backoff = 2 ** attempt
                            _logger.warning('Ollama attempt %d failed (HTTP %d): retrying in %ds',
                                          attempt + 1, e.response.status_code, backoff)
                            time.sleep(backoff)
                        else:
                            raise
                    else:
                        raise
        except Exception as e:
            _logger.error('Ollama call failed: %s', str(e))
            raise UserError(_('Ollama provider error: %s') % str(e))
