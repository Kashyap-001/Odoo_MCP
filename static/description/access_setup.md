# AI Gateway — Access Control Setup Guide

## The 3 Roles

| Role | Menu Access | Tools | Sessions |
|---|---|---|---|
| **AI Gateway User** | Chat only | Read-only tools (9 built-in) | Own sessions only |
| **AI Gateway Manager** | Chat, Agents, Tools, Sessions, Cost Analysis, Issues | All tools | All sessions |
| **AI Gateway Administrator** | Everything including Configuration | All tools | All sessions |

Groups are hierarchical: Admin inherits Manager, Manager inherits User.

---

## Step 1 — Assign a Role to a User

1. Go to **Settings → Users & Companies → Users**
2. Open the user's profile
3. In the **Access Rights** section, find the **AI Gateway** category
4. Select one of: `AI Gateway User` / `AI Gateway Manager` / `AI Gateway Administrator`
5. Save

A user with **no AI Gateway role** will not see the "AI Gateway" menu at all.

---

## Step 2 — Configure Access Rules

Access rules control which **agents** and **tools** a user can interact with. Two default rules are pre-installed:

| Rule | Who | Tools |
|---|---|---|
| AI Gateway User Access | AI Gateway User group | 9 read-only tools |
| AI Gateway Manager Access | AI Gateway Manager group | All tools |

To create a custom rule:

1. Go to **AI Gateway → Configuration → Access Rules**
2. Click **New**
3. Fill in:
   - **Name** — e.g. "Sales Team Access"
   - **Who tab** — pick one or more **Groups** and/or specific **Users**
   - **What tab** — optionally restrict to specific **Agents** and/or **Tools**
     - Leave empty = all agents / all tools allowed
   - **Permissions & Limits tab** — toggle session history view, export, and daily/monthly caps
4. Save

Multiple rules are merged with **OR logic** — if a user matches more than one rule, they get the union of all permissions.

---

## Tool Categories (for building custom rules)

| Tool | Category | Mutates data? |
|---|---|---|
| list_models, get_model_schema | Read | No |
| search_read, read_record, read_group | Read | No |
| get_attachments, read_attachment | Read | No |
| lookup_model_history, accounting_health_summary | Read | No |
| create_record, update_record, delete_record | Write | **Yes** |
| create_records, update_records, delete_records | Write | **Yes** |
| post_message, upload_attachment, set_binary_field | Write | **Yes** |
| import_from_file | Write | **Yes** |
| execute_method, execute_orm | Advanced/Sandbox | **Yes** |
| create_echart, ai_agent_query | Advanced | Yes |

Recommended policy: give **Users** only the Read-only tools. Give **Managers** everything. Reserve `execute_orm` and `execute_method` for admins or trusted users.

---

## Rate Limiting

- **Daily Rate Limit** — max chat sessions per 24 hours (0 = unlimited)
- **Monthly Rate Limit** — max chat sessions per 30 days (0 = unlimited)

If a user matches multiple rules, the **highest (most permissive) limit wins**.

---

## Troubleshooting

**"User can't see the AI Gateway menu"**
→ Make sure the user is assigned `AI Gateway User` (or higher) in Settings → Users.

**"User gets Access Denied when chatting"**
→ No access rule matches this user. Create a rule that includes their group or user directly.

**"User can see tool X in the AI chat but gets an error executing it"**
→ The tool is in the agent's tool list but blocked by the user's access rule. Add the tool to their rule, or remove it from the agent's Tool Sets.

**"Manager can't see all sessions"**
→ Ensure the user has `AI Gateway Manager` group, not just `AI Gateway User`.

**"Admin changed a default rule but upgrade reset it"**
→ Default rules use `noupdate="1"` — upgrades never overwrite them. Changes are permanent unless manually reset.
