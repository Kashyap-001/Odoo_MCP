import { Component, useState, useRef, onMounted, onPatched, onWillUnmount, markup } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { ErrorHandler } from "@web/core/utils/components";

export class ChatWidget extends Component {
    static template = "mcp_gateway.ChatWidget";

    setup() {
        this.state = useState({
            agents: [],
            recentSessions: [],
            selectedAgent: null,
            messages: [],
            loading: false,
            isTyping: false,
            totalTokens: 0,
            estimatedCost: 0,
            sessionId: null,
            sidebarOpen: true,
            collapsedAgents: {},
            pendingFile: null,
            templates: [],
            showTemplates: false,
            searchQuery: "",
            searchResults: null,
            messageRenderErrors: new Set(),
        });

        this._sendSeq = 0;
        this._searchTimer = null;

        this.messageInput = useRef("messageInput");
        this.fileInput = useRef("fileInput");
        this.chatHistory = useRef("chatHistory");
        this.notificationService = useService("notification");
        this.orm = useService("orm");
        this.actionService = useService("action");

        onMounted(() => {
            this.loadAgents();
            this.loadRecentSessions();
            this.loadCompanyCurrency();
            // Event delegation for copy buttons injected by _renderMarkdown
            this.chatHistory.el?.addEventListener('click', this._onChatClick.bind(this));
        });
    }

    async loadAgents() {
        try {
            const response = await this.orm.searchRead(
                "mcp.agent",
                [["active", "=", true]],
                ["id", "name", "provider", "model_name", "status", "session_count", "color", "total_tokens", "total_cost_usd", "avatar"]
            );
            this.state.agents = response;
        } catch (error) {
            console.error("MCP: Failed to load agents", error);
        }
    }

    async loadRecentSessions() {
        try {
            const sessions = await this.orm.searchRead(
                "mcp.session",
                [["user_id", "=", user.userId]],
                ["id", "name", "agent_id", "create_date", "state", "is_pinned"],
                { limit: 20, order: "create_date desc" }
            );
            this.state.recentSessions = await this._attachPreviews(sessions);
        } catch (error) {
            console.error("MCP: Failed to load sessions", error);
        }
    }

    async _attachPreviews(sessions) {
        if (sessions.length === 0) {
            return sessions;
        }
        const sessionIds = sessions.map(s => s.id);
        // Get the single latest message id PER session via read_group, not a
        // shared global `limit` searchRead — a shared limit across all
        // sessionIds starves older/quieter sessions out of the top-N pool
        // entirely once total message volume grows past the limit, silently
        // dropping them from the sidebar (they get filtered out below since
        // they'd never get a msgMap entry).
        const groups = await this.orm.readGroup(
            "mcp.session.message",
            [["session_id", "in", sessionIds], ["role", "in", ["user", "assistant"]]],
            ["id:max"],
            ["session_id"]
        );
        const latestIds = groups.map(g => g.id).filter(Boolean);
        const msgMap = {};
        if (latestIds.length) {
            const lastMessages = await this.orm.searchRead(
                "mcp.session.message",
                [["id", "in", latestIds]],
                ["session_id", "content"],
            );
            for (const msg of lastMessages) {
                const sid = msg.session_id[0];
                const text = msg.content || '';
                const isStructured = text.charAt(0) === '{' && (text.includes('"_type"') || text.includes('"_is_structured"'));
                msgMap[sid] = isStructured
                    ? '[Tool result]'
                    : text.substring(0, 60) + (text.length > 60 ? '…' : '');
            }
        }
        return sessions
            .filter(s => msgMap[s.id])
            .map(s => ({ ...s, lastMessage: msgMap[s.id] }));
    }

