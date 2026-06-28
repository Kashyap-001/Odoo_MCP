"""
conftest.py — Pytest configuration with Odoo mocks.

Mocks Odoo modules so unit tests can run without Odoo database.
"""

import sys
from unittest.mock import MagicMock, patch

# Create mock Odoo modules
class MockOdooModule:
    """Mock for odoo module."""
    def __getattr__(self, name):
        return getattr(self, name, MockOdooSubModule())


class MockOdooSubModule:
    """Mock for submodules like odoo.tests, odoo.exceptions."""
    def __getattr__(self, name):
        if name == 'fields':
            return MagicMock()
        if name == 'models':
            return MagicMock()
        if name == 'api':
            return MagicMock()
        if name == '_':
            return lambda x: x
        if name == 'TransactionCase':
            # For integration tests, we'll provide a mock
            return MockTransactionCase
        if name == 'AccessError':
            return Exception
        if name == 'UserError':
            return Exception
        return MagicMock()


class MockTransactionCase:
    """Mock TransactionCase for tests that still use it."""
    def setUp(self):
        self.env = MagicMock()
        self.env.user = MagicMock()
        self.env.user.id = 1

    def __init__(self):
        self.env = MagicMock()
        self.env.user = MagicMock()
        self.env.user.id = 1


# Install mock odoo module
sys.modules['odoo'] = MockOdooModule()
sys.modules['odoo.tests'] = MagicMock()
sys.modules['odoo.tests'].TransactionCase = MockTransactionCase
sys.modules['odoo.exceptions'] = MagicMock()
sys.modules['odoo.exceptions'].AccessError = Exception
sys.modules['odoo.exceptions'].UserError = Exception
sys.modules['odoo import'] = MagicMock()  # Handle "from odoo import x"


# Mock the mcp package imports that import odoo
import mcp_gateway.mcp.gateway