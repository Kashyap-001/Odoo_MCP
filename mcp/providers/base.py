"""
mcp_gateway/mcp/providers/base.py

Abstract base class for LLM provider adapters.

Key classes:
  AbstractProvider — Base class for all provider implementations

Dependencies:
  - abc module for abstract base class
  - requests for HTTP calls
  - json for payload building

Developer notes:
  - All providers follow same interface: build_headers, build_payload, parse_response
  - Providers never see plaintext API keys (decrypted only in call())
  - Error handling standardized: all exceptions converted to UserError
  - Subclasses implement provider-specific protocol details
"""

import logging
import requests
from abc import ABC, abstractmethod
from odoo.exceptions import UserError
from odoo import _

_logger = logging.getLogger(__name__)


class AbstractProvider(ABC):
    """
    Abstract base class for LLM provider adapters.

    Each provider (Anthropic, OpenAI, Gemini, Ollama) implements:
      1. build_headers() — HTTP headers including authentication
      2. build_payload() — Request body for the provider's API
      3. parse_response() — Parse provider response to standard format
      4. get_available_models() — Fetch list of available models

    The call() method orchestrates these, handles retries, and error handling.

    Developer notes:
      - Adapters receive agent record (with decrypted API key)
      - Never log or store plaintext keys
      - Return dict with standardized keys (text, stop_reason, tool_calls, tokens)
    """

    def __init__(self, env):
        """
        Initialize provider adapter.

        Args:
            env: Odoo environment for database access
        """
        self.env = env
        self._logger = logging.getLogger(self.__class__.__module__)

    @abstractmethod
    def build_headers(self, agent) -> dict:
        """
        Build HTTP headers for provider API call.

        Must include all required authentication.

        Args:
            agent: mcp.agent record (with decrypted api_key)

        Returns:
            dict: HTTP headers including Authorization if needed
        """

    @abstractmethod
    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        """
        Build request body for provider API.

        Translate generic messages and tool specs to provider format.

        Args:
            messages (list): Message history in format:
              [
                {'role': 'user', 'content': '...'},
                {'role': 'assistant', 'content': '...'},
              ]
            tool_specs (list): Tool specs in Anthropic format:
              [
                {
                  'name': 'tool_name',
                  'description': '...',
                  'input_schema': {...}
                }
              ]
            agent: mcp.agent record with LLM parameters

        Returns:
            dict: Request body formatted for this provider's API
        """

    @abstractmethod
    def parse_response(self, raw_json: dict) -> dict:
        """
        Parse provider API response to standard format.

        Args:
            raw_json (dict): Full JSON response from provider API

        Returns:
            dict: Standardized response with keys:
              {
                'text': str or None (assistant reply),
                'stop_reason': str ('end_turn', 'tool_use', 'max_tokens', etc.),
                'tool_calls': list of {
                  'id': str,
                  'name': str,
                  'arguments': dict,
                },
                'input_tokens': int,
                'output_tokens': int,
              }

        Raises:
            UserError: if response parsing fails
        """

    @abstractmethod
    def get_available_models(self, agent) -> list:
        """
        Fetch list of available models from provider.

        Used for model discovery and selection UI.

        Args:
            agent: mcp.agent record (for auth)

        Returns:
            list: Model IDs (e.g., ['claude-sonnet-4-6', 'claude-opus'])

        Raises:
            UserError: if fetch fails
        """

    def call(self, agent, messages: list, tool_specs: list) -> dict:
        """
        Make an API call to the LLM provider.

        Orchestrates building request, calling provider, and parsing response.
        Includes retry logic for transient failures.

        Args:
            agent: mcp.agent record
            messages (list): Message history
            tool_specs (list): Tool specifications

        Returns:
            dict: Standardized response (see parse_response)

        Raises:
            UserError: on persistent failure after retries
        """
        try:
            headers = self.build_headers(agent)
            payload = self.build_payload(messages, tool_specs, agent)
            url = agent.api_base_url or self._get_default_url(agent.provider)

            # ── Retry loop with exponential backoff ──────────────────────
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    response = requests.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=15,
                    )
                    response.raise_for_status()
                    return self.parse_response(response.json())

                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    if attempt < max_retries:
                        backoff = 2 ** attempt  # 1s, 2s
                        self._logger.warning(
                            '%s attempt %d failed (timeout/connection): retrying in %ds',
                            agent.provider, attempt + 1, backoff
                        )
                        import time
                        time.sleep(backoff)
                    else:
                        raise

                except requests.exceptions.HTTPError as e:
                    if e.response.status_code in (500, 502, 503, 429):
                        if attempt < max_retries:
                            backoff = 2 ** attempt
                            self._logger.warning(
                                '%s attempt %d failed (HTTP %d): retrying in %ds',
                                agent.provider, attempt + 1, e.response.status_code, backoff
                            )
                            import time
                            time.sleep(backoff)
                        else:
                            raise
                    else:
                        # Non-retryable HTTP error
                        raise

        except Exception as e:
            self._logger.error('%s call failed: %s', agent.provider, str(e))
            raise UserError(_('LLM provider error: %s') % str(e))

    def _get_default_url(self, provider: str) -> str:
        """
        Get default API URL for a provider.

        Returns:
            str: Base URL for provider API
        """
        urls = {
            'anthropic': 'https://api.anthropic.com/v1/messages',
            'openai': 'https://api.openai.com/v1/chat/completions',
            'gemini': 'https://generativelanguage.googleapis.com/v1beta/models',
            'ollama': 'http://localhost:11434/api/chat',
        }
        return urls.get(provider, '')