    onSessionSearchInput(ev) {
        const query = ev.target.value;
        this.state.searchQuery = query;
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => this.searchSessions(query.trim()), 300);
    }

    clearSessionSearch() {
        this.state.searchQuery = "";
        this.state.searchResults = null;
        clearTimeout(this._searchTimer);
    }

    async searchSessions(query) {
        if (!query) {
            this.state.searchResults = null;
            return;
        }
        try {
            const sessions = await this.orm.searchRead(
                "mcp.session",
                ["&", ["user_id", "=", user.userId], "|", ["name", "ilike", query], ["session_message_ids.content", "ilike", query]],
                ["id", "name", "agent_id", "create_date", "state", "is_pinned"],
                { order: "create_date desc", limit: 50 }
            );
            this.state.searchResults = await this._attachPreviews(sessions);
        } catch (error) {
            console.error("MCP: Failed to search sessions", error);
        }
    }

    toggleAgentGroup(agentId) {
        this.state.collapsedAgents = {
            ...this.state.collapsedAgents,
            [agentId]: !this.state.collapsedAgents[agentId],
        };
    }

    get sessionsByAgent() {
        const groups = {};
        const sessions = this.state.searchResults ?? this.state.recentSessions;
        for (const session of sessions) {
            const agentId = Array.isArray(session.agent_id) ? session.agent_id[0] : session.agent_id;
            const agentName = Array.isArray(session.agent_id) ? session.agent_id[1] : 'Unknown Agent';
            if (!groups[agentId]) {
                const agent = this.state.agents.find(a => a.id === agentId);
                groups[agentId] = {
                    id: agentId,
                    name: agentName,
                    avatar: agent ? agent.avatar : false,
                    sessions: []
                };
            }
            groups[agentId].sessions.push(session);
        }
        const results = Object.values(groups);
        for (const group of results) {
            group.sessions.sort((a, b) => {
                if (a.is_pinned && !b.is_pinned) return -1;
                if (!a.is_pinned && b.is_pinned) return 1;
                return 0;
            });
        }
        return results;
    }

    selectAgent(agent) {
        this._sendSeq++;
        this.state.selectedAgent = agent;
        this.state.messages = [];
        this.state.totalTokens = 0;
        this.state.estimatedCost = 0;
        this.state.sessionId = null;
        this.state.showTemplates = false;
        if (window.innerWidth < 768) this.state.sidebarOpen = false;
        this.loadTemplates(agent.id);
    }

    async loadTemplates(agentId) {
        try {
            this.state.templates = await this.orm.searchRead(
                "mcp.prompt.template",
                [["active", "=", true], "|", ["is_global", "=", true], ["agent_id", "=", agentId]],
                ["id", "name", "content", "category", "variables"],
                { order: "sequence asc" }
            );
        } catch (e) {
            console.error("MCP: Failed to load templates", e);
        }
    }

    toggleTemplates() {
        this.state.showTemplates = !this.state.showTemplates;
    }

    insertTemplate(template) {
        const el = this.messageInput.el;
        if (!el) return;
        el.value = template.content;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        const match = template.content.match(/\{(\w+)\}/);
        if (match) {
            const start = template.content.indexOf(match[0]);
            el.setSelectionRange(start, start + match[0].length);
        }
        el.focus();
        this.state.showTemplates = false;
    }

    async loadSessionMessages(sessionId) {
        const messages = await this.orm.searchRead(
            "mcp.session.message",
            [["session_id", "=", sessionId], ["role", "in", ["user", "assistant"]]],
            ["role", "content", "tool_name", "create_date"],
            { order: "create_date asc, id asc" }
        );
        this.state.messages = messages.map(msg => {
            if (msg.role === 'user') {
                const { cleanContent, attachmentChip } = this._parseUserMessage(msg.content);
                return { ...msg, content: cleanContent, attachmentChip };
            }
            const { parsedContent, structuredData } = this._parseContent(msg.content);
            return { ...msg, content: parsedContent, structuredData };
        });
    }

    async selectSession(session) {
        this._sendSeq++;
        this.state.loading = true;
        try {
            const agent = this.state.agents.find(a => a.id === session.agent_id[0]);
            this.state.selectedAgent = agent || { id: session.agent_id[0], name: session.agent_id[1] };
            this.state.sessionId = session.id;
            this.loadTemplates(session.agent_id[0]);

            await this.loadSessionMessages(session.id);

            const sessionData = await this.orm.read("mcp.session", [session.id], ["input_tokens", "output_tokens", "estimated_cost_usd"]);
            if (sessionData.length > 0) {
                this.state.totalTokens = sessionData[0].input_tokens + sessionData[0].output_tokens;
                this.state.estimatedCost = sessionData[0].estimated_cost_usd;
            }
        } catch (error) {
            console.error("Failed to select session:", error);
        } finally {
            this.state.loading = false;
            this._scrollToBottom();
        }
    }

    async togglePinSession(session) {
        try {
            const newPinned = !session.is_pinned;
            await this.orm.write("mcp.session", [session.id], { is_pinned: newPinned });
            session.is_pinned = newPinned;
            this._refreshSessionLists();
        } catch (error) {
            console.error("MCP: Failed to pin/unpin session", error);
        }
    }

    async renameSession(session) {
        const newName = window.prompt("Rename session", session.name);
        if (!newName || newName === session.name) {
            return;
        }
        try {
            await this.orm.write("mcp.session", [session.id], { name: newName });
            session.name = newName;
            this._refreshSessionLists();
        } catch (error) {
            console.error("MCP: Failed to rename session", error);
        }
    }

    async exportSession(session) {
        try {
            const action = await this.orm.call("mcp.session", "action_export_transcript", [session.id]);
            this.actionService.doAction(action);
        } catch (error) {
            console.error("MCP: Failed to export session", error);
        }
    }

    async deleteSession(session) {
        if (!window.confirm(`Delete "${session.name}"? This cannot be undone.`)) {
            return;
        }
        try {
            await this.orm.unlink("mcp.session", [session.id]);
            this.state.recentSessions = this.state.recentSessions.filter(s => s.id !== session.id);
            if (this.state.searchResults) {
                this.state.searchResults = this.state.searchResults.filter(s => s.id !== session.id);
            }
            if (this.state.sessionId === session.id) {
                this.state.sessionId = null;
                this.state.messages = [];
            }
        } catch (error) {
            console.error("MCP: Failed to delete session", error);
        }
    }

    // Force rerender of session lists after an in-place mutation (OWL reactivity
    // doesn't pick up writes to nested object properties).
    _refreshSessionLists() {
        this.state.recentSessions = [...this.state.recentSessions];
        if (this.state.searchResults) {
            this.state.searchResults = [...this.state.searchResults];
        }
    }

    async loadCompanyCurrency() {
        try {
            const companies = await this.orm.searchRead(
                "res.company",
                [["id", "=", user.companyId]],
                ["currency_id"]
            );
            if (companies.length > 0 && companies[0].currency_id) {
                const currencyId = companies[0].currency_id[0];
                const currencies = await this.orm.searchRead(
                    "res.currency",
                    [["id", "=", currencyId]],
                    ["symbol"]
                );
                if (currencies.length > 0) {
                    this.state.companyCurrencySymbol = currencies[0].symbol;
                }
            }
        } catch (e) {
            this.state.companyCurrencySymbol = "$";
        }
    }

    newChat() {
        this._sendSeq++;
        this.state.selectedAgent = null;
        this.state.sessionId = null;
        this.state.messages = [];
        this.state.sidebarOpen = true;
    }

    toggleSidebar() {
        this.state.sidebarOpen = !this.state.sidebarOpen;
    }

    onAttachClick() {
        this.fileInput.el?.click();
    }

    async onFileChange(ev) {
        const file = ev.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async (e) => {
            const dataUrl = e.target.result;
            const base64 = dataUrl.split(',')[1];
            try {
                const resp = await rpc("/mcp/attachment/stage", {
                    filename: file.name,
                    mimetype: file.type || 'application/octet-stream',
                    datas: base64,
                });
                if (resp.status === 'success') {
                    this.state.pendingFile = { id: resp.data.id, name: resp.data.name, mimetype: resp.data.mimetype || file.type || '' };
                } else {
                    this.notificationService.add('File upload failed: ' + resp.error, { type: 'danger' });
                }
            } catch (err) {
                this.notificationService.add('File upload failed', { type: 'danger' });
            }
        };
        reader.readAsDataURL(file);
        ev.target.value = '';
    }

    async sendMessage() {
        const message = this.messageInput.el?.value?.trim();
        if (!message && !this.state.pendingFile || !this.state.selectedAgent) return;
        if (!message && this.state.pendingFile) {
            this.notificationService.add('Add a message to send with the file', { type: 'warning' });
            return;
        }

        const mySeq = ++this._sendSeq;
        const stagedFileId = this.state.pendingFile?.id || null;
        const pendingFileName = this.state.pendingFile?.name || null;
        const pendingFileMime = this.state.pendingFile?.mimetype || '';
        this.state.pendingFile = null;

        this.state.loading = true;
        this.state.isTyping = true;
        const _liveIsSheet = ['application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.oasis.opendocument.spreadsheet'].includes(pendingFileMime);
        const _liveIsImg = pendingFileMime.startsWith('image/');
        const _liveIsPdf = pendingFileMime === 'application/pdf';
        const _liveChip = pendingFileName ? {
            name: pendingFileName,
            mimetype: pendingFileMime,
            isSpreadsheet: _liveIsSheet,
            isImage: _liveIsImg,
            isPdf: _liveIsPdf,
            typeLabel: _liveIsSheet ? 'Spreadsheet' : _liveIsPdf ? 'PDF' : _liveIsImg ? 'Image' : 'File',
            iconType: _liveIsSheet ? 'sheet' : _liveIsPdf ? 'pdf' : _liveIsImg ? 'image' : 'file',
            ext: pendingFileName.includes('.') ? pendingFileName.split('.').pop().toUpperCase().slice(0, 5) : 'FILE',
            attachmentId: stagedFileId,
        } : null;
        this.state.messages.push({ role: "user", content: message, attachmentChip: _liveChip });
        this.messageInput.el.value = "";
        this.messageInput.el.style.height = "auto";

        try {
            const response = await rpc("/mcp/chat", {
                agent_id: this.state.selectedAgent.id,
                message: message,
                session_id: this.state.sessionId,
                staged_attachment_id: stagedFileId,
            });

            if (this._sendSeq !== mySeq) {
                this.loadRecentSessions();
                return;
            }

            if (response.status === 'error') {
                this.state.messages.push({
                    role: 'error',
                    content: response.error || 'An unexpected error occurred',
                });
                this.loadRecentSessions();
                return;
            }

            const result = response.data;
            this.state.sessionId = result.session_id;
            await this.loadSessionMessages(result.session_id);

            this.state.totalTokens += result.input_tokens + result.output_tokens;
            this.state.estimatedCost += result.cost_usd;

            this.loadRecentSessions();
        } catch (error) {
            if (this._sendSeq === mySeq) {
                this.state.messages.push({
                    role: 'error',
                    content: error.message || 'Failed to send message. Check your connection.',
                });
            }
        } finally {
            this.state.loading = false;
            this.state.isTyping = false;
            if (this._sendSeq === mySeq) this._scrollToBottom();
        }
    }

    _scrollToBottom() {
        setTimeout(() => {
            requestAnimationFrame(() => {
                const el = this.chatHistory.el;
                if (el) el.scrollTop = el.scrollHeight;
            });
        }, 100);
    }

    // ── Copy button handler (event delegation) ──────────────────────────────

    _onChatClick(e) {
        const btn = e.target.closest('.mcp-copy-btn');
        if (btn) {
            const code = btn.closest('pre')?.querySelector('code')?.textContent || '';
            const doFallback = () => {
                const ta = document.createElement('textarea');
                ta.value = code;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
            };
            (navigator.clipboard ? navigator.clipboard.writeText(code).catch(doFallback) : Promise.resolve(doFallback()))
                .then(() => {
                    btn.classList.add('mcp-copy-done');
                    setTimeout(() => btn.classList.remove('mcp-copy-done'), 1500);
                });
            return;
        }

        const link = e.target.closest('.mcp-record-link');
        if (link) {
            e.preventDefault();
            const model = link.dataset.model;
            const id = parseInt(link.dataset.id, 10);
            const name = link.dataset.name || link.innerText.trim();
            if (model && this.actionService) {
                if (!isNaN(id)) {
                    this.actionService.doAction({
                        type: "ir.actions.act_window",
                        res_model: model,
                        res_id: id,
                        views: [[false, "form"]],
                        target: "current",
                    });
                } else if (name) {
                    this.orm.searchRead(model, ["|", ["name", "=", name], ["display_name", "=", name]], ["id"], { limit: 1 })
                        .then(res => {
                            if (res.length > 0) {
                                this.actionService.doAction({
                                    type: "ir.actions.act_window",
                                    res_model: model,
                                    res_id: res[0].id,
                                    views: [[false, "form"]],
                                    target: "current",
                                });
                            }
                        });
                }
            }
            return;
        }

        if (this.state.showTemplates &&
            !e.target.closest('.mcp-templates-panel') &&
            !e.target.closest('.mcp-templates-btn')) {
            this.state.showTemplates = false;
        }

        const chip = e.target.closest('.mcp-suggestion-chip');
        if (chip) {
            e.preventDefault();
            const text = chip.dataset.text || chip.innerText.trim();
            if (this.messageInput.el) {
                this.messageInput.el.value = text;
                this.messageInput.el.dispatchEvent(new Event('input', { bubbles: true }));
                this.sendMessage();
            }
            return;
        }
    }

    // ── Markdown renderer ───────────────────────────────────────────────────

    _escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // Mirrors gateway.py's _repair_broken_content_json — best-effort repair for
    // a {"_type": "...", "content": "..."} reply whose content string contains
    // a literal, unescaped quote (a common LLM mistake). Only handles this
    // project's own single-trailing-"content"-field shape, not general JSON.
    // Client-side copy needed because the server-side fix only prevents new
    // occurrences — it can't retroactively repair messages already stored.
    _repairBrokenContentJson(text) {
        const head = text.match(/^\s*\{\s*"_type"\s*:\s*"(\w+)"\s*,\s*"content"\s*:\s*"/);
        if (!head) return null;
        const rest = text.slice(head[0].length);
        const tail = rest.match(/"\s*\}\s*$/);
        if (!tail) return null;
        const rawContent = rest.slice(0, tail.index);
        const neutralized = rawContent.replace(/(?<!\\)"/g, '\\"');
        try {
            return { _type: head[1], content: JSON.parse('"' + neutralized + '"') };
        } catch (e) {
            return { _type: head[1], content: rawContent };
        }
    }

    _inlineFormat(str) {
        let res = str
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
            .replace(/`([^`\n]+)`/g, '<code class="mcp-md-inline">$1</code>');

        // Match standard markdown links and parse Odoo record links/custom protocols
        res = res.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label, url) => {
            const modelMatch = url.match(/[#&](?:amp;)?model=([\w.]+)/);
            const idMatch = url.match(/[#&](?:amp;)?id=(\d+)/);
            if (modelMatch && idMatch) {
                return `<a href="#" class="mcp-record-link text-decoration-none fw-bold" style="color: var(--mcp-primary, #714B67);" data-model="${modelMatch[1]}" data-id="${idMatch[2]}">${label}</a>`;
            }
            const protoMatch = url.match(/^(?:odoo|mcp):\/\/([\w.]+)\/(\d+)/);
            if (protoMatch) {
                return `<a href="#" class="mcp-record-link text-decoration-none fw-bold" style="color: var(--mcp-primary, #714B67);" data-model="${protoMatch[1]}" data-id="${protoMatch[2]}">${label}</a>`;
            }
            const finalUrl = url.startsWith('/') ? url : (url.match(/^https?:\/\//) ? url : 'https://' + url);
            return `<a href="${finalUrl}" target="_blank" rel="noopener noreferrer">${label}</a>`;
        });
        return res;
    }

    _renderMarkdown(text) {
        const lines = text.split('\n');
        const out = [];
        let inCode = false;
        let codeLines = [];
        let codeLang = '';
        let inList = false;
        let listOrdered = false;
        let inTable = false;
        let tableHeaders = [];
        let tableRows = [];

        const closeList = () => {
            if (inList) { out.push(listOrdered ? '</ol>' : '</ul>'); inList = false; }
        };

        const closeTable = () => {
            if (inTable) {
                let html = '<div class="mcp-result-scroll my-2"><table class="table table-sm table-bordered mcp-result-table"><thead><tr>';
                for (const h of tableHeaders) {
                    html += `<th>${this._inlineFormat(this._escapeHtml(h.trim()))}</th>`;
                }
                html += '</tr></thead><tbody>';
                for (const row of tableRows) {
                    html += '<tr>';
                    for (let i = 0; i < row.length; i++) {
                        const cell = row[i];
                        const header = tableHeaders[i] || '';
                        html += `<td>${this._inlineFormat(this._formatTableCell(cell, header))}</td>`;
                    }
                    html += '</tr>';
                }
                html += '</tbody></table></div>';
                out.push(html);
                inTable = false;
                tableHeaders = [];
                tableRows = [];
            }
        };

        for (const raw of lines) {
            const fenceMatch = raw.match(/^```(\w*)$/);
            if (fenceMatch) {
                if (!inCode) {
                    closeList();
                    closeTable();
                    inCode = true; codeLang = fenceMatch[1] || ''; codeLines = [];
                } else {
                    const escaped = codeLines.join('\n')
                        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    const langAttr = codeLang ? ` data-lang="${codeLang}"` : '';
                    out.push(`<pre class="mcp-md-code"${langAttr}><button class="mcp-copy-btn" title="Copy"><i class="fa fa-copy"></i></button><code>${escaped}</code></pre>`);
                    inCode = false; codeLines = []; codeLang = '';
                }
                continue;
            }
            if (inCode) { codeLines.push(raw); continue; }

            // Table check
            const isTableLine = raw.trim().startsWith('|') && raw.trim().endsWith('|');
            if (isTableLine) {
                closeList();
                const cells = raw.trim().split('|').slice(1, -1);
                if (raw.match(/^\|\s*[-:\s|]+\s*\|$/)) {
                    continue;
                }
                if (!inTable) {
                    inTable = true;
                    tableHeaders = cells;
                    tableRows = [];
                } else {
                    tableRows.push(cells);
                }
                continue;
            } else {
                closeTable();
            }

            const line = this._escapeHtml(raw);

            if (line.startsWith('### ')) { closeList(); out.push(`<h5 class="mcp-md-h">${this._inlineFormat(line.slice(4))}</h5>`); continue; }
            if (line.startsWith('## '))  { closeList(); out.push(`<h4 class="mcp-md-h">${this._inlineFormat(line.slice(3))}</h4>`); continue; }
            if (line.startsWith('# '))   { closeList(); out.push(`<h3 class="mcp-md-h">${this._inlineFormat(line.slice(2))}</h3>`); continue; }

            if (line.match(/^-{3,}$/) || line.match(/^\*{3,}$/)) { closeList(); out.push('<hr class="mcp-md-hr">'); continue; }

            const ulMatch = line.match(/^[-*] (.+)$/);
            if (ulMatch) {
                if (!inList || listOrdered) { if (inList) out.push('</ol>'); out.push('<ul class="mcp-md-ul">'); inList = true; listOrdered = false; }
                out.push(`<li>${this._inlineFormat(ulMatch[1])}</li>`);
                continue;
            }

            const olMatch = line.match(/^\d+\. (.+)$/);
            if (olMatch) {
                if (!inList || !listOrdered) { if (inList) out.push('</ul>'); out.push('<ol class="mcp-md-ul">'); inList = true; listOrdered = true; }
                out.push(`<li>${this._inlineFormat(olMatch[1])}</li>`);
                continue;
            }

            if (line.trim() === '') { closeList(); out.push('<br>'); continue; }

            out.push(`<p class="mcp-md-p">${this._inlineFormat(line)}</p>`);
        }

        if (inCode) {
            const escaped = codeLines.join('\n').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            out.push(`<pre class="mcp-md-code"><button class="mcp-copy-btn" title="Copy"><i class="fa fa-copy"></i></button><code>${escaped}</code></pre>`);
        }
        closeList();
        closeTable();
        return out.join('');
    }

    // Render markdown content as safe markup — used by text type so **bold** and |tables| display correctly
    _md(content) {
        return markup(this._renderMarkdown(content || ''));
    }

    // _type:'html' content is already run through Odoo's html_sanitize server-side
    // before storage (gateway.py's _sanitize_html_blocks) — wrap in markup() here
    // so it actually renders as HTML instead of being escaped a second time by t-out.
    _html(content) {
        return markup(content || '');
    }

    // One malformed message (bad structuredData, a chart+attachment combo that
    // trips an OWL edge case, etc.) must not take down the whole session's message
    // list — each bubble is wrapped in an ErrorHandler that redirects here instead
    // of letting the render exception propagate up through the shared template.
    _onMessageRenderError(index, error) {
        console.error('Failed to render chat message at index', index, error);
        this.state.messageRenderErrors.add(index);
    }

    // Full icon class string for the attachment card — one branch per mimetype family.
    _attachmentIconClass(mimetype) {
        let kind = 'fa-file-word-o text-primary';
        if (mimetype === 'application/pdf') kind = 'fa-file-pdf-o text-danger';
        else if (mimetype === 'text/csv') kind = 'fa-file-text-o text-success';
        else if (mimetype.includes('spreadsheet') || mimetype.includes('excel')) kind = 'fa-file-excel-o text-success';
        return `fa ${kind} fa-2x mcp-ai-attachment-icon`;
    }

    // Short filetype label for the attachment card's meta line (icon already conveys the rest).
    _attachmentTypeLabel(mimetype) {
        if (mimetype === 'application/pdf') return 'PDF';
        if (mimetype === 'text/csv') return 'CSV';
        if (mimetype.includes('spreadsheet') || mimetype.includes('excel')) return 'Excel';
        return 'File';
    }

    // /web/content/<id>?download=true forces Content-Disposition: attachment,
    // which browsers refuse to render inline (blank iframe, forced download even
    // with target="_blank"). Strip it for anything meant to be viewed, not saved.
    _attachmentViewUrl(url) {
        return url.replace('?download=true', '');
    }

    _formatBytes(bytes) {
        if (!bytes && bytes !== 0) return '';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    // Odoo's /web/image/<model>/<id>/<field> URLs are the same for every write —
    // browsers cache them by URL, so redisplaying one right after an update (e.g.
    // set_binary_field) can show the stale pre-update image. msgId (the session
    // message's own DB id) is unique per assistant turn, so tagging the URL with
    // it forces a fresh fetch after any write without refetching on every re-render
    // of the SAME already-loaded message.
    _cacheBustUrl(url, msgId) {
        if (!url || !msgId) return url;
        const sep = url.includes('?') ? '&' : '?';
        return `${url}${sep}_cb=${msgId}`;
    }

    _formatTableCell(cell, header) {
        const clean = cell.replace(/^\*\*|^\*|^\`|\*\*$|\*$|\`$/g, '').trim();
        const headerLower = header ? header.trim().toLowerCase() : '';
        const cleanLower = clean.toLowerCase();
        
        // 1. Selection / Status / Stage checks
        const knownStates = ['draft', 'sale', 'done', 'cancel', 'cancelled', 'posted', 'paid', 'confirm', 'refused', 'new', 'open', 'sent', 'error', 'failed'];
        if (knownStates.some(s => cleanLower.includes(s)) || ['state', 'status', 'stage'].includes(headerLower)) {
            const isSuccess = ['done','paid','posted','sale','open','confirm','sent'].some(s => cleanLower.includes(s));
            const isDanger = ['cancel','cancelled','refused','failed','error'].some(s => cleanLower.includes(s));
            const isSecondary = ['draft','new'].some(s => cleanLower.includes(s));
            const bgClass = isSuccess ? 'bg-success' : (isDanger ? 'bg-danger' : (isSecondary ? 'bg-secondary' : 'bg-primary'));
            return `<span class="badge ${bgClass}">${this._escapeHtml(clean)}</span>`;
        }

        // 2. Bold red text for errors/failures
        if (cleanLower.includes('error') || cleanLower.includes('failed') || cleanLower.includes('exception')) {
            return `<span class="text-danger fw-bold">${this._escapeHtml(clean)}</span>`;
        }

        // 3. Boolean checks
        if (cleanLower === 'true' || cleanLower === 'yes' || clean === '✓') {
            return '<span class="text-success fw-bold">✓</span>';
        }
        if (cleanLower === 'false' || cleanLower === 'no' || clean === '—') {
            return '<span class="text-muted">—</span>';
        }

        return this._escapeHtml(clean);
    }

    // ── Other helpers ────────────────────────────────────────────────────────

    _formatCellValue(value, fieldMeta, record) {
        let type = 'char';
        let currencyField = null;
        if (typeof fieldMeta === 'string') {
            const fieldName = fieldMeta.toLowerCase();
            if (fieldName.includes('amount') || fieldName.includes('price') || fieldName.includes('total') || fieldName.includes('subtotal') || fieldName.includes('residual')) {
                type = 'monetary';
            } else if (fieldName.includes('date')) {
                type = 'date';
            } else if (fieldName.includes('count') || fieldName.includes('qty') || fieldName.includes('quantity')) {
                type = 'integer';
            }
        } else if (fieldMeta && typeof fieldMeta === 'object') {
            type = fieldMeta.type || 'char';
            currencyField = fieldMeta.currency_field;
        }

        if (value === null || value === undefined || (value === false && type !== 'boolean')) return '—';

        // 1. Binary check (images)
        if (type === 'binary' || (typeof value === 'string' && value.startsWith('/web/image'))) {
            if (typeof value === 'string' && value.startsWith('/')) {
                return markup(`<img src="${this._escapeHtml(value)}" style="height:40px;width:40px;object-fit:contain;border-radius:4px;"/>`);
            }
        }

        // 2. Boolean check
        if (type === 'boolean' || typeof value === 'boolean') {
            return value ? markup('<span class="text-success fw-bold">✓</span>') : markup('<span class="text-muted">—</span>');
        }

        // 3. Selection/Status/Stage check
        const valStr = String(value);
        const valStrLower = valStr.toLowerCase();
        const knownStates = ['draft', 'sale', 'done', 'cancel', 'cancelled', 'posted', 'paid', 'confirm', 'refused', 'new', 'open', 'error', 'failed'];
        if (type === 'selection' || knownStates.includes(valStrLower) || (typeof fieldMeta === 'string' && ['state', 'status', 'stage'].includes(fieldMeta.toLowerCase()))) {
            const isSuccess = ['done','paid','posted','sale','open','confirm'].includes(valStrLower);
            const isDanger = ['cancel','cancelled','refused','failed','error'].includes(valStrLower);
            const isSecondary = ['draft','new'].includes(valStrLower);
            const bgClass = isSuccess ? 'bg-success' : (isDanger ? 'bg-danger' : (isSecondary ? 'bg-secondary' : 'bg-primary'));
            return markup(`<span class="badge ${bgClass}">${this._escapeHtml(valStr)}</span>`);
        }

        // 4. Red text for errors/failures
        if (typeof value === 'string' && (valStrLower.includes('error') || valStrLower.includes('failed') || valStrLower.includes('exception'))) {
            return markup(`<span class="text-danger fw-bold">${this._escapeHtml(value)}</span>`);
        }

        // 5. Date / DateTime formatting
        if (type === 'date' && typeof value === 'string')
            return new Date(value).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
        if (type === 'datetime' && typeof value === 'string')
            return new Date(value).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

        // 6. Monetary / Float formatting
        if ((type === 'monetary' || type === 'float') && typeof value === 'number') {
            let symbol = '';
            if (currencyField && record?.[currencyField]) {
                const name = Array.isArray(record[currencyField]) ? record[currencyField][1] : record[currencyField];
                const symbols = { USD: '$', EUR: '€', GBP: '£', INR: '₹', JPY: '¥', AED: 'د.إ' };
                symbol = symbols[name] || (name + ' ');
            } else if (this.state.companyCurrencySymbol) {
                symbol = this.state.companyCurrencySymbol;
            } else {
                symbol = '$';
            }
            return symbol + value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        // 7. Integer formatting
        if (type === 'integer' && typeof value === 'number') return value.toLocaleString();

        // 8. Relational field (Many2one)
        if (Array.isArray(value) && value.length === 2 && typeof value[0] === 'number') {
            const relation = fieldMeta?.relation;
            if (relation) {
                return markup(`<a href="#" class="mcp-record-link text-decoration-none fw-bold" style="color: var(--mcp-primary, #714B67);" data-model="${relation}" data-id="${value[0]}">${this._escapeHtml(value[1])}</a>`);
            }
            return value[1];
        }

        // 8a. Direct Record ID column or primary name column link
        if ((fieldMeta === 'id' || fieldMeta?.name === 'id' || fieldMeta?.relation)) {
            const relation = typeof fieldMeta === 'object' ? fieldMeta.relation : null;
            const recordId = record?.id || (typeof value === 'number' ? value : null);
            if (relation && recordId) {
                return markup(`<a href="#" class="mcp-record-link text-decoration-none fw-bold" style="color: var(--mcp-primary, #714B67);" data-model="${relation}" data-id="${recordId}">${this._escapeHtml(value)}</a>`);
            }
        }

        if (Array.isArray(value)) return value.join(', ');
        return value;
    }

    stringify(val) {
        try {
            return JSON.stringify(val, null, 2);
        } catch (e) {
            return String(val);
        }
    }

    onKeyDown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            if (this.state.loading) return;
            this.sendMessage();
        } else if (event.key === 'k' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            this.newChat();
        } else if (event.key === 'Escape') {
            this.messageInput.el?.blur();
            if (window.innerWidth < 768) this.state.sidebarOpen = false;
        }
    }

    _parseUserMessage(content) {
        // Strip [User uploaded file: "name" (...)] injected by gateway.
        // Split on \n\n — the gateway always does _att_note + '\n\n' + user_message.
        // Using indexOf(']') was wrong: the bracket contains env["ir.attachment"] which has an early ].
        if (typeof content !== 'string' || !content.startsWith('[User uploaded file: "')) {
            return { cleanContent: content, attachmentChip: null };
        }
        const sep = content.indexOf('\n\n');
        if (sep === -1) return { cleanContent: content, attachmentChip: null };

        const bracket = content.slice(0, sep);
        const cleanContent = content.slice(sep + 2);

        const nameMatch = bracket.match(/\[User uploaded file: "([^"]+)"/);
        const mimeMatch = bracket.match(/mimetype: ([^,)]+)/);
        const idMatch = bracket.match(/attachment_id:\s*(\d+)/);
        const name = nameMatch ? nameMatch[1] : 'attachment';
        const mimetype = mimeMatch ? mimeMatch[1].trim() : '';
        const attachmentId = idMatch ? parseInt(idMatch[1], 10) : null;

        const SPREADSHEET_MIMES = [
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.oasis.opendocument.spreadsheet',
        ];
        const isSpreadsheet = SPREADSHEET_MIMES.includes(mimetype);
        const isImage = mimetype.startsWith('image/');
        const isPdf = mimetype === 'application/pdf';
        const typeLabel = isSpreadsheet ? 'Spreadsheet' : isPdf ? 'PDF' : isImage ? 'Image' : 'File';
        const iconType = isSpreadsheet ? 'sheet' : isPdf ? 'pdf' : isImage ? 'image' : 'file';
        const ext = name.includes('.') ? name.split('.').pop().toUpperCase().slice(0, 5) : 'FILE';

        return {
            cleanContent,
            attachmentChip: { name, mimetype, isSpreadsheet, isImage, isPdf, typeLabel, iconType, ext, attachmentId },
        };
    }

    _parseContent(content) {
        if (typeof content !== 'string') return { parsedContent: content, structuredData: null };
        
        // Parse execution gate from raw text
        if (content.includes('proceed') && (content.includes('order') || content.includes('record') || content.includes('S000'))) {
            const records = [];
            const rx = /\b([A-Z0-9/\-_]+)\s*\(([^,]+),\s*([^)]+)\)/g;
            let match;
            while ((match = rx.exec(content)) !== null) {
                records.push({
                    name: match[1],
                    partner: match[2].trim(),
                    amount: match[3].trim()
                });
            }
            if (records.length > 0) {
                const lines = content.split('\n').filter(l => l.trim().length > 0);
                const desc = lines.find(l => l.includes('action_quotation_sent') || l.includes('sent stage') || l.includes('code indicates')) || lines[0];
                const prompt = lines.find(l => l.includes('proceed') || l.includes('let me know')) || 'Would you like to proceed?';
                const model = content.includes('sale order') ? 'sale.order' : 'res.partner';
                
                return {
                    parsedContent: null,
                    structuredData: {
                        _type: "execution_gate",
                        title: "Confirm Send Quotation Action",
                        model: model,
                        records: records,
                        description: desc,
                        action_prompt: prompt,
                        confirm_command: "proceed"
                    }
                };
            }
        }

        if (content.startsWith("Tool execution failed:")) {
            return {
                parsedContent: null,
                structuredData: {
                    _type: "error",
                    content: content
                }
            };
        }
        if (content.startsWith('{"_is_structured":') || content.startsWith('{"_type":')) {
            try {
                const parsed = JSON.parse(content);
                if (parsed.company_currency_symbol) {
                    this.state.companyCurrencySymbol = parsed.company_currency_symbol;
                }
                return { parsedContent: null, structuredData: parsed };
            }
            catch (e) {
                // Common LLM mistake: an unescaped literal quote left inside the
                // content string (e.g. `when you say "foo", ...`). Repair
                // already-stored broken messages client-side too — the server-side
                // fix (gateway.py's _repair_broken_content_json) only prevents new
                // ones, it can't retroactively fix messages already in the DB.
                const repaired = this._repairBrokenContentJson(content);
                if (repaired) return { parsedContent: null, structuredData: repaired };
            }
        }
        const typeIdx = content.indexOf('{"_type":');
        if (typeIdx > 0) {
            try {
                const parsed = JSON.parse(content.slice(typeIdx));
                if (parsed._type) {
                    if (parsed.company_currency_symbol)
                        this.state.companyCurrencySymbol = parsed.company_currency_symbol;
                    return { parsedContent: null, structuredData: parsed };
                }
            } catch (e) { /* fallthrough */ }
        }
        const m = content.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
        if (m) {
            try {
                const parsed = JSON.parse(m[1]);
                if (parsed._type || parsed._is_structured) {
                    if (parsed.company_currency_symbol) {
                        this.state.companyCurrencySymbol = parsed.company_currency_symbol;
                    }
                    return { parsedContent: null, structuredData: parsed };
                }
            } catch (e) { /* fallthrough */ }
        }
        return { parsedContent: markup(this._renderMarkdown(content)), structuredData: null };
    }

    onInputResize(ev) {
        const el = ev.target;
        el.style.height = "auto";
        el.style.height = el.scrollHeight + "px";
    }
}

export class MessageBubble extends Component {
    static template = "mcp_gateway.MessageBubble";
    static props = ["message"];
}

// ECharts' default yAxis/xAxis "name" renders at the top of the plot area
// (nameLocation:'end'), the same vertical space a title+subtext block occupies —
// the two collide whenever both are set and nobody gave the grid explicit top
// clearance. Only steps in when needed; never touches an explicit grid.top the
// chart's own data_code already set. Handles both object and array axes.
function avoidTitleAxisNameOverlap(options) {
    const titleObj = Array.isArray(options.title) ? options.title[0] : options.title;
    const hasTitle = titleObj && (titleObj.text || titleObj.subtext);
    if (!hasTitle) return options;

    const getAxisName = (axis) => {
        if (!axis) return null;
        if (Array.isArray(axis)) {
            for (const ax of axis) {
                if (ax && ax.name) return ax.name;
            }
            return null;
        }
        return axis.name;
    };

    const hasAxisName = getAxisName(options.xAxis) || getAxisName(options.yAxis);
    if (!hasAxisName) return options;
    if (options.grid && options.grid.top !== undefined) return options;
    const top = titleObj.subtext ? 90 : 60;
    return { ...options, grid: { ...(options.grid || {}), top } };
}

// Genuine child component (not a t-call) so it gets its own mount/patch/unmount
// lifecycle — needed to call echarts.init() only after its <div> actually exists,
// and to dispose the chart instance when the message list re-renders it away.
export class EchartPreview extends Component {
    static template = "mcp_gateway.EchartPreview";
    static props = {
        optionsJson: { type: String, optional: true },
        chartId: { type: Number, optional: true },
        title: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.chartRef = useRef("chart");
        this._chart = null;
        this._observer = null;
        this._resizeHandler = () => this._chart && this._chart.resize();
        // Chat bubbles are a live view of the same mcp.echart record, not a
        // frozen screenshot — if the user edits the chart later (color, data,
        // type) via read_record+update_record, earlier bubbles should reflect
        // it too. optionsJson (the snapshot captured when this message was
        // created) is only the fallback for missing/deleted charts or old
        // messages from before chart_id existed.
        this._liveOptionsJson = null;

        // Both hooks are async — anything thrown AFTER the first `await` becomes
        // an unhandled promise rejection, which OWL's <ErrorHandler> (sync-render-
        // only) does NOT catch, unlike a synchronous render error. A single bad
        // chart could otherwise escape the per-message containment entirely.
        // Wrap each hook's own body so a failure here degrades gracefully in just
        // this chart's div, same as _render()'s own internal try/catch already does.
        onMounted(async () => {
            try {
                window.addEventListener("resize", this._resizeHandler);
                this._setupObserver();
                await this._fetchLive();
                await this._render();
            } catch (e) {
                console.error("EchartPreview onMounted failed:", e);
                if (this.chartRef.el) this.chartRef.el.textContent = "Failed to render chart.";
            }
        });
        onPatched(async () => {
            try {
                await this._fetchLive();
                await this._render();
            } catch (e) {
                console.error("EchartPreview onPatched failed:", e);
                if (this.chartRef.el) this.chartRef.el.textContent = "Failed to render chart.";
            }
        });
        onWillUnmount(() => {
            window.removeEventListener("resize", this._resizeHandler);
            if (this._observer) {
                this._observer.disconnect();
                this._observer = null;
            }
            this._chart?.dispose();
        });
    }

    async _fetchLive() {
        if (!this.props.chartId) return;
        try {
            const rows = await this.orm.read("mcp.echart", [this.props.chartId], ["options"]);
            if (rows.length && rows[0].options) this._liveOptionsJson = rows[0].options;
        } catch (e) {
            // Chart deleted or inaccessible — fall back to the stored snapshot.
        }
    }

    _setupObserver() {
        const el = this.chartRef.el;
        if (!el || typeof ResizeObserver === "undefined") return;
        // Chat panel layout (sidebar toggle, new messages, scroll) can still be
        // settling when this component mounts, so the container's measured size
        // at echarts.init() time can be smaller than its final rendered size —
        // baking in a cramped canvas where labels overlap/get cut off. Re-render
        // whenever the container's actual size changes, mirroring echart_field.js.
        this._observer = new ResizeObserver(() => {
            if (el.offsetWidth > 0 && el.offsetHeight > 0) this._render().catch(() => {});
        });
        this._observer.observe(el);
    }

    async _render() {
        const el = this.chartRef.el;
        if (!el) return;
        // Don't init/resize into a zero-size container (still-settling layout).
        if (el.offsetWidth === 0 || el.offsetHeight === 0) return;
        if (typeof echarts === "undefined") {
            el.textContent = "Chart library failed to load.";
            return;
        }
        const rawOptions = this._liveOptionsJson || this.props.optionsJson;
        if (!rawOptions) {
            // AI referenced a chart_id but the live fetch hasn't resolved yet
            // (or failed) and there's no stored snapshot to fall back to.
            el.textContent = this.props.chartId ? "Loading chart…" : "No chart data.";
            return;
        }
        let options;
        try {
            options = JSON.parse(rawOptions);
            options = avoidTitleAxisNameOverlap(options);
        } catch (e) {
            el.textContent = "Invalid chart data.";
            return;
        }
        try {
            if (!this._chart) this._chart = echarts.init(el, null, { renderer: "canvas" });
            this._chart.resize();
            this._chart.setOption(options, true);
        } catch (e) {
            el.textContent = "Failed to render chart.";
        }
    }

    // Only meaningful once the chart is a real mcp.echart record — the
    // controller reads it live from the DB, nothing to show for a bare
    // options-only snapshot (old messages predating chart_id).
    get viewUrl() {
        return this.props.chartId ? `/mcp/echart/${this.props.chartId}/view` : null;
    }

    // Mirrors the sanitization generate_export_file uses server-side
    // (re.sub(r'[^A-Za-z0-9_-]+', '_', title)) so chart downloads get the
    // same "Chart_Title_Here.ext" naming convention as file exports.
    get _filenameBase() {
        const title = (this.props.title || "chart").trim();
        return title.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "chart";
    }

    // Pure client-side canvas capture — same technique as mcp_charts'
    // ChartModal.downloadPng(), works even without a saved chart_id.
    downloadPng() {
        if (!this._chart) return;
        const url = this._chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#fff" });
        const a = document.createElement("a");
        a.href = url;
        a.download = `${this._filenameBase}.png`;
        a.click();
    }

    // Capture the same PNG client-side, then hand it to the server to wrap in
    // a one-line HTML page and run through Odoo's own wkhtmltopdf pipeline —
    // identical mechanism to generate_export_file's PDF branch, no new dependency.
    async downloadPdf() {
        if (!this._chart) return;
        const png = this._chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#fff" });
        try {
            const result = await this.orm.call("mcp.echart", "action_export_chart_pdf", [png]);
            const bytes = atob(result.pdf_base64);
            const buffer = new Uint8Array(bytes.length);
            for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
            const blobUrl = URL.createObjectURL(new Blob([buffer], { type: "application/pdf" }));
            const a = document.createElement("a");
            a.href = blobUrl;
            a.download = `${this._filenameBase}.pdf`;
            a.click();
            URL.revokeObjectURL(blobUrl);
        } catch (e) {
            console.error("Chart PDF export failed:", e);
        }
    }
}

EchartPreview.components = { Dropdown, DropdownItem };

ChatWidget.components = { MessageBubble, Dropdown, DropdownItem, EchartPreview, ErrorHandler };

import { registry } from "@web/core/registry";
registry.category("actions").add("mcp_gateway.chat", ChatWidget);
