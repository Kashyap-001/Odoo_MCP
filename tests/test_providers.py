"""
mcp_gateway/tests/test_providers.py

Test suite for LLM provider adapters.

Test classes:
  TestProviderBase — Abstract provider interface
  TestAnthropicProvider — Claude API adapter
  TestOpenAIProvider — GPT API adapter
  TestGeminiProvider — Gemini API adapter
  TestOllamaProvider — Local Ollama adapter

Dependencies:
  - unittest.mock — Mocking HTTP responses
"""

import json
from unittest import mock
from odoo.tests import TransactionCase
from odoo.exceptions import UserError


class TestProviderBase(TransactionCase):
    """Test abstract provider interface."""

    def test_provider_call_method_exists(self):
        """Test provider has call method."""
        from mcp.providers.base import AbstractProvider

        self.assertTrue(hasattr(AbstractProvider, 'call'))


class TestAnthropicProvider(TransactionCase):
    """Test Anthropic Claude adapter."""

    @mock.patch('requests.post')
    def test_anthropic_call_success(self, mock_post):
        """Test successful Anthropic API call."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'id': 'msg-123',
            'content': [
                {
                    'type': 'text',
                    'text': 'Hello, I can help you with that.',
                }
            ],
            'usage': {
                'input_tokens': 10,
                'output_tokens': 5,
            },
            'stop_reason': 'end_turn',
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        from mcp.providers.anthropic import AnthropicProvider
        provider = AnthropicProvider()

        agent = mock.Mock()
        agent.api_key = 'sk-ant-test'
        agent.model_name = 'claude-sonnet-4-6'

        result = provider.call(
            agent,
            [{'role': 'user', 'content': 'Hello'}],
            [],
        )

        self.assertEqual(result['text'], 'Hello, I can help you with that.')
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)

    @mock.patch('requests.post')
    def test_anthropic_retry_logic(self, mock_post):
        """Test Anthropic provider retry on 500 error."""
        mock_response_error = mock.Mock()
        mock_response_error.status_code = 500
        mock_response_error.raise_for_status.side_effect = Exception('Server error')

        mock_response_ok = mock.Mock()
        mock_response_ok.json.return_value = {
            'content': [{'type': 'text', 'text': 'Retried'}],
            'usage': {'input_tokens': 1, 'output_tokens': 1},
            'stop_reason': 'end_turn',
        }
        mock_response_ok.status_code = 200

        # First call fails, second succeeds
        mock_post.side_effect = [mock_response_error, mock_response_ok]

        from mcp.providers.anthropic import AnthropicProvider
        provider = AnthropicProvider()

        agent = mock.Mock()
        agent.api_key = 'sk-ant-test'
        agent.model_name = 'claude-sonnet-4-6'

        # Should retry and eventually succeed
        with mock.patch('time.sleep'):  # Skip sleep delay
            result = provider.call(agent, [], [])
            self.assertEqual(result['text'], 'Retried')


class TestOpenAIProvider(TransactionCase):
    """Test OpenAI GPT adapter."""

    @mock.patch('requests.post')
    def test_openai_call_success(self, mock_post):
        """Test successful OpenAI API call."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'id': 'chatcmpl-123',
            'choices': [
                {
                    'message': {
                        'role': 'assistant',
                        'content': 'I can help with that.',
                    },
                    'finish_reason': 'stop',
                }
            ],
            'usage': {
                'prompt_tokens': 10,
                'completion_tokens': 5,
            },
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        from mcp.providers.openai import OpenAIProvider
        provider = OpenAIProvider()

        agent = mock.Mock()
        agent.api_key = 'sk-test'
        agent.model_name = 'gpt-4'
        agent.temperature = 0.7
        agent.max_tokens = 2000

        result = provider.call(
            agent,
            [{'role': 'user', 'content': 'Test'}],
            [],
        )

        self.assertEqual(result['text'], 'I can help with that.')
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)

    @mock.patch('requests.post')
    def test_openai_tool_call_parsing(self, mock_post):
        """Test OpenAI tool call parsing."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'choices': [
                {
                    'message': {
                        'role': 'assistant',
                        'tool_calls': [
                            {
                                'id': 'call-123',
                                'function': {
                                    'name': 'get_weather',
                                    'arguments': '{"location": "NYC"}',
                                },
                            }
                        ],
                    },
                    'finish_reason': 'tool_calls',
                }
            ],
            'usage': {
                'prompt_tokens': 10,
                'completion_tokens': 5,
            },
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        from mcp.providers.openai import OpenAIProvider
        provider = OpenAIProvider()

        agent = mock.Mock()
        agent.api_key = 'sk-test'
        agent.model_name = 'gpt-4'

        result = provider.call(agent, [], [])

        self.assertEqual(len(result['tool_calls']), 1)
        self.assertEqual(result['tool_calls'][0]['name'], 'get_weather')


class TestGeminiProvider(TransactionCase):
    """Test Google Gemini adapter."""

    @mock.patch('requests.post')
    def test_gemini_call_success(self, mock_post):
        """Test successful Gemini API call."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'candidates': [
                {
                    'content': {
                        'parts': [
                            {
                                'text': 'This is a Gemini response.',
                            }
                        ]
                    }
                }
            ],
            'usageMetadata': {
                'promptTokenCount': 10,
                'candidatesTokenCount': 5,
            },
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        from mcp.providers.gemini import GeminiProvider
        provider = GeminiProvider()

        agent = mock.Mock()
        agent.api_key = 'test-gemini-key'
        agent.model_name = 'gemini-1.5-pro'

        result = provider.call(agent, [], [])

        self.assertIn('Gemini response', result['text'])


class TestOllamaProvider(TransactionCase):
    """Test local Ollama adapter."""

    @mock.patch('requests.post')
    def test_ollama_call_success(self, mock_post):
        """Test successful Ollama API call."""
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'model': 'llama2',
            'created_at': '2024-01-01T00:00:00.000000Z',
            'message': {
                'role': 'assistant',
                'content': 'Ollama response',
            },
            'done': True,
            'total_duration': 1000000,
            'load_duration': 100000,
            'prompt_eval_count': 10,
            'eval_count': 5,
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        from mcp.providers.ollama import OllamaProvider
        provider = OllamaProvider()

        agent = mock.Mock()
        agent.base_url = 'http://localhost:11434'
        agent.model_name = 'llama2'

        result = provider.call(agent, [], [])

        self.assertEqual(result['text'], 'Ollama response')
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)
