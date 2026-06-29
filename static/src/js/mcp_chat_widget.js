import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

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
        });

        this._sendSeq = 0;

        this.messageInput = useRef("messageInput");
        this.fileInput = useRef("fileInput");
        this.chatHistory = useRef("chatHistory");
        this.notificationService = useService("notification");
        this.orm = useService("orm");

        onMounted(() => {
            this.loadAgents();
            this.loadRecentSessions();
            // Event delegation for copy buttons injected by _renderMarkdown
            this.chatHistory.el?.addEventListener('click', this._onChatClick.bind(this));
        });
    }

    async loadAgents() {
        try {
            const response = await this.orm.searchRead(
                "mcp.agent",
                [["active", "=", true]],
                ["id", "name", "provider", "model_name", "status", "session_count", "color", "total_tokens", "total_cost_usd"]
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
                ["id", "name", "agent_id", "create_date", "state"],
                { limit: 20, order: "create_date desc" }
            );

            if (sessions.length > 0) {
                const sessionIds = sessions.map(s => s.id);
                const lastMessages = await this.orm.searchRead(
                    "mcp.session.message",
                    [["session_id", "in", sessionIds], ["role", "in", ["user", "assistant"]]],
                    ["session_id", "role", "content"],
                    { order: "create_date desc", limit: 60 }
                );
                const msgMap = {};
                for (const msg of lastMessages) {
                    const sid = msg.session_id[0];
                    if (!msgMap[sid]) {
                        const text = msg.content || '';
                        const isStructured = text.charAt(0) === '{' && (text.includes('"_type"') || text.includes('"_is_structured"'));
                        msgMap[sid] = isStructured
                            ? '[Tool result]'
                            : text.substring(0, 60) + (text.length > 60 ? '…' : '');
                    }
                }
                this.state.recentSessions = sessions
                    .filter(s => msgMap[s.id])
                    .map(s => ({ ...s, lastMessage: msgMap[s.id] }));
            } else {
                this.state.recentSessions = sessions;
            }
        } catch (error) {
            console.error("MCP: Failed to load sessions", error);
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
        for (const session of this.state.recentSessions) {
            const agentId = Array.isArray(session.agent_id) ? session.agent_id[0] : session.agent_id;
            const agentName = Array.isArray(session.agent_id) ? session.agent_id[1] : 'Unknown Agent';
            if (!groups[agentId]) {
                groups[agentId] = { id: agentId, name: agentName, sessions: [] };
            }
            groups[agentId].sessions.push(session);
        }
        return Object.values(groups);
    }

    selectAgent(agent) {
        this._sendSeq++;
        this.state.selectedAgent = agent;
        this.state.messages = [];
        this.state.totalTokens = 0;
        this.state.estimatedCost = 0;
        this.state.sessionId = null;
        if (window.innerWidth < 768) this.state.sidebarOpen = false;
    }

    async selectSession(session) {
        this._sendSeq++;
        this.state.loading = true;
        try {
            const agent = this.state.agents.find(a => a.id === session.agent_id[0]);
            this.state.selectedAgent = agent || { id: session.agent_id[0], name: session.agent_id[1] };
            this.state.sessionId = session.id;

            const messages = await this.orm.searchRead(
                "mcp.session.message",
                [["session_id", "=", session.id], ["role", "in", ["user", "assistant"]]],
                ["role", "content", "tool_name", "create_date"],
                { order: "create_date asc" }
            );
            this.state.messages = messages.map(msg => {
                const { parsedContent, structuredData } = this._parseContent(msg.content);
                return { ...msg, content: parsedContent, structuredData };
            });

            const sessionData = await this.orm.read("mcp.session", [session.id], ["input_tokens", "output_tokens", "estimated_cost_usd"]);
            if (sessionData.length > 0) {
                this.state.totalTokens = sessionData[0].input_tokens + sessionData[0].output_tokens;
                this.state.estimatedCost = sessionData[0].estimated_cost_usd;
            }

            if (window.innerWidth < 768) this.state.sidebarOpen = false;
        } catch (error) {
            this.notificationService.add("Failed to load session history", { type: "danger" });
        } finally {
            this.state.loading = false;
            this._scrollToBottom();
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
                    this.state.pendingFile = { id: resp.data.id, name: resp.data.name };
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
        this.state.pendingFile = null;

        this.state.loading = true;
        this.state.isTyping = true;
        this.state.messages.push({ role: "user", content: message });
        this.messageInput.el.value = "";

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
            const { parsedContent, structuredData } = this._parseContent(result.reply);
            this.state.messages.push({
                role: "assistant",
                content: parsedContent,
                structuredData: structuredData,
            });

            this.state.sessionId = result.session_id;
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
        if (!btn) return;
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
    }

    // ── Markdown renderer ───────────────────────────────────────────────────

    _escapeHtml(str) {
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    _inlineFormat(str) {
        return str
            .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
            .replace(/`([^`\n]+)`/g, '<code class="mcp-md-inline">$1</code>')
            .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    }

    _renderMarkdown(text) {
        const lines = text.split('\n');
        const out = [];
        let inCode = false;
        let codeLines = [];
        let codeLang = '';
        let inList = false;
        let listOrdered = false;

        const closeList = () => {
            if (inList) { out.push(listOrdered ? '</ol>' : '</ul>'); inList = false; }
        };

        for (const raw of lines) {
            const fenceMatch = raw.match(/^```(\w*)$/);
            if (fenceMatch) {
                if (!inCode) {
                    closeList();
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
        return out.join('');
    }

    // ── Other helpers ────────────────────────────────────────────────────────

    _formatCellValue(value, fieldMeta, record) {
        const type = fieldMeta?.type || 'char';
        if (value === null || value === undefined || (value === false && type !== 'boolean')) return '—';
        if (type === 'boolean') return value ? '✓' : '—';
        if (type === 'date' && typeof value === 'string')
            return new Date(value).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
        if (type === 'datetime' && typeof value === 'string')
            return new Date(value).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        if ((type === 'monetary' || type === 'float') && typeof value === 'number') {
            const currField = fieldMeta?.currency_field;
            let symbol = '';
            if (currField && record?.[currField]) {
                const name = Array.isArray(record[currField]) ? record[currField][1] : record[currField];
                const symbols = { USD: '$', EUR: '€', GBP: '£', INR: '₹', JPY: '¥', AED: 'د.إ' };
                symbol = symbols[name] || (name + ' ');
            }
            return symbol + value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }
        if (type === 'integer' && typeof value === 'number') return value.toLocaleString();
        if (Array.isArray(value) && value.length === 2 && typeof value[0] === 'number') return value[1];
        if (Array.isArray(value)) return value.join(', ');
        return value;
    }

    onKeyDown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            this.sendMessage();
        } else if (event.key === 'k' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            this.newChat();
        } else if (event.key === 'Escape') {
            this.messageInput.el?.blur();
            if (window.innerWidth < 768) this.state.sidebarOpen = false;
        }
    }

    _parseContent(content) {
        if (typeof content !== 'string') return { parsedContent: content, structuredData: null };
        if (content.startsWith('{"_is_structured":') || content.startsWith('{"_type":')) {
            try { return { parsedContent: null, structuredData: JSON.parse(content) }; }
            catch (e) { /* fallthrough */ }
        }
        const m = content.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/);
        if (m) {
            try {
                const parsed = JSON.parse(m[1]);
                if (parsed._type || parsed._is_structured) {
                    return { parsedContent: null, structuredData: parsed };
                }
            } catch (e) { /* fallthrough */ }
        }
        return { parsedContent: this._renderMarkdown(content), structuredData: null };
    }
}

export class MessageBubble extends Component {
    static template = "mcp_gateway.MessageBubble";
    static props = ["message"];
}

ChatWidget.components = { MessageBubble };

import { registry } from "@web/core/registry";
registry.category("actions").add("mcp_gateway.chat", ChatWidget);
