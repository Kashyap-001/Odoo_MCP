# Adding Custom Tools

This guide shows how to register new tools for agents to use.

## Overview

Tools fall into three categories:

1. **Odoo Tools** — Execute ORM methods (search_read, create, write, custom actions)
2. **External Tools** — Call HTTP APIs (REST endpoints)
3. **MCP Server Tools** — Call remote MCP servers

## Method 1: UI Tool Creation

### Fastest Path: Tool Scanner Wizard

1. Go to **AI Gateway → Configuration → Tools** (or use menu)
2. Click **Scan Tools** button
3. Optionally filter by module (e.g., "sale", "crm", "hr_employee")
4. Review suggested tools (search_read, create, write, action_* methods)
5. Uncheck already-registered tools
6. Click **Create Selected Tools**

The wizard auto-detects:
- Odoo model methods
- Input fields required
- Read-only vs write operations
- Confirmation requirement

## Method 2: Manual Tool Registration

### Step 1: Create Tool Record

Navigate to **AI Gateway → Configuration → Tools** → **Create**

Fill in:

```
Name:                partner_search
Display Name Label:  Search Partners
Category:            Sales & CRM
Description:         Search for business partners by name/email

Tool Type:           Odoo
Odoo Model:          res.partner
Odoo Method:         search_read
Odoo Fields:         ['id','name','email','phone','country_id']

Is Read-Only:        ✓ (checked)
Requires Confirm:    ☐ (unchecked)
```

### Step 2: Define Input Schema

Paste JSON schema for parameters:

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Partner name to search for (substring match)"
    },
    "email": {
      "type": "string",
      "description": "Email address filter (optional)"
    },
    "limit": {
      "type": "integer",
      "description": "Max results to return",
      "default": 10
    }
  },
  "required": ["name"]
}
```

### Step 3: Add to Agent

Edit agent → **Tools** tab → Add tool to tool set

## Method 3: Programmatic Tool Creation

### Create via Python (for scripts/migrations)

```python
# In custom module or script

env['mcp.tool'].create({
    'name': 'custom_report_generate',
    'display_name_label': 'Generate Custom Report',
    'description': 'Generate monthly sales report',
    'category_id': env['mcp.tool.category'].search([('name','=','Sales & CRM')])[0].id,
    'tool_type': 'odoo',
    'odoo_model': 'sale.order',
    'odoo_method': 'generate_monthly_report',
    'is_readonly': False,
    'requires_confirm': True,
    'input_schema': json.dumps({
        'type': 'object',
        'properties': {
            'year': {'type': 'integer'},
            'month': {'type': 'integer', 'minimum': 1, 'maximum': 12},
        },
        'required': ['year', 'month'],
    }),
})
```

## Tool Types & Examples

### Type 1: Odoo ORM Tools

#### Simple Read Tool (search_read)

```
Name: product_search
Odoo Model: product.product
Odoo Method: search_read
Odoo Fields: ['id','name','default_code','list_price','qty_available']

Input Schema:
{
  "type": "object",
  "properties": {
    "name": {"type": "string", "description": "Product name"},
    "limit": {"type": "integer", "default": 10}
  }
}
```

When called:
```python
env['product.product'].search_read(
    [('name', 'ilike', args['name'])],
    fields=['id','name','default_code','list_price','qty_available'],
    limit=args.get('limit', 10)
)
```

#### Write Tool (create/write)

```
Name: sale_order_create
Odoo Model: sale.order
Odoo Method: create
Requires Confirm: ✓ (important for write ops!)

Input Schema:
{
  "type": "object",
  "properties": {
    "partner_id": {"type": "integer", "description": "Customer ID"},
    "order_line": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "product_id": {"type": "integer"},
          "product_uom_qty": {"type": "number"}
        }
      }
    }
  },
  "required": ["partner_id"]
}
```

When called:
```python
env['sale.order'].create({
    'partner_id': args['partner_id'],
    'order_line': [
        (0, 0, line) for line in args.get('order_line', [])
    ]
})
```

#### Custom Method Tool

```
Name: lead_convert_opportunity
Odoo Model: crm.lead
Odoo Method: convert_opportunity

Input Schema:
{
  "type": "object",
  "properties": {
    "lead_id": {"type": "integer"},
    "customer_id": {"type": "integer"}
  }
}
```

Calls custom method:
```python
lead = env['crm.lead'].browse(args['lead_id'])
lead.convert_opportunity(args['customer_id'])
```

### Type 2: External HTTP Tools

#### REST API Tool (GET)

```
Name: weather_lookup
Display Name: Check Weather
Tool Type: External
External URL: https://api.openweathermap.org/data/2.5/weather?q={city}&appid=API_KEY
External Auth Type: None

