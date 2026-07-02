# AI Gateway — Prompt Testing Guide

Use this guide to manually test all available Odoo tools directly from the AI Chat Widget. For each tool, we list the intent, sample prompts, and what UI elements/results should render in the chat bubble to confirm the tool is working.

---

## 1. Schema & Model Discovery Tools

### `list_models`
* **Purpose**: List all Odoo models available to the agent.
* **Test Prompts**:
  * *"List all available models in the system"*
  * *"Show me what database tables you can access"*
* **Expected UI**: Renders a list of model badges (`res.partner`, `sale.order`, `res.users`, etc.).

### `get_model_schema`
* **Purpose**: Inspect the fields, labels, types, and relations of a specific model.
* **Test Prompts**:
  * *"Show me the database schema for the partner model"*
  * *"Inspect the res.users fields"*
* **Expected UI**: Renders a tabular field registry showing field names, Types (e.g. `char`, `many2one`), Labels, and Relational models.

---

## 2. Reading & Searching Data Tools

### `search_read`
* **Purpose**: Search for records and retrieve specific columns.
* **Test Prompts**:
  * *"Search res.partner where name is Mitchell Admin and show name, email, and phone"*
  * *"Find all users where login is admin"*
* **Expected UI**: Renders an embedded Odoo Table containing the queried records. Relational name fields (like Many2one) should render as bold, clickable Odoo links.

### `read_record`
* **Purpose**: Read all fields of a specific record by ID.
* **Test Prompts**:
  * *"Show me all details for partner record ID 2"*
  * *"Read the user record with ID 2"*
* **Expected UI**: Renders a key-value detail card of the record.

### `read_group`
* **Purpose**: Group and aggregate records (similar to Odoo pivot/graph views).
* **Test Prompts**:
  * *"Group partners by country and show the count"*
  * *"Summarize sales orders by state"*
* **Expected UI**: Renders an aggregated summary table showing group categories and row counts.

---

## 3. Data Manipulation Tools

### `create_record`
* **Purpose**: Create a single record in a model.
* **Test Prompts**:
  * *"Create a new partner named 'Delta Tech' with email 'delta@example.com'"*
* **Expected UI**: Renders a success badge with the created record ID and a clickable link to open the partner in Odoo.

### `update_record`
* **Purpose**: Update a record by ID.
* **Test Prompts**:
  * *"Update partner ID 2 set email to 'newadmin@example.com'"*
* **Expected UI**: Renders an info badge indicating the record was updated.

### `delete_record`
* **Purpose**: Delete a record by ID.
* **Test Prompts**:
  * *"Delete partner ID 4"*
* **Expected UI**: Renders a warning/trash badge showing that the record has been removed.

### `create_records` / `update_records` / `delete_records` (Bulk)
* **Purpose**: Create, update, or delete multiple records at once.
* **Test Prompts**:
  * *"Create three partners: Alpha (alpha@test.com), Beta (beta@test.com), Gamma (gamma@test.com)"*
  * *"Set email to 'bulk@test.com' for partner IDs [5, 6, 7]"*
* **Expected UI**: Renders a summary badge: *"Created/Updated 3 records in res.partner"*.

---

## 4. Chatter & Attachment Tools

### `post_message`
* **Purpose**: Post a message to a record's chatter.
* **Test Prompts**:
  * *"Post a message saying 'Hello from AI Agent' on partner ID 2"*
* **Expected UI**: Renders a message confirmation badge: *"Message posted on res.partner #2 (Message ID: #X)"*.

### `get_attachments`
* **Purpose**: Retrieve attachments linked to a record.
* **Test Prompts**:
  * *"List all attachments on partner ID 2"*
* **Expected UI**: Renders a list of attachments with download links and file extensions (e.g. PDF, PNG).

### `upload_attachment`
* **Purpose**: Upload a base64 file to a record's chatter.
* **Test Prompts**:
  * *"Upload attachment named 'notes.txt' with content 'Sample notes text' to partner ID 2"*
* **Expected UI**: Renders a success badge with the file name and record ID.

### `read_attachment`
* **Purpose**: Extract text content from an Excel or ODS file.
* **Test Prompts**:
  * *"Read and show the contents of Excel attachment ID 15"*
* **Expected UI**: Renders the Excel file sheets as formatted tables.

### `set_binary_field`
* **Purpose**: Set binary fields (like avatar images) from a URL or base64.
* **Test Prompts**:
  * *"Download image from 'https://example.com/logo.png' and set it as the image_1920 for partner ID 2"*
* **Expected UI**: Renders a success badge displaying the file size.

---

## 5. Specialty Tools

### `accounting_health_summary`
* **Purpose**: Retrieve open and overdue AR/AP summary statistics.
* **Test Prompts**:
  * *"Show me the accounting health summary"*
  * *"What is our current receivables and payables backlog?"*
* **Expected UI**: Renders a table comparing receivables (AR) and payables (AP) open/overdue amounts, complete with currency symbols.

### `create_echart`
* **Purpose**: Run python queries to generate an ECharts dynamic visual chart.
* **Test Prompts**:
  * *"Create an ECharts bar chart named 'Top 3 Partners' showing partner counts"*
* **Expected UI**: Renders a success badge. The chart can then be viewed under the **Charts** menu.

### `execute_orm`
* **Purpose**: Safely execute multi-statement Python code blocks.
* **Test Prompts**:
  * *"Execute ORM python code: return env['res.users'].search_count([])"*
* **Expected UI**: Renders the return value (e.g. `5` or list of values) formatted in a code result block.

### `import_from_file`
* **Purpose**: Bulk import data from a CSV/Excel attachment.
* **Test Prompts**:
  * *"Import data from attachment ID 10 into model res.partner"*
* **Expected UI**: Renders a summary card showing rows created, fields mapped, and any validation warnings.
