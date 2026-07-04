# Adding a Custom LLM Provider

This guide shows how to add support for a new LLM provider (e.g., Cohere, Hugging Face, custom API).

## Step 1: Create Provider Adapter

Create file: `mcp_gateway/mcp/providers/myai.py`

```python
"""MyAI Provider Adapter"""

import requests
import logging
from mcp.providers.base import AbstractProvider

_logger = logging.getLogger(__name__)

class MyAIProvider(AbstractProvider):
    """MyAI LLM adapter"""

    def build_headers(self, agent) -> dict:
        """Build HTTP headers with authentication"""
        return {
            'Authorization': f'Bearer {agent.api_key}',
            'Content-Type': 'application/json',
        }

    def build_payload(self, agent, messages, tool_specs) -> dict:
        """Build request payload in MyAI format"""
        payload = {
            'model': agent.model_name,
            'messages': messages,
            'temperature': agent.temperature,
            'max_tokens': agent.max_tokens,
        }
        
        if tool_specs:
            # MyAI-specific tool format
            payload['tools'] = [
                {
                    'name': spec['name'],
                    'description': spec['description'],
                    'parameters': spec['input_schema'],
                }
                for spec in tool_specs
            ]
        
        return payload

    def parse_response(self, response_data) -> dict:
        """Parse MyAI API response to standard format"""
        choice = response_data.get('choices', [{}])[0]
        message = choice.get('message', {})
        
        text = message.get('content', '')
        tool_calls = []
        
        # Parse tool calls if present
        if 'tool_calls' in message:
            for call in message['tool_calls']:
                tool_calls.append({
                    'id': call.get('id'),
                    'name': call.get('function', {}).get('name'),
                    'arguments': call.get('function', {}).get('arguments'),
                })
        
        usage = response_data.get('usage', {})
        
        return {
            'text': text,
            'stop_reason': choice.get('finish_reason', 'unknown'),
            'tool_calls': tool_calls,
            'input_tokens': usage.get('prompt_tokens', 0),
            'output_tokens': usage.get('completion_tokens', 0),
        }

    @staticmethod
    def get_available_models() -> list:
        """Return list of available model names"""
        return [
            'myai-large',
            'myai-medium',
            'myai-small',
        ]

    def format_tool_calls(self, tool_calls: list) -> list:
        """Shape tool_calls for the assistant history entry sent back to the provider.
        OpenAI-wire-format providers (OpenAI/Ollama/Grok/OpenCode) all return the same
        shape here — copy one of those, not Anthropic's or Gemini's (both are unique)."""
        return [
            {
                'id': call['id'],
                'type': 'function',
                'function': {'name': call['name'], 'arguments': call['arguments']},
            }
            for call in tool_calls
        ]

    def format_tool_result(self, tool_call_id: str, tool_name: str, result: str) -> dict:
        """Build the message that reports a tool's result back to the provider."""
        return {
            'role': 'tool',
            'tool_call_id': tool_call_id,
            'name': tool_name,
            'content': result,
        }
```

**Both methods are `@abstractmethod` on `AbstractProvider`** — Python's ABC machinery raises
`TypeError` at instantiation if either is missing or misnamed. This isn't optional boilerplate:
Gemini's adapter was uninstantiable for weeks because it implemented `_parse_response` instead of
the required `parse_response`, and Anthropic's `format_tool_calls()` once returned `[]`
unconditionally, silently dropping every tool call from conversation history after the first turn.

## Step 2: Register Provider

Edit `mcp_gateway/mcp/providers/__init__.py`:

```python
from mcp.providers.myai import MyAIProvider

__all__ = [
    'AbstractProvider',
    'AnthropicProvider',
    'OpenAIProvider',
    'GeminiProvider',
    'OllamaProvider',
    'GrokProvider',
    'OpenCodeProvider',
    'MyAIProvider',  # Add this
]
```

## Step 3: Update Agent Model Selection

Edit `mcp_gateway/models/mcp_agent.py`, update the provider field:

```python
provider = fields.Selection([
    ('anthropic', 'Anthropic Claude'),
    ('openai', 'OpenAI GPT'),
    ('gemini', 'Google Gemini'),
    ('ollama', 'Ollama Local'),
    ('grok', 'Grok (xAI)'),
    ('opencode', 'OpenCode AI'),
    ('myai', 'MyAI'),  # Add this
], ...)
```

