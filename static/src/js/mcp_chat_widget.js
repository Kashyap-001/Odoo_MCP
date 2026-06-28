/**
 * mcp_gateway/static/src/js/mcp_chat_widget.js
 *
 * OWL 3 chat widget components for in-Odoo AI interaction.
 *
 * Components:
 *   - ChatWidget — Main chat interface
 *   - MessageBubble — Individual message rendering
 */

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
        });

        this._sendSeq = 0;  // incremented on every send + session switch to invalidate stale responses

        this.messageInput = useRef("messageInput");
        this.notificationService = useService("notification");
        this.orm = useService("orm");

        onMounted(() => {
            this.loadAgents();
            this.loadRecentSessions();
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
                // First hit per session_id (sorted desc) = last message
                const msgMap = {};
                for (const msg of lastMessages) {
                    const sid = msg.session_id[0];
                    if (!msgMap[sid]) {
                        const text = msg.content || '';
                        msgMap[sid] = text.startsWith('{"_is_structured":')
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
        this._sendSeq++;  // invalidate any in-flight sendMessage
        this.state.selectedAgent = agent;
        this.state.messages = [];
        this.state.totalTokens = 0;
        this.state.estimatedCost = 0;
        this.state.sessionId = null;
        if (window.innerWidth < 768) this.state.sidebarOpen = false;
    }

    async selectSession(session) {
        this._sendSeq++;  // invalidate any in-flight sendMessage
        this.state.loading = true;
        try {
            const agent = this.state.agents.find(a => a.id === session.agent_id[0]);
            this.state.selectedAgent = agent || { id: session.agent_id[0], name: session.agent_id[1] };
            this.state.sessionId = session.id;

            // Load only user/assistant messages — tool_result are intermediate noise
            const messages = await this.orm.searchRead(
                "mcp.session.message",
                [["session_id", "=", session.id], ["role", "in", ["user", "assistant"]]],
                ["role", "content", "tool_name", "create_date"],
                { order: "create_date asc" }
            );
            this.state.messages = messages
                .map(msg => {
                    const { parsedContent, structuredData } = this._parseContent(msg.content);
                    return { ...msg, content: parsedContent, structuredData };
                });

            // Get session totals
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
        this._sendSeq++;  // invalidate any in-flight sendMessage
        this.state.selectedAgent = null;
        this.state.sessionId = null;
        this.state.messages = [];
        this.state.sidebarOpen = true;
    }

    toggleSidebar() {
        this.state.sidebarOpen = !this.state.sidebarOpen;
    }

    async sendMessage() {
        const message = this.messageInput.el?.value?.trim();
        if (!message || !this.state.selectedAgent) return;

        const mySeq = ++this._sendSeq;

        this.state.loading = true;
        this.state.isTyping = true;
        this.state.messages.push({ role: "user", content: message });
        this.messageInput.el.value = "";

        try {
            const response = await rpc("/mcp/chat", {
                agent_id: this.state.selectedAgent.id,
                message: message,
                session_id: this.state.sessionId,
            });

            // User switched sessions while AI was running — result is saved to DB;
            // it will appear when they reload that session. Just refresh the sidebar.
            if (this._sendSeq !== mySeq) {
                this.loadRecentSessions();
                return;
            }

            if (response.status === 'error') {
                throw new Error(response.error);
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
            this.notificationService.add(
                error.message || "Failed to send message",
                { type: "danger" }
            );
            if (this._sendSeq === mySeq) this.state.messages.pop();
        } finally {
            // Always clear loading — selectSession() manages its own loading flag separately
            this.state.loading = false;
            this.state.isTyping = false;
            if (this._sendSeq === mySeq) this._scrollToBottom();
        }
    }

    _scrollToBottom() {
        setTimeout(() => {
            requestAnimationFrame(() => {
                const el = this.el?.querySelector('.mcp-chat-history');
                if (el) el.scrollTop = el.scrollHeight;
            });
        }, 100);
    }

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
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            this.sendMessage();
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
        return { parsedContent: content, structuredData: null };
    }
}

export class MessageBubble extends Component {
    static template = "mcp_gateway.MessageBubble";
    static props = ["message"];
}

ChatWidget.components = { MessageBubble };

import { registry } from "@web/core/registry";
registry.category("actions").add("mcp_gateway.chat", ChatWidget);
