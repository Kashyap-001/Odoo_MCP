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

from . import models
from . import controllers
from . import wizard

__all__ = ['models', 'controllers', 'wizard']