Update `_onchange_provider()` to set default model:

```python
def _onchange_provider(self):
    ...
    elif self.provider == 'myai':
        self.model_name = 'myai-large'
```

Update `_get_provider_instance()` to import:

```python
def _get_provider_instance(self):
    ...
    elif self.provider == 'myai':
        from mcp.providers.myai import MyAIProvider
        return MyAIProvider()
```

## Step 4: Create Views for Configuration

Edit `mcp_gateway/views/mcp_agent_views.xml`, add to agent form:

```xml
<!-- In agent form, myai-specific fields group -->
<group string="MyAI Configuration" attrs="{'invisible': [('provider', '!=', 'myai')]}">
    <field name="api_key" label="MyAI API Key" required="1"/>
    <field name="model_name" attrs="{'invisible': [('provider', '!=', 'myai')]}"/>
</group>
```

## Step 5: Write Tests

Create `mcp_gateway/tests/test_myai_provider.py`:

```python
from unittest import mock
from odoo.tests import TransactionCase

class TestMyAIProvider(TransactionCase):
    """Test MyAI provider adapter"""

    @mock.patch('requests.post')
    def test_myai_call_success(self, mock_post):
        """Test successful MyAI API call"""
        mock_response = mock.Mock()
        mock_response.json.return_value = {
            'choices': [{
                'message': {'content': 'Hello from MyAI'},
                'finish_reason': 'stop',
            }],
            'usage': {
                'prompt_tokens': 10,
                'completion_tokens': 5,
            },
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        from mcp.providers.myai import MyAIProvider
        provider = MyAIProvider()

        agent = mock.Mock()
        agent.api_key = 'test-key'
        agent.model_name = 'myai-large'
        agent.temperature = 0.7
        agent.max_tokens = 2000

        result = provider.call(agent, [], [])

        self.assertEqual(result['text'], 'Hello from MyAI')
        self.assertEqual(result['input_tokens'], 10)
```

## Step 6: Update Documentation

Edit `README.md` to list new provider in Supported Providers section.

## Implementation Checklist

- [ ] Create provider adapter (inherit AbstractProvider)
- [ ] Implement all 6 abstract methods (`build_headers`, `build_payload`, `parse_response`, `get_available_models`, `format_tool_calls`, `format_tool_result`) — ABC raises `TypeError` at instantiation if any is missing
- [ ] Register in `__init__.py` (both the import and `PROVIDER_MAP`)
- [ ] Update agent model selection field
- [ ] Update `_onchange_provider()`
- [ ] Update `_get_provider_instance()`
- [ ] Add form views for config
- [ ] Write unit tests
- [ ] Test with actual API
- [ ] Update README documentation
- [ ] Create migration guide if breaking changes

## Common Patterns

### Authentication Methods

**Bearer Token (Most common)**
```python
headers = {
    'Authorization': f'Bearer {agent.api_key}',
}
```

**API Key Header**
```python
headers = {
    'X-API-Key': agent.api_key,
}
```

**Query Parameter**
```python
url = f'https://api.example.com/endpoint?key={agent.api_key}'
```

### Tool Call Formats

**Standard (OpenAI/Anthropic)**
```json
{
  "tool_calls": [
    {
      "id": "call-123",
      "function": {"name": "get_weather", "arguments": "{}"}
    }
  ]
}
```

**Custom Format**
Adapt in `parse_response()` to match provider's format, then convert to standard.

### Retry Strategy

`AbstractProvider.call()` handles the HTTP-level retry (unless you override `call()` for
provider-specific requirements):
- Max 2 attempts (3 tries total)
- Backoff `2 ** attempt` — 1s, 2s
- Handles connection/timeout errors and 5xx/429 HTTP errors

Separately, `gateway.py`'s turn loop wraps every `provider.call()` in its own outer retry
(3 retries, `2s * (attempt + 1)` backoff — 2s/4s/6s) that catches providers which return an
in-band error string (e.g. `"[Provider error: ...]"`) instead of raising — this is orchestration-level,
not something a new provider adapter needs to implement itself.

### Timeout Configuration

Default: 15 seconds (cloud providers)

For Ollama-like local services:
```python
def call(self, agent, messages, tool_specs):
    timeout = 30  # Longer timeout for local
    ...
```
