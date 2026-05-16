#!/usr/bin/env python3
"""
List issues from Odoo database for triage skill integration.
Usage: python3 list_issues.py [--state STATE] [--category CATEGORY]
"""

import os
import sys
import argparse
import json

# Add Odoo to path
odoo_path = os.path.expanduser('~/odoo/odoo18')
sys.path.insert(0, odoo_path)

import odoo
from odoo.modules.registry import Registry
from odoo.sql_db import db_connect


def list_issues(state=None, category=None):
    """Query issues from Odoo database."""
    try:
        db_name = 'odoo18'
        registry = Registry(db_name)
        with registry.cursor() as cr:
            Issue = registry.get('mcp.issue')
            domain = [('active', '=', True)]
            if state:
                domain.append(('state', '=', state))
            if category:
                domain.append(('category', '=', category))

            issues = Issue.search_read(
                cr, 1,  # uid
                domain,
                ['id', 'name', 'category', 'state', 'priority', 'reporter_id', 'assignee_id', 'create_date', 'description']
            )

            return issues
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(description='List Odoo issues')
    parser.add_argument('--state', help='Filter by state (needs_triage, needs_info, etc.)')
    parser.add_argument('--category', help='Filter by category (bug, enhancement)')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    args = parser.parse_args()

    issues = list_issues(state=args.state, category=args.category)

    if args.json:
        print(json.dumps(issues, default=str))
    else:
        for issue in issues:
            print(f"#{issue['id']}: [{issue['category']}] [{issue['state']}] {issue['name']}")
            print(f"   Reporter: {issue['reporter_id'][1] if issue['reporter_id'] else 'N/A'}")
            print(f"   Priority: {issue['priority']}")
            print()


if __name__ == '__main__':
    main()