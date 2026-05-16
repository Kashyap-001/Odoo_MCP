"""
mcp_gateway/controllers/chat_controller.py

HTTP API endpoints for chat, webhooks, and agent/tool discovery.

Key classes:
  ChatController — Handles all HTTP API routes

Routes:
  POST   /mcp/chat
  GET    /mcp/agents/available
  GET    /mcp/tools/available
  GET    /mcp/session/<id>/transcript
  POST   /mcp/webhook/<token>

Dependencies:
  - mcp.gateway — agentic loop
  - http.route — Odoo HTTP routing
  - json — request/response serialization
"""

import logging
from odoo import http
from odoo.exceptions import AccessError, UserError
from ..mcp.gateway import McpGateway

_logger = logging.getLogger(__name__)


class ChatController(http.Controller):
    """
    HTTP API controller for AI Gateway.

    Provides endpoints for chat, agent/tool discovery, webhooks, and session export.
    """

    @http.route('/mcp/chat', type='json', auth='user', methods=['POST'])
    def chat(self, agent_id: int, message: str, session_id: int = None,
             active_model: str = None, active_id: int = None) -> dict:
        """
        Chat endpoint — send message to agent and get reply.

        Args:
            agent_id (int): Agent to use
            message (str): User message
            session_id (int): Optional existing session ID
            active_model (str): Optional Odoo model for context
            active_id (int): Optional record ID for context

        Returns:
            dict: {
              'reply': str,
              'session_id': int,
              'tool_calls': int,
              'input_tokens': int,
              'output_tokens': int,
              'cost_usd': float,
              'agent_name': str,
            }

        Raises:
            AccessError: if agent not accessible
            UserError: if agent unconfigured or provider error
        """
        try:
            user = http.request.env.user
            gateway = McpGateway(http.request.env, user)
            result = gateway.run(
                agent_id=agent_id,
                user_message=message,
                session_id=session_id,
                active_model=active_model,
                active_id=active_id,
            )

            agent = http.request.env['mcp.agent'].browse(agent_id)
            result['agent_name'] = agent.name

            return {
                'status': 'success',
                'data': result,
            }
        except AccessError as e:
            _logger.warning('Access denied for chat: %s', str(e))
            return {
                'status': 'error',
                'error': 'Access denied',
            }
        except UserError as e:
            _logger.warning('User error in chat: %s', str(e))
            return {
                'status': 'error',
                'error': str(e),
            }
        except Exception as e:
            _logger.error('Unexpected error in chat endpoint: %s', str(e))
            return {
                'status': 'error',
                'error': 'Internal server error',
            }

    @http.route('/mcp/agents/available', type='json', auth='user', methods=['GET'])
    def agents_available(self) -> dict:
        """
        List agents available to current user.

        Returns:
            dict: {
              'status': 'success',
              'data': [
                {
                  'id': 1,
                  'name': 'Sales Assistant',
                  'provider': 'anthropic',
                  'model_name': 'claude-sonnet-4-6',
                  'description': '...',
                  'avatar_url': '/web/image/...',
                  'status': 'online',
                  'session_count': 5,
                }
              ]
            }
        """
        try:
            user = http.request.env.user
            rules = http.request.env['mcp.access.rule'].get_rules_for_user(user)

            if len(rules['agent_ids']) == 0:
                # User has access to all agents via rules with empty agent_ids
                agents = http.request.env['mcp.agent'].search([('active', '=', True)])
            else:
                agents = rules['agent_ids']

            agents_data = []
            for agent in agents:
                avatar_url = ''
                if agent.avatar:
                    avatar_url = f'/web/image/{agent._name}/{agent.id}/avatar'

                agents_data.append({
                    'id': agent.id,
                    'name': agent.name,
                    'provider': agent.provider,
                    'model_name': agent.model_name,
                    'description': agent.description,
                    'avatar_url': avatar_url,
                    'status': agent.status,
                    'session_count': agent.session_count,
                    'color': agent.color,
                })

            return {
                'status': 'success',
                'data': agents_data,
            }
        except Exception as e:
            _logger.error('Error fetching available agents: %s', str(e))
            return {
                'status': 'error',
                'error': 'Failed to fetch agents',
            }

    @http.route('/mcp/tools/available', type='json', auth='user', methods=['GET'])
    def tools_available(self, agent_id: int = None) -> dict:
        """
        List tools available to current user.

        Args:
            agent_id (int): Optional agent ID to filter tools

        Returns:
            dict: {
              'status': 'success',
              'data': [
                {
                  'id': 1,
                  'name': 'partner_search',
                  'display_name_label': 'Search Partners',
                  'description': '...',
                  'category': 'Sales & CRM',
                  'is_readonly': True,
                }
              ]
            }
        """
        try:
            user = http.request.env.user
            rules = http.request.env['mcp.access.rule'].get_rules_for_user(user)

            if len(rules['tool_ids']) == 0:
                # Access to all tools
                tools = http.request.env['mcp.tool'].search([('active', '=', True)])
            else:
                tools = rules['tool_ids']

            if agent_id:
                agent = http.request.env['mcp.agent'].browse(agent_id)
                if agent.exists():
                    tools = tools & agent.effective_tool_ids

            tools_data = []
            for tool in tools:
                tools_data.append({
                    'id': tool.id,
                    'name': tool.name,
                    'display_name_label': tool.display_name_label,
                    'description': tool.description,
                    'category': tool.category_id.name if tool.category_id else '',
                    'is_readonly': tool.is_readonly,
                    'requires_confirm': tool.requires_confirm,
                })

            return {
                'status': 'success',
                'data': tools_data,
            }
        except Exception as e:
            _logger.error('Error fetching available tools: %s', str(e))
            return {
                'status': 'error',
                'error': 'Failed to fetch tools',
            }

    @http.route('/mcp/session/<int:session_id>/transcript', type='http', auth='user')
    def session_transcript(self, session_id: int, **kwargs):
        """
        Download session transcript as plaintext file.

        Args:
            session_id (int): Session ID to export

        Returns:
            Response: File download
        """
        try:
            user = http.request.env.user
            session = http.request.env['mcp.session'].browse(session_id)

            if not session.exists():
                return http.request.not_found()

            # Check access (user must own session or be admin)
            if session.user_id.id != user.id and not user.has_group('mcp_gateway.group_mcp_admin'):
                return http.request.redirect('/web')

            # Build transcript
            lines = [f'Session: {session.name}']
            lines.append(f'Agent: {session.agent_id.name}')
            lines.append(f'User: {session.user_id.name}')
            lines.append(f'Duration: {session.duration_seconds:.1f}s')
            lines.append(f'Tokens: {session.input_tokens + session.output_tokens}')
            lines.append(f'Cost: ${session.estimated_cost_usd:.4f}')
            lines.append('=' * 60 + '\n')

            for msg in session.session_message_ids:
                lines.append(f'[{msg.role.upper()}] {msg.create_date}')
                if msg.role in ('user', 'assistant'):
                    lines.append(msg.content)
                elif msg.role == 'tool_call':
                    lines.append(f'Tool: {msg.tool_name}')
                    lines.append(f'Args: {msg.content}')
                elif msg.role == 'tool_result':
                    lines.append(f'Result: {msg.content[:200]}')
                lines.append('')

            content = '\n'.join(lines)
            filename = f'{session.name.replace(" ", "_")}.txt'

            return http.request.make_response(
                content,
                [('Content-Type', 'text/plain'), ('Content-Disposition', f'attachment; filename="{filename}"')]
            )
        except Exception as e:
            _logger.error('Error exporting transcript: %s', str(e))
            return http.request.not_found()

    @http.route('/mcp/webhook/<string:token>', type='json', auth='none', methods=['POST'])
    def webhook_trigger(self, token: str, **kwargs) -> dict:
        """
        Webhook endpoint for external trigger events.

        Args:
            token (str): Webhook token for authentication
            Additional kwargs from request body passed as trigger context

        Returns:
            dict: {
              'status': 'success',
              'session_id': int,
              'reply': str,
            }
        """
        try:
            # Find webhook trigger by token
            trigger = http.request.env['mcp.webhook.trigger'].search([
                ('token', '=', token),
                ('active', '=', True),
            ], limit=1)

            if not trigger:
                return {'status': 'error', 'error': 'Invalid webhook token'}

            # Get the trigger model and record from request
            body = http.request.get_json_data()
            model_name = body.get('model')
            record_id = body.get('record_id')

            if not model_name or not record_id:
                return {'status': 'error', 'error': 'Missing model or record_id in request'}

            # Verify model matches trigger
            if model_name != trigger.trigger_model:
                return {'status': 'error', 'error': 'Model mismatch'}

            # Get record
            record = http.request.env[model_name].browse(record_id)
            if not record.exists():
                return {'status': 'error', 'error': 'Record not found'}

            # Fire trigger
            result = trigger.fire(record)

            return {
                'status': 'success',
                'session_id': result['session_id'],
                'reply': result['reply'],
            }

        except Exception as e:
            _logger.error('Webhook trigger error: %s', str(e))
            return {'status': 'error', 'error': str(e)[:200]}
