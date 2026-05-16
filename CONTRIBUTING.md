# Contributing to AI Gateway

Thank you for your interest in contributing to the AI Gateway module for Odoo 18! This guide covers development setup, coding standards, and how to extend the module.

## Development Setup

### Prerequisites
- Odoo 18 Community or Enterprise
- Python 3.12+
- Git (for version control)
- A virtual environment (venv or conda)

### Step 1: Fork & Clone
```bash
git clone https://github.com/yourusername/odoo-mcp-gateway.git
cd odoo-mcp-gateway
```

### Step 2: Create Virtual Environment
```bash
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Step 3: Install Dependencies
```bash
pip install -e .
pip install requests cryptography
pip install pytest pytest-cov  # For testing
```

### Step 4: Link Module to Odoo
```bash
# In your Odoo addons directory:
ln -s /path/to/odoo-mcp-gateway/mcp_gateway ./mcp_gateway
```

### Step 5: Start Odoo
```bash
odoo-bin -c /path/to/odoo.conf --addons-path=/path/to/addons --dev=all
```

### Step 6: Install & Enable Developer Mode
- Go to Odoo → Apps → Update app list → Install `mcp_gateway`
- Enable Developer mode (user avatar → Settings → toggle "Developer mode")
- Navigate to AI Gateway menu to verify

## Coding Standards

All contributions must follow these standards:

### Python Style
- **PEP 8 compliant** (4-space indentation, line length ≤ 100 chars)
- **Type hints** on all gateway and provider methods
- **Logging:** `_logger = logging.getLogger(__name__)` in every file
- **No global state:** Pass env and user as parameters, never import them globally
- **Docstrings:** Full module, class, and method docstrings per Section 4 of README

### File Headers
Every Python file must start with:
```python
"""
mcp_gateway/<path/to/file.py>

<One paragraph describing the file's purpose.>

Key classes:
  ClassName — brief description

Dependencies:
  - any non-obvious imports

Developer notes:
  - gotchas or design decisions
"""
```

### Method Docstrings
Every public method must have a docstring:
```python
def my_method(self, param1: str, param2: int) -> dict:
    """
    One-line summary.

    Longer explanation if needed.

    Args:
        param1 (str): What it is
        param2 (int): What it is

    Returns:
        dict: What is returned

    Raises:
        UserError: When and why
        AccessError: When and why

    Example:
        result = self.my_method('hello', 42)
    """
```

### Odoo 18 Specifics
- Use `_inherit = 'model'` pattern, never old-style `_columns`
- Use `<list>` not `<tree>` in XML views
- Use `_()` for all user-facing strings (translation support)
- Use `ir.config_parameter` for module settings
- Use `@api.constrains` for field validation
- Use `mail.thread` and `mail.activity.mixin` where appropriate
- No deprecated `fields.Reference` — use Many2one with domain instead

### Comments
Add comments above every non-obvious code block:
```python
# Decrypt API key from Fernet ciphertext before passing to provider.
# We never store or log plaintext keys — they live in memory only.
decrypted_key = self._decrypt_api_key()
```

For long methods, add section dividers:
```python
# ── 1. Validate input ──────────────────────────────────────────
validation_result = self._validate(param)

# ── 2. Update database ─────────────────────────────────────────
self.state = 'done'
```

### Security
- **API keys:** Always Fernet-encrypt before writing. Never log plaintext.
- **Access checks:** Use `env['ir.rule'].check_access_rule()` or access rules via `mcp.access.rule.get_rules_for_user()`
- **Tool calls:** Log every call (even failures) for audit trail
- **Validation:** Sanitize all external input with `@api.constrains` or `.strip()`

### Testing
- Write unit tests for all new features
- Use `TransactionCase` for database access
- Mock external HTTP calls with `unittest.mock.patch`
- Aim for >80% code coverage

Example test:
```python
from odoo.tests import TransactionCase

class TestMyFeature(TransactionCase):
    def setUp(self):
        super().setUp()
        self.agent = self.env['mcp.agent'].create({
            'name': 'Test Agent',
            'provider': 'anthropic',
            'model_name': 'claude-sonnet-4-6',
        })

    def test_agent_creation(self):
        self.assertTrue(self.agent.id)
        self.assertEqual(self.agent.status, 'unconfigured')
```

## How to Add a New AI Provider

### Step 1: Create Provider Class
Create `mcp/providers/myprovider.py`:
```python
from mcp.providers.base import AbstractProvider

class MyProviderAdapter(AbstractProvider):
    """Adapter for MyProvider LLM API."""

    def build_headers(self, agent) -> dict:
        """Return HTTP headers including auth."""
        return {
            'Authorization': f'Bearer {agent._decrypt_api_key()}',
            'Content-Type': 'application/json',
        }

    def build_payload(self, messages: list, tool_specs: list, agent) -> dict:
        """Build request body for provider API."""
        return {
            'model': agent.model_name,
            'messages': messages,
            'tools': tool_specs,
            'temperature': agent.temperature,
            'max_tokens': agent.max_tokens,
        }

    def parse_response(self, raw_json: dict) -> dict:
        """Parse provider response into standard format."""
        return {
            'text': raw_json.get('choices', [{}])[0].get('message', {}).get('content'),
            'stop_reason': raw_json.get('choices', [{}])[0].get('finish_reason'),
            'tool_calls': [],  # Extract from raw_json as needed
            'input_tokens': raw_json.get('usage', {}).get('prompt_tokens', 0),
            'output_tokens': raw_json.get('usage', {}).get('completion_tokens', 0),
        }

    def get_available_models(self, agent) -> list:
        """Fetch list of available models from provider."""
        headers = self.build_headers(agent)
        response = requests.get('https://api.myprovider.com/models', headers=headers, timeout=10)
        response.raise_for_status()
        return [m['id'] for m in response.json()['models']]
