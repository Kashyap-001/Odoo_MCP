#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/kashyap/odoo/odoo18')

import odoo
from odoo import api, SUPERUSER_ID
from odoo.tools import config

config.parse_config(['-c', '/home/kashyap/odoo/odoo18/odoo.conf'])
registry = odoo.modules.registry.Registry('odoo18')

with registry.cursor() as cr:
    env = api.Environment(cr, SUPERUSER_ID, {})
    Issue = env['mcp.issue']

    issues = Issue.search_read([], ['id', 'name', 'state', 'category', 'reporter_id', 'create_date'])

    print("=== Odoo Internal Issues ===")
    if not issues:
        print("No internal issues found")
    for issue in issues:
        state = issue.get('state') or 'unlabeled'
        category = issue.get('category') or 'unlabeled'
        print(f"ID: {issue['id']} | State: {state} | Category: {category} | {issue['name'][:60]}")