"""HTTP route ownership is distinct from the handlers and domain services.

A route is a path, handler name and allowed methods; methods=None means WebSocket.
The existing LiveAgentService provides handlers without changing their lifetime.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from starlette.routing import Route, WebSocketRoute

PUBLIC_ROUTES = (
    ('/live', 'live_page', ('GET',)),
    ('/live/', 'live_page', ('GET',)),
    ('/talk', 'talk_page', ('GET',)),
    ('/talk/', 'talk_page', ('GET',)),
    ('/neural', 'neural_life_page', ('GET',)),
    ('/neural/', 'neural_life_page', ('GET',)),
    ('/brain', 'brain_lab_page', ('GET',)),
    ('/brain/', 'brain_lab_page', ('GET',)),
    ('/screeps', 'screeps_page', ('GET',)),
    ('/screeps/', 'screeps_page', ('GET',)),
    ('/agents/ferro/live', 'live_page', ('GET',)),
    ('/agents/ferro/live/', 'live_page', ('GET',)),
    ('/agents/ferro/talk', 'talk_page', ('GET',)),
    ('/agents/ferro/talk/', 'talk_page', ('GET',)),
    ('/agents/ferro/neural', 'neural_life_page', ('GET',)),
    ('/agents/ferro/neural/', 'neural_life_page', ('GET',)),
    ('/agents/ferro/brain', 'brain_lab_page', ('GET',)),
    ('/agents/ferro/brain/', 'brain_lab_page', ('GET',)),
    ('/agents/ferro/screeps', 'screeps_page', ('GET',)),
    ('/agents/ferro/screeps/', 'screeps_page', ('GET',)),
    ('/api/agent/brain-lab', 'brain_lab_status', ('GET',)),
    ('/api/agent/screeps', 'screeps_status', ('GET',)),
    ('/api/agent/neural-telemetry', 'neural_telemetry_status', ('GET',)),
    ('/api/agent/status', 'status', ('GET',)),
    ('/api/agent/stream', 'stream', ('GET',)),
    ('/api/agent/chat', 'chat', ('POST',)),
)

OPERATOR_ROUTES = (
    ('/operator', 'operator_page', ('GET',)),
    ('/operator/', 'operator_page', ('GET',)),
    ('/operator/chat', 'operator_private_chat_page', ('GET',)),
    ('/operator/chat/', 'operator_private_chat_page', ('GET',)),
    ('/operator/llm', 'operator_llm_audit_page', ('GET',)),
    ('/operator/llm/', 'operator_llm_audit_page', ('GET',)),
    ('/operator/audience', 'operator_audience_page', ('GET',)),
    ('/operator/audience/', 'operator_audience_page', ('GET',)),
    ('/operator/api/audience', 'operator_audience_api', ('GET',)),
    ('/operator/api/llm-requests', 'operator_llm_requests', ('GET',)),
    ('/operator/api/chat', 'operator_chat_get', ('GET',)),
    ('/operator/api/chat', 'operator_chat_post', ('POST',)),
    ('/operator/api/chat/stream', 'operator_chat_stream', ('POST',)),
    ('/operator/api/chat/goal-draft', 'operator_chat_goal_draft', ('POST',)),
    ('/operator/api/chat/goals/{proposal_id:path}/activate', 'operator_chat_goal_activate', ('POST',)),
    ('/operator/api/request-login', 'operator_request_login', ('POST',)),
    ('/operator/api/login-delivery/email', 'operator_email_settings', ('POST',)),
    ('/operator/api/session', 'operator_session_status', ('GET',)),
    ('/operator/api/session', 'operator_login', ('POST',)),
    ('/operator/api/status', 'operator_status', ('GET',)),
    ('/operator/api/accounts/x/oauth/start', 'operator_x_oauth_start', ('POST',)),
    ('/operator/api/accounts/x/oauth/complete', 'operator_x_oauth_complete', ('POST',)),
    ('/operator/api/accounts/x/oauth/cancel', 'operator_x_oauth_cancel', ('POST',)),
    ('/operator/api/account-instances/{account_id}', 'operator_account_instance_delete', ('DELETE',)),
    ('/operator/browser/x/websockify', 'operator_novnc_websocket', None),
    ('/operator/browser/x/{path:path}', 'operator_novnc_asset', ('GET',)),
    ('/operator/api/accounts/x/browser/status', 'operator_browser_status', ('GET',)),
    ('/operator/api/accounts/{provider_id}/login/stream', 'operator_account_stream', None),
    ('/operator/api/accounts/{provider_id}/connect', 'operator_account_connect', ('POST',)),
    ('/operator/api/accounts/{provider_id}/verify', 'operator_account_verify', ('POST',)),
    ('/operator/api/accounts/{provider_id}/login/snapshot', 'operator_account_snapshot', ('GET',)),
    ('/operator/api/accounts/{provider_id}/login/click', 'operator_account_login_click', ('POST',)),
    ('/operator/api/accounts/{provider_id}/login/type', 'operator_account_login_type', ('POST',)),
    ('/operator/api/accounts/{provider_id}/login/key', 'operator_account_login_key', ('POST',)),
    ('/operator/api/accounts/{provider_id}/login/stop', 'operator_account_login_stop', ('POST',)),
    ('/operator/api/browser/local/start', 'operator_local_browser_start', ('POST',)),
    ('/operator/api/browser/local/snapshot', 'operator_local_browser_snapshot', ('GET',)),
    ('/operator/api/browser/local/click', 'operator_local_browser_click', ('POST',)),
    ('/operator/api/browser/local/type', 'operator_local_browser_type', ('POST',)),
    ('/operator/api/browser/local/key', 'operator_local_browser_key', ('POST',)),
    ('/operator/api/browser/local/stop', 'operator_local_browser_stop', ('POST',)),
    ('/operator/api/requests/{request_id}/decision', 'operator_resource_decision', ('POST',)),
    ('/operator/api/survival/needs/{need_id}/decision', 'operator_survival_need_decision', ('POST',)),
    ('/operator/api/survival/hosts/bootstrap', 'operator_bootstrap_remote_host', ('POST',)),
    ('/operator/api/survival/hosts', 'operator_register_remote_host', ('POST',)),
    ('/operator/api/survival/hosts/{host_id}/verify', 'operator_verify_remote_host', ('POST',)),
    ('/operator/api/survival/offload', 'operator_plan_service_offload', ('POST',)),
    ('/operator/api/survival/offload/{action_id}/execute', 'operator_execute_service_offload', ('POST',)),
    ('/operator/api/secrets/{env_name}', 'operator_store_secret', ('POST',)),
    ('/operator/api/resources/{env_name}', 'operator_store_resource_metadata', ('POST',)),
    ('/operator/api/logout', 'operator_logout', ('POST',)),
)

def _materialize(specs, handlers: Mapping[str, Any]):
    routes = []
    for path, name, methods in specs:
        endpoint = handlers[name]
        if methods is None:
            routes.append(WebSocketRoute(path, endpoint))
        else:
            routes.append(Route(path, endpoint, methods=list(methods)))
    return routes


def public_routes(handlers: Mapping[str, Any]):
    """Build public site and telemetry routes, independently of the operator UI."""
    return _materialize(PUBLIC_ROUTES, handlers)


def operator_routes(handlers: Mapping[str, Any]):
    """Build private operator/browser routes only when the console is enabled."""
    return _materialize(OPERATOR_ROUTES, handlers)
