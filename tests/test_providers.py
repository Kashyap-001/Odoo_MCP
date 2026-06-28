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
        from ..mcp.providers.base import AbstractProvider

        self.assertTrue(hasattr(AbstractProvider, 'call'))


class TestAnthropicProvider(TransactionCase):
    """Test Anthropic Claude adapter."""

    @mock.patch('anthropic.Anthropic')
    def test_anthropic_call_success(self, mock_anthropic_class):
        """Test successful Anthropic API call."""
        mock_client = mock_anthropic_class.return_value
        mock_response = mock.Mock()
        mock_response.model_dump.return_value = {
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
        mock_client.messages.create.return_value = mock_response

        from ..mcp.providers.anthropic import AnthropicAdapter
        provider = AnthropicAdapter(self.env)

        agent = mock.Mock()
        agent._decrypt_api_key.return_value = 'sk-ant-test'
        agent.model_name = 'claude-sonnet-4-6'
        agent.max_tokens = 100
        agent.temperature = 0.7
        agent.top_p = 0.9
        agent.system_prompt = 'You are a helpful assistant.'

        result = provider.call(
            agent,
            [{'role': 'user', 'content': 'Hello'}],
            [],
        )

        self.assertEqual(result['text'], 'Hello, I can help you with that.')
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)

    @mock.patch('anthropic.Anthropic')
    def test_anthropic_retry_logic(self, mock_anthropic_class):
        """Test Anthropic provider retry/error handling mapping."""
        mock_client = mock_anthropic_class.return_value
        
        import anthropic
        import httpx
        fake_request = httpx.Request("POST", "https://api.anthropic.com")
        fake_response = httpx.Response(429, request=fake_request)
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="Rate limit exceeded",
            response=fake_response,
            body={}
        )

        from ..mcp.providers.anthropic import AnthropicAdapter
        provider = AnthropicAdapter(self.env)

        agent = mock.Mock()
        agent._decrypt_api_key.return_value = 'sk-ant-test'
        agent.model_name = 'claude-sonnet-4-6'
        agent.max_tokens = 100
        agent.temperature = 0.7
        agent.top_p = 0.9
        agent.system_prompt = 'You are a helpful assistant.'

        with self.assertRaises(UserError) as cm:
            provider.call(agent, [], [])
        self.assertIn('rate limit exceeded', str(cm.exception).lower())


class TestOpenAIProvider(TransactionCase):
    """Test OpenAI GPT adapter."""

    @mock.patch('openai.OpenAI')
    def test_openai_call_success(self, mock_openai_class):
        """Test successful OpenAI API call."""
        mock_client = mock_openai_class.return_value
        
        mock_response = mock.Mock()
        mock_choice = mock.Mock()
        mock_message = mock.Mock()
        mock_message.content = 'I can help with that.'
        mock_message.function_call = None
        mock_message.tool_calls = []
        mock_choice.message = mock_message
        mock_choice.finish_reason = 'stop'
        mock_response.choices = [mock_choice]
        mock_response.usage = mock.Mock(prompt_tokens=10, completion_tokens=5)
        
        mock_response.model_dump.return_value = {
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
        
        mock_client.chat.completions.create.return_value = mock_response

        from ..mcp.providers.openai import OpenAIAdapter
        provider = OpenAIAdapter(self.env)

        agent = mock.Mock()
        agent._decrypt_api_key.return_value = 'sk-test'
        agent.model_name = 'gpt-4'
        agent.temperature = 0.7
        agent.max_tokens = 2000
        agent.top_p = 0.9

        result = provider.call(
            agent,
            [{'role': 'user', 'content': 'Test'}],
            [],
        )

        self.assertEqual(result['text'], 'I can help with that.')
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)

    @mock.patch('openai.OpenAI')
    def test_openai_tool_call_parsing(self, mock_openai_class):
        """Test OpenAI tool call parsing."""
        mock_client = mock_openai_class.return_value
        
        mock_response = mock.Mock()
        mock_choice = mock.Mock()
        mock_message = mock.Mock()
        mock_message.content = None
        mock_message.function_call = None
        
        mock_tool_call = mock.Mock()
        mock_tool_call.id = 'call-123'
        mock_tool_call.function = mock.Mock(name='get_weather', arguments='{"location": "NYC"}')
        
        mock_message.tool_calls = [mock_tool_call]
        mock_choice.message = mock_message
        mock_choice.finish_reason = 'tool_calls'
        mock_response.choices = [mock_choice]
        mock_response.usage = mock.Mock(prompt_tokens=10, completion_tokens=5)
        
        mock_response.model_dump.return_value = {
            'choices': [
                {
                    'message': {
                        'role': 'assistant',
                        'content': None,
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
        
        mock_client.chat.completions.create.return_value = mock_response

        from ..mcp.providers.openai import OpenAIAdapter
        provider = OpenAIAdapter(self.env)

        agent = mock.Mock()
        agent._decrypt_api_key.return_value = 'sk-test'
        agent.model_name = 'gpt-4'
        agent.temperature = 0.7
        agent.max_tokens = 2000
        agent.top_p = 0.9

        result = provider.call(agent, [], [])

        self.assertEqual(len(result['tool_calls']), 1)
        self.assertEqual(result['tool_calls'][0]['name'], 'get_weather')


class TestGeminiProvider(TransactionCase):
    """Test Google Gemini adapter."""

    @mock.patch('google.generativeai.GenerativeModel')
    @mock.patch('google.generativeai.configure')
    def test_gemini_call_success(self, mock_configure, mock_generative_model_class):
        """Test successful Gemini API call."""
        mock_model = mock_generative_model_class.return_value
        
        mock_response = mock.Mock()
        mock_candidate = mock.Mock()
        mock_candidate.finish_reason = mock.Mock(name='STOP')
        mock_candidate.content = mock.Mock(parts=[mock.Mock(text='This is a Gemini response.', function_call=None)])
        mock_response.candidates = [mock_candidate]
        mock_response.usage_metadata = {
            'prompt_token_count': 10,
            'candidates_token_count': 5,
        }
        mock_model.generate_content.return_value = mock_response

        from ..mcp.providers.gemini import GeminiAdapter
        provider = GeminiAdapter(self.env)

        agent = mock.Mock()
        agent._decrypt_api_key.return_value = 'test-gemini-key'
        agent.model_name = 'gemini-1.5-pro'
        agent.system_prompt = 'You are a helpful assistant.'
        agent.max_tokens = 100
        agent.temperature = 0.7
        agent.top_p = 0.9

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

        from ..mcp.providers.ollama import OllamaAdapter
        provider = OllamaAdapter(self.env)

        agent = mock.Mock()
        agent.base_url = 'http://localhost:11434'
        agent.model_name = 'llama2'

        result = provider.call(agent, [], [])

        self.assertEqual(result['text'], 'Ollama response')
        self.assertEqual(result['input_tokens'], 10)
        self.assertEqual(result['output_tokens'], 5)
