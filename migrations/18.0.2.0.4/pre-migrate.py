"""
18.0.2.0.4 — pre-migrate.py

Drop unused ORM auto-trigger columns from mcp_webhook_trigger.
These fields (trigger_model, trigger_on, trigger_fields, domain) were
placeholders for an ORM hook feature that was never implemented.
HTTP-only webhook via n8n is the supported pattern.
"""


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE mcp_webhook_trigger
            DROP COLUMN IF EXISTS trigger_model,
            DROP COLUMN IF EXISTS trigger_on,
            DROP COLUMN IF EXISTS trigger_fields,
            DROP COLUMN IF EXISTS domain
    """)
