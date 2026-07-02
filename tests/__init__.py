"""
mcp_gateway/tests/__init__.py

Test suite for AI Gateway module.

Test modules:
  - test_gateway — Core orchestration logic
  - test_tools — Tool dispatch and execution
  - test_access — Access control and rate limiting
  - test_providers — LLM provider adapters
  - test_webhook — Webhook trigger system

Running tests — TWO paths exist, they are NOT equivalent:

  1. CANONICAL — Odoo's real test runner, against an actual registry/DB:
       cd <odoo18_dir> && python3 odoo-bin \\
         --addons-path=<path_to_this_module's_parent>,<odoo18_dir>/addons \\
         -d <your_db> -p <unused_port> \\
         --test-enable --test-tags /mcp_gateway -u mcp_gateway --stop-after-init
     This is the authoritative signal. It actually exercises the ORM, so a
     `create()` genuinely reflects the values you passed, constraints fire,
     etc.

  2. FAST SMOKE TEST — plain `pytest tests/` via tests/conftest.py, which mocks
     the entire `odoo` package (no DB, no registry). `self.env['model'].create()`
     returns a bare unconfigured MagicMock, NOT a record reflecting the input —
     so any test asserting on a created record's field values will fail here
     even when the code is completely correct. Useful only as a fast "does it
     import/run at all" check — never trust a pytest failure over path 1's
     result, and never trust pytest fully passing as proof of ORM-level
     correctness either.
"""

from . import test_gateway
from . import test_tools
from . import test_access
from . import test_providers
from . import test_webhook

__all__ = [
    'test_gateway',
    'test_tools',
    'test_access',
    'test_providers',
    'test_webhook',
]
