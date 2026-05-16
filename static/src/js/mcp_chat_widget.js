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
import { NotificationService } from "@web/core/notifications/notification_service";
import { useService } from "@web/core/utils/hooks";

export class ChatWidget extends Component {
    static template = "mcp_gateway.ChatWidget";

    setup() {
        this.state = useState({
            agents: [],
            selectedAgent: null,
            messages: [],
            loading: false,
            totalTokens: 0,
            estimatedCost: 0,
            sessionId: null,
        });

        this.messageInput = useRef("messageInput");
        this.notificationService = useService("notification");
        this.orm = useService("orm");

        onMounted(() => this.loadAgents());
    }

    async loadAgents() {
        try {
            const response = await rpc({
                model: "mcp.agent",
                method: "search_read",
                args: [
                    [["active", "=", true]],
                    ["id", "name", "provider", "model_name", "status", "session_count", "color"],
                ],
            });
            this.state.agents = response;
        } catch (error) {
            this.notificationService.add("Failed to load agents", {
                type: "danger",
            });
        }
    }

    selectAgent(agent) {
        this.state.selectedAgent = agent;
        this.state.messages = [];
        this.state.totalTokens = 0;
        this.state.estimatedCost = 0;
        this.state.sessionId = null;
    }

    async sendMessage() {
        const message = this.messageInput.current?.value?.trim();
        if (!message || !this.state.selectedAgent) return;

        this.state.loading = true;
        const userMsg = {
            role: "user",
            content: message,
        };
        this.state.messages.push(userMsg);
        this.messageInput.current.value = "";

        try {
            const result = await rpc({
                model: "mcp.gateway",
                method: "run",
                args: [],
                kwargs: {
                    agent_id: this.state.selectedAgent.id,
                    user_message: message,
                    session_id: this.state.sessionId,
                },
            });

            // Add assistant message
            this.state.messages.push({
                role: "assistant",
                content: result.reply,
            });

            // Update session tracking
            this.state.sessionId = result.session_id;
            this.state.totalTokens += result.input_tokens + result.output_tokens;
            this.state.estimatedCost += result.cost_usd;

            // Add tool calls to display
            if (result.tool_calls > 0) {
                this.state.messages.push({
                    role: "system",
                    content: `${result.tool_calls} tool(s) executed`,
                });
            }
        } catch (error) {
            this.notificationService.add(
                error.message || "Failed to send message",
                { type: "danger" }
            );
            // Remove failed message
            this.state.messages.pop();
        } finally {
            this.state.loading = false;
        }
    }

    onKeyDown(event) {
        if (event.key === "Enter" && event.ctrlKey) {
            this.sendMessage();
        }
    }
}

ChatWidget.components = { MessageBubble };

export class MessageBubble extends Component {
    static template = "mcp_gateway.MessageBubble";
    static props = ["message"];
}
