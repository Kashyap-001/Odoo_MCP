def migrate(cr, version):
    cr.execute("DROP TABLE IF EXISTS mcp_issue_tag_mcp_issue_rel CASCADE")
    cr.execute("DROP TABLE IF EXISTS mcp_issue CASCADE")
    cr.execute("DROP TABLE IF EXISTS mcp_issue_tag CASCADE")
