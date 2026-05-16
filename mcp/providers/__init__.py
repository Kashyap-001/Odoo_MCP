"""
mcp_gateway/mcp/providers/__init__.py

Provider adapter registry for LLM backends.

Adapters defined:
  - Anthropic (Claude models) - uses anthropic SDK
  - OpenAI (GPT models) - uses openai SDK
  - Google Gemini (Gemini models) - uses google-generativeai SDK
  - Ollama (local models) - direct HTTP
  - MiniMax (MiniMax models)
  - OpenCode (OpenCode AI - OpenAI compatible)

Usage:
  provider_class = PROVIDER_MAP[agent.provider]
  provider = provider_class(env)
  result = provider.call(agent, messages, tool_specs)
"""

from .base import AbstractProvider
from .anthropic import AnthropicAdapter
from .openai import OpenAIAdapter
from .gemini import GeminiAdapter
from .ollama import OllamaAdapter
from .minimax import MiniMaxAdapter
from .opencode import OpenCodeAdapter

PROVIDER_MAP = {
    'anthropic': AnthropicAdapter,
    'openai': OpenAIAdapter,
    'gemini': GeminiAdapter,
    'ollama': OllamaAdapter,
    'minimax': MiniMaxAdapter,
    'opencode': OpenCodeAdapter,
}

__all__ = [
    'AbstractProvider',
    'AnthropicAdapter',
    'OpenAIAdapter',
    'GeminiAdapter',
    'OllamaAdapter',
    'MiniMaxAdapter',
    'OpenCodeAdapter',
    'PROVIDER_MAP',
]
