"""
mcp_gateway/mcp/tools/builtin_tools.py

Pre-configured Odoo tools for the built-in tool library.

Defines 14 commonly-used Odoo tools that are auto-installed:
  1-2:   Partner (search, create)
  3-5:   Sale Order (search, create, confirm)
  6-7:   Invoice (search, details)
  8-10:  Product, Stock, CRM Lead (search)
  11-12: CRM Lead (create), Calendar Event (create)
  13-14: Helpdesk Ticket (create), HR Employee (search)

These tools are instantiated as mcp.tool records in data/default_tools.xml.

Developer notes:
  - All tools have complete input_schema with required/optional fields
  - Descriptions written for AI understanding (mention what agent can do)
  - Tool names follow snake_case convention
  - readonly=True for read operations, False for mutations
  - requires_confirm=True for sensitive operations (create, confirm, etc.)
"""

import json

BUILTIN_TOOLS = [
    # ════════════════════════════════════════════════════════════════
    # SALES & CRM TOOLS
    # ════════════════════════════════════════════════════════════════

    {
        'name': 'partner_search',
        'display_name_label': 'Search Partners',
        'description': 'Search for customers, suppliers, or contacts by name, email, or phone. Returns matching partners with ID, name, email, phone, and website.',
        'tool_type': 'odoo',
        'category_id': False,  # Will be set to Sales & CRM
        'odoo_model': 'res.partner',
        'odoo_method': 'search_read',
        'odoo_domain': '[]',
        'odoo_fields': 'id,name,email,phone,website,country_id',
        'odoo_limit': 10,
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Partner name (partial match)'},
                'email': {'type': 'string', 'description': 'Partner email'},
                'phone': {'type': 'string', 'description': 'Partner phone number'},
                'limit': {'type': 'integer', 'description': 'Max results', 'default': 10},
            },
        }),
        'output_sample': '[{"id": 1, "name": "Acme Inc", "email": "contact@acme.com", "phone": "+1234567890"}]',
    },

    {
        'name': 'partner_create',
        'display_name_label': 'Create Partner',
        'description': 'Create a new customer, supplier, or contact. Requires name and at least email or phone. Returns created partner ID.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'res.partner',
        'odoo_method': 'create',
        'odoo_domain': '[]',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Partner name (required)'},
                'email': {'type': 'string', 'description': 'Email address'},
                'phone': {'type': 'string', 'description': 'Phone number'},
                'website': {'type': 'string', 'description': 'Website URL'},
                'country_id': {'type': 'integer', 'description': 'Country ID'},
            },
            'required': ['name'],
        }),
        'output_sample': '{"id": 42}',
    },

    {
        'name': 'sale_order_search',
        'display_name_label': 'Search Sales Orders',
        'description': 'Search for sales orders by customer name, status, or date. Returns order ID, customer, amount, and state.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'sale.order',
        'odoo_method': 'search_read',
        'odoo_domain': '[]',
        'odoo_fields': 'id,partner_id,amount_total,state,date_order',
        'odoo_limit': 10,
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'partner_name': {'type': 'string', 'description': 'Customer name filter'},
                'state': {'type': 'string', 'description': 'Order state (draft, sent, sale, done, cancel)'},
                'limit': {'type': 'integer', 'default': 10},
            },
        }),
        'output_sample': '[{"id": 1, "partner_id": [1, "Acme"], "amount_total": 1000.0, "state": "sale"}]',
    },

    {
        'name': 'sale_order_create',
        'display_name_label': 'Create Sales Order',
        'description': 'Create a new sales order for a customer. Requires customer ID and order lines with product and quantity.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'sale.order',
        'odoo_method': 'create',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'partner_id': {'type': 'integer', 'description': 'Customer ID (required)'},
                'order_line': {
                    'type': 'array',
                    'description': 'Order lines',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'product_id': {'type': 'integer'},
                            'product_uom_qty': {'type': 'number'},
                            'price_unit': {'type': 'number'},
                        },
                    },
                },
            },
            'required': ['partner_id', 'order_line'],
        }),
        'output_sample': '{"id": 99}',
    },

    {
        'name': 'sale_order_confirm',
        'display_name_label': 'Confirm Sales Order',
        'description': 'Confirm a draft sales order (transition to sale state). Requires order ID.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'sale.order',
        'odoo_method': 'action_confirm',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'id': {'type': 'integer', 'description': 'Sale order ID (required)'},
            },
            'required': ['id'],
        }),
        'output_sample': '{"success": true}',
    },

    {
        'name': 'crm_lead_search',
        'display_name_label': 'Search CRM Leads',
        'description': 'Search for sales leads by name, stage, or probability. Returns lead ID, name, company, and stage.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'crm.lead',
        'odoo_method': 'search_read',
        'odoo_fields': 'id,name,partner_name,stage_id,probability',
        'odoo_limit': 10,
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Lead name filter'},
                'stage_id': {'type': 'integer', 'description': 'Stage ID'},
            },
        }),
        'output_sample': '[{"id": 1, "name": "Tech Corp Lead", "partner_name": "Tech Corp", "stage_id": [1, "Qualified"], "probability": 0.5}]',
    },

    {
        'name': 'crm_lead_create',
        'display_name_label': 'Create CRM Lead',
        'description': 'Create a new sales lead for follow-up. Requires contact name and company name. Sets initial stage to "New".',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'crm.lead',
        'odoo_method': 'create',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Lead name (required)'},
                'partner_name': {'type': 'string', 'description': 'Company name'},
                'email_from': {'type': 'string', 'description': 'Email'},
                'phone': {'type': 'string', 'description': 'Phone'},
            },
            'required': ['name'],
        }),
        'output_sample': '{"id": 42}',
    },

    # ════════════════════════════════════════════════════════════════
    # FINANCE & ACCOUNTING TOOLS
    # ════════════════════════════════════════════════════════════════

    {
        'name': 'invoice_search',
        'display_name_label': 'Search Invoices',
        'description': 'Search for invoices by customer, state (draft, posted, paid), or amount. Returns invoice number, date, amount, and state.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'account.move',
        'odoo_method': 'search_read',
        'odoo_domain': '[["move_type","in",["out_invoice","in_invoice"]]]',
        'odoo_fields': 'id,name,partner_id,amount_total,state,invoice_date',
        'odoo_limit': 10,
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'partner_name': {'type': 'string', 'description': 'Customer name filter'},
                'state': {'type': 'string', 'description': 'Invoice state (draft, posted, paid, canceled)'},
            },
        }),
        'output_sample': '[{"id": 1, "name": "INV-2025-001", "partner_id": [1, "Acme"], "amount_total": 500.0, "state": "posted"}]',
    },

    {
        'name': 'invoice_details',
        'display_name_label': 'Get Invoice Details',
        'description': 'Get full details of an invoice including line items, taxes, and payments.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'account.move',
        'odoo_method': 'read',
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'id': {'type': 'integer', 'description': 'Invoice ID (required)'},
            },
            'required': ['id'],
        }),
        'output_sample': '{"id": 1, "name": "INV-2025-001", "amount_total": 500.0, "invoice_line_ids": [...]}',
    },

    # ════════════════════════════════════════════════════════════════
    # OPERATIONS & INVENTORY TOOLS
    # ════════════════════════════════════════════════════════════════

    {
        'name': 'product_search',
        'display_name_label': 'Search Products',
        'description': 'Search for products by name, category, or code. Returns product ID, name, category, and price.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'product.template',
        'odoo_method': 'search_read',
        'odoo_fields': 'id,name,categ_id,list_price,default_code',
        'odoo_limit': 10,
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Product name (partial match)'},
                'categ_id': {'type': 'integer', 'description': 'Category ID'},
            },
        }),
        'output_sample': '[{"id": 1, "name": "Widget Pro", "list_price": 99.99, "default_code": "WID-001"}]',
    },

    {
        'name': 'stock_quantity_search',
        'display_name_label': 'Check Stock Levels',
        'description': 'Check inventory levels for products across warehouses. Returns product, warehouse, and available quantity.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'stock.quant',
        'odoo_method': 'search_read',
        'odoo_fields': 'id,product_id,location_id,quantity,reserved_quantity',
        'odoo_limit': 20,
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'product_id': {'type': 'integer', 'description': 'Product ID'},
                'location_id': {'type': 'integer', 'description': 'Warehouse/location ID'},
            },
        }),
        'output_sample': '[{"product_id": [1, "Widget"], "location_id": [1, "Stock"], "quantity": 50.0, "reserved_quantity": 5.0}]',
    },

    {
        'name': 'calendar_event_create',
        'display_name_label': 'Create Calendar Event',
        'description': 'Create a calendar event or meeting. Requires event name, start time, and attendees (optional).',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'calendar.event',
        'odoo_method': 'create',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Event name (required)'},
                'start': {'type': 'string', 'description': 'Start datetime (ISO format, required)'},
                'stop': {'type': 'string', 'description': 'End datetime (ISO format, required)'},
                'partner_ids': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'Attendee partner IDs'},
            },
            'required': ['name', 'start', 'stop'],
        }),
        'output_sample': '{"id": 42}',
    },

    # ════════════════════════════════════════════════════════════════
    # HR & SUPPORT TOOLS
    # ════════════════════════════════════════════════════════════════

    {
        'name': 'helpdesk_ticket_create',
        'display_name_label': 'Create Support Ticket',
        'description': 'Create a new helpdesk ticket. Requires ticket name and description. Used for tracking support requests.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'helpdesk.ticket',
        'odoo_method': 'create',
        'is_readonly': False,
        'requires_confirm': True,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Ticket title (required)'},
                'description': {'type': 'string', 'description': 'Ticket description'},
                'partner_id': {'type': 'integer', 'description': 'Customer partner ID'},
            },
            'required': ['name'],
        }),
        'output_sample': '{"id": 42}',
    },

    {
        'name': 'employee_search',
        'display_name_label': 'Search Employees',
        'description': 'Search for employees by name or department. Returns employee ID, name, department, and job title.',
        'tool_type': 'odoo',
        'category_id': False,
        'odoo_model': 'hr.employee',
        'odoo_method': 'search_read',
        'odoo_fields': 'id,name,department_id,job_id,work_email',
        'odoo_limit': 10,
        'is_readonly': True,
        'requires_confirm': False,
        'input_schema': json.dumps({
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Employee name (partial match)'},
                'department_id': {'type': 'integer', 'description': 'Department ID'},
            },
        }),
        'output_sample': '[{"id": 1, "name": "John Smith", "department_id": [1, "Sales"], "job_id": [1, "Sales Manager"]}]',
    },
]
