def migrate(cr, version):
    cr.execute("UPDATE mcp_agent SET provider = 'grok' WHERE provider = 'minimax'")
    cr.execute("UPDATE mcp_model_option SET provider = 'grok' WHERE provider = 'minimax'")