```

### Step 2: Register in Models
Update `models/mcp_agent.py` — add to provider field selection:
```python
provider = fields.Selection([
    ('anthropic', 'Anthropic'),
    ('openai', 'OpenAI'),
    ('gemini', 'Google Gemini'),
    ('ollama', 'Ollama (local)'),
    ('myprovider', 'MyProvider'),  # Add here
], required=True)
```

### Step 3: Import in Provider Registry
Update `mcp/providers/__init__.py`:
```python
from .myprovider import MyProviderAdapter

PROVIDER_MAP = {
    'anthropic': AnthropicAdapter,
    'openai': OpenAIAdapter,
    'gemini': GeminiAdapter,
    'ollama': OllamaAdapter,
    'myprovider': MyProviderAdapter,  # Add here
}
```

### Step 4: Test & Document
- Write tests in `tests/test_providers.py`
- Add default model_name examples to README
- Document API specifics in comments

## How to Add a New Built-in Tool

### Option A: Odoo ORM Tool (no code)
1. Go to AI Gateway → Tools → New
2. **Name:** `partner_search` (snake_case)
3. **Display Name Label:** "Partner Search"
4. **Tool Type:** "Odoo built-in"
5. **Odoo Model:** `res.partner`
6. **Odoo Method:** `search_read`
7. **Odoo Fields:** `id,name,email,phone`
8. **Odoo Domain:** `[['active','=',True]]` (JSON)
9. **Is Readonly:** `True`
10. **Input Schema:** Fill with JSON Schema (see example below)
11. **Save**

Example JSON Schema for Odoo tool:
```json
{
  "type": "object",
  "properties": {
    "domain": {
      "type": "array",
      "description": "Odoo domain filter (e.g., [['name','ilike','John']])"
    },
    "limit": {
      "type": "integer",
      "description": "Max records to return",
      "default": 10
    }
  },
  "required": ["domain"]
}
```

### Option B: External API Tool (no code)
1. Go to AI Gateway → Tools → New
2. **Name:** `weather_forecast`
3. **Tool Type:** "External API"
4. **Endpoint URL:** `https://api.weather.example.com/forecast`
5. **HTTP Method:** `GET`
6. **Auth Type:** `API key header`
7. **Auth Value:** Your API key
8. **Response Path:** `data.forecast` (uses dot notation to extract result)
9. **Timeout Seconds:** `15`
10. **Input Schema:** JSON Schema with parameters
11. **Save**

Example JSON Schema for external API:
```json
{
  "type": "object",
  "properties": {
    "lat": {
      "type": "number",
      "description": "Latitude"
    },
    "lon": {
      "type": "number",
      "description": "Longitude"
    },
    "days": {
      "type": "integer",
      "description": "Number of days to forecast",
      "default": 5
    }
  },
  "required": ["lat", "lon"]
}
```

### Option C: Custom MCP Server Tool (with code)
1. Create your MCP server (see doc/adding_a_tool.md for example)
2. Run it: `python mcp_server.py`
3. Go to AI Gateway → Tools → New
4. **Tool Type:** "Custom MCP server"
5. **MCP Server URL:** `http://localhost:8000`
6. **MCP Server Key:** `your-secret-key`
7. **Save**

## Running Tests

### All Tests
```bash
cd /path/to/mcp_gateway
python -m pytest tests/ -v --cov=mcp_gateway --cov-report=html
```

### Specific Test
```bash
python -m pytest tests/test_gateway.py::TestMcpGateway::test_run_with_mock_provider -v
```

### With Coverage Report
```bash
python -m pytest tests/ --cov=mcp_gateway --cov-report=term-missing
```

## Pull Request Checklist

Before submitting a PR, ensure:

- [ ] Code follows PEP 8 and Odoo 18 conventions
- [ ] All new methods have complete docstrings
- [ ] New files have header comments
- [ ] All user-facing strings use `_()` for translation
- [ ] API keys are never logged or exposed
- [ ] Tests added for new features (>80% coverage)
- [ ] Tests pass: `pytest tests/`
- [ ] No breaking changes to existing APIs
- [ ] Views use `<list>` not `<tree>`
- [ ] OWL 3 components (no legacy Widget)
- [ ] No deprecated Odoo patterns
- [ ] CHANGELOG.md updated with your changes
- [ ] Commit message is clear and references issues

## Code Review Process

1. Create a PR with a clear title and description
2. Ensure all CI checks pass (tests, linting)
3. Respond to reviewer feedback
4. After approval, squash commits and merge to main
5. Your contribution will be included in the next release!

## Reporting Issues

Use GitHub Issues to report bugs. Include:
- Odoo version
- Python version
- Steps to reproduce
- Expected vs. actual behavior
- Relevant error logs (without sensitive data)

## License

By contributing, you agree that your code will be licensed under LGPL-3. See LICENSE file.

---

Thank you for making AI Gateway better! 🙌