Input Schema:
{
  "type": "object",
  "properties": {
    "city": {"type": "string", "description": "City name"}
  },
  "required": ["city"]
}
```

When called:
```python
url = f"https://api.openweathermap.org/data/2.5/weather?q={args['city']}&appid=API_KEY"
response = requests.get(url)
return response.json()
```

#### REST API with Authentication

```
Name: github_repo_search
External URL: https://api.github.com/search/repositories?q={query}
External Auth Type: bearer
External Auth Header: YOUR_GITHUB_TOKEN

Input Schema:
{
  "type": "object",
  "properties": {
    "query": {"type": "string"}
  }
}
```

Headers added:
```python
headers = {'Authorization': 'Bearer YOUR_GITHUB_TOKEN'}
```

#### POST with Body

```
Name: slack_post_message
External URL: https://slack.com/api/chat.postMessage
External Method: POST (inferred if has request_body)

Input Schema:
{
  "type": "object",
  "properties": {
    "channel": {"type": "string"},
    "text": {"type": "string"}
  }
}
```

### Type 3: MCP Server Tools

```
Name: mcp_analytics_query
Tool Type: MCP Server
MCP Server URL: http://localhost:3001

Input Schema:
{
  "type": "object",
  "properties": {
    "query": {"type": "string"},
    "start_date": {"type": "string"},
    "end_date": {"type": "string"}
  }
}
```

Calls:
```
POST http://localhost:3001/call
{
  "tool_name": "mcp_analytics_query",
  "arguments": {
    "query": "...",
    "start_date": "...",
    "end_date": "..."
  }
}
```

## JSON Schema Best Practices

### Required vs Optional

```json
{
  "type": "object",
  "properties": {
    "required_field": {"type": "string"},
    "optional_field": {"type": "string"}
  },
  "required": ["required_field"]  // only required_field is mandatory
}
```

### Field Types

```json
{
  "string": {"type": "string"},
  "integer": {"type": "integer"},
  "number": {"type": "number"},
  "boolean": {"type": "boolean"},
  "date": {"type": "string", "format": "date"},
  "datetime": {"type": "string", "format": "date-time"},
  "enum": {"type": "string", "enum": ["option1", "option2"]},
  "array": {
    "type": "array",
    "items": {"type": "string"}
  },
  "object": {
    "type": "object",
    "properties": {"key": {"type": "string"}}
  }
}
```

### Constraints

```json
{
  "name": {
    "type": "string",
    "minLength": 1,
    "maxLength": 100
  },
  "age": {
    "type": "integer",
    "minimum": 0,
    "maximum": 150
  },
  "quantity": {
    "type": "number",
    "multipleOf": 0.5
  }
}
```

## Access Control

Tools inherit permissions from access rules:

1. **Agent-level access** — User must have permission to use agent
2. **Tool-level access** — User's group must be in tool access rule
3. **Group access** — User must be in allowed group

Example:
```
Access Rule: "Sales Team Tools"
  Group: Sales Team
  Agent IDs: [] (all agents)
  Tool IDs: [partner_search, sale_order_create, invoice_search]
```

Users in "Sales Team" group can now use those 3 tools with any agent.

## Testing Your Tool

### From Chat Interface

1. Create agent with your tool in tool set
2. Open **AI Gateway → Chat**
3. Send message: "Use partner_search tool to find 'John'"
4. Agent should call tool and include result in reply

### Manually

```python
from mcp.tools.dispatcher import ToolDispatcher

tool = env['mcp.tool'].search([('name','=','partner_search')], limit=1)
dispatcher = ToolDispatcher()

result = dispatcher.dispatch(
    tool,
    {'name': 'John', 'limit': 5},
    env,
    env.user
)

print(json.loads(result))
# {
#   'success': True,
#   'result': [
#     {'id': 1, 'name': 'John Doe', 'email': 'john@example.com', ...},
#     ...
#   ]
# }
```

## Troubleshooting

### Tool Not Appearing in Chat

- [ ] Tool `active` is True
- [ ] User has access (check mcp.access.rule)
- [ ] Tool added to agent's tool set
- [ ] Refresh page/clear cache

### Tool Execution Fails

Check session messages:
1. Go to Session record
2. Scroll to Messages tab
3. Find tool_call message
4. Find corresponding tool_result message
5. Check error in result

### JSON Schema Validation Error

Validate schema:
```python
import json
schema = json.loads(tool.input_schema)
# Should not raise
```

Common mistakes:
- Missing `"type": "object"` at top level
- Typo in `"properties"`
- Invalid JSON (unquoted keys)
