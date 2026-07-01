def migrate(cr, version):
    # model_selection changed from Selection (varchar) to Many2one (integer).
    # Drop the old column so Odoo recreates it with the correct type.
    cr.execute("ALTER TABLE mcp_agent DROP COLUMN IF EXISTS model_selection")
