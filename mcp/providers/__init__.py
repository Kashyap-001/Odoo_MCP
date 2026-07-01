from .base import AbstractProvider
from .anthropic import AnthropicAdapter
from .openai import OpenAIAdapter
from .gemini import GeminiAdapter
from .ollama import OllamaAdapter
from .grok import GrokAdapter
from .opencode import OpenCodeAdapter

PROVIDER_MAP = {
    'anthropic': AnthropicAdapter,
    'openai': OpenAIAdapter,
    'gemini': GeminiAdapter,
    'ollama': OllamaAdapter,
    'grok': GrokAdapter,
    'opencode': OpenCodeAdapter,
}

__all__ = [
    'AbstractProvider',
    'AnthropicAdapter',
    'OpenAIAdapter',
    'GeminiAdapter',
    'OllamaAdapter',
    'GrokAdapter',
    'OpenCodeAdapter',
    'PROVIDER_MAP',
]
