"""
conftest.py — Pytest configuration with Odoo mocks.

Mocks Odoo modules so unit tests can run without Odoo database.
"""

import sys
from unittest.mock import MagicMock, patch

# Create mock Odoo modules
class MockOdooModule:
    """Mock for odoo module."""
    __path__ = []
    def __getattr__(self, name):
        if name == '_':
            return lambda x: x
        return MockOdooSubModule()


class MockOdooSubModule:
    """Mock for submodules like odoo.tests, odoo.exceptions."""
    __path__ = []
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
        if name == 'ValidationError':
            return Exception
        return MagicMock()


import unittest

class MockTransactionCase(unittest.TestCase):
    """Mock TransactionCase for tests that still use it."""
    def setUp(self):
        super().setUp()
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
sys.modules['odoo.exceptions'].ValidationError = Exception
sys.modules['odoo.fields'] = MockOdooSubModule()
sys.modules['odoo.http'] = MockOdooSubModule()
sys.modules['odoo.tools'] = MockOdooSubModule()
sys.modules['odoo.tools.safe_eval'] = MockOdooSubModule()
sys.modules['odoo import'] = MagicMock()  # Handle "from odoo import x"


# Mock the mcp package imports that import odoo
import mcp_gateway.mcp.gateway