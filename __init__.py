"""
mcp_gateway/__init__.py

Module initialization for AI Gateway. Registers models and controllers.

Dependencies:
  - All models are auto-discovered by Odoo from models/ subdirectory
  - Controllers auto-discovered from controllers/ subdirectory
  - Wizards auto-discovered from wizard/ subdirectory

Developer notes:
  - This file is minimal — Odoo 18 auto-discovers models and controllers
  - All business logic lives in individual model files (mcp_agent.py, etc.)
"""

try:
    import odoo
except ImportError:
    import sys
    from unittest.mock import MagicMock

    class MockOdooModule:
        """Mock for odoo module when running tests outside Odoo environment."""
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
            return MagicMock()

    import unittest

    class MockTransactionCase(unittest.TestCase):
        """Mock TransactionCase for tests that still use it."""
        def setUp(self):
            super().setUp()
            self.env = MagicMock()
            self.env.user = MagicMock()
            self.env.user.id = 1

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
    sys.modules['odoo import'] = MagicMock()

from . import models
from . import controllers
from . import wizard

__all__ = ['models', 'controllers', 'wizard']
