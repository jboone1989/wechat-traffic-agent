"""Single-process Web application composition; feature handlers own behavior.

This module contains only lifetime ownership, permission callbacks, and
dependency wiring. Endpoints depend on injected service ports, not one another.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware

from ..account_management import AccountManager
from ..audience_analytics import AudienceAnalytics
from ..llm_audit import LLMRequestAuditStore
from ..memory_runtime import OwnerMemoryRuntime
from ..neural_circuit import NeuralCircuitInspector
from ..neural_telemetry import NeuralTelemetrySampler
from ..operator_console import OperatorConsole, operator_console_enabled
from ..operator_memory import OperatorLongTermMemory
from ..operator_private_chat import OperatorConversationStore
from ..operator_private_chat_handlers import OperatorPrivateChatHandlers
from ..public_activity import read_public_activity
from .operator_browser import OperatorBrowserHandlers
from .operator_login import OperatorLoginHandlers
from .operator_pages import OperatorPages
from .operator_resources import OperatorResourceHandlers
from .public_chat import PublicChatHandlers, PublicChatPorts
from .public_status import PublicStatusHandlers
from .public_views import PublicViews
from .route_registry import operator_routes, public_routes


@dataclass(frozen=True)
class OperatorServices:
    console: object | None
    accounts: object | None
    oauth: object | None
    browser: object | None
    novnc: object | None
    conversation: object | None
    memory: object | None
    memory_runtime: object | None
    audit: object | None
    analytics: object | None


class WebAppFactory:
    def __init__(self, service, *, chat_ports: PublicChatPorts,
                 snapshot, degraded, snapshot_timeout, reply_timeout, max_chars):
        self.service = service
        self.chat_ports = chat_ports
        self.snapshot, self.degraded = snapshot, degraded
        self.snapshot_timeout, self.reply_timeout = snapshot_timeout, reply_timeout
        self.max_chars = max_chars
        self.operator = None

    def _operator_services(self):
        host = self.service
        console = OperatorConsole(host.state_root) if operator_console_enabled() else None
        if console is None:
            return OperatorServices(*(None for _ in range(10)))
        from ..local_browser_control import LocalBrowserControl
        from ..novnc_takeover import AgentRuntimeNoVNC
        from ..x_oauth_headless import XOAuthHeadlessManager
        accounts = AccountManager(host.state_root)
        conversation = OperatorConversationStore(host.state_root)
        runtime = getattr(host.application, "domain_runtime", None)
        if runtime is not None:
            conversation.reconcile_pending_semantic_upgrades(runtime)
            refs = conversation.active_canonical_goal_refs(runtime)
            if refs:
                scheduler = getattr(host.wakeful, "schedule_goal_followup", None)
                if callable(scheduler):
                    scheduler(datetime.now(timezone.utc), seconds=5, relevant_refs=refs)
        memory = OperatorLongTermMemory(host.state_root)
        return OperatorServices(
            console, accounts, XOAuthHeadlessManager(host.state_root),
            LocalBrowserControl(state_root=host.state_root), AgentRuntimeNoVNC(),
            conversation, memory, OwnerMemoryRuntime(host.state_root, store=memory.store),
            LLMRequestAuditStore(host.state_root),
            AudienceAnalytics(
                host.state_root,
                access_log=Path(os.environ.get("CONTENT_NGINX_ACCESS_LOG", "/var/log/nginx/access.log")),
            ),
        )

    def _authorized(self, request):
        return self.operator.console is not None and bool(request.session.get("operator"))

    def _websocket_authorized(self, websocket):
        return self.operator.console is not None and bool(
            (websocket.scope.get("session") or {}).get("operator")
        )

    @staticmethod
    def _write_allowed(request):
        return request.headers.get("x-operator-request") == "1"

    def _public_endpoints(self):
        host = self.service
        engagement = (os.environ.get("CONTENT_WEB_ENGAGEMENT_ENABLED") or "").strip().lower()
        views = PublicViews(
            host, telemetry=NeuralTelemetrySampler(host.wakeful),
            circuit=NeuralCircuitInspector(host.wakeful),
            engagement_enabled=engagement in {"1", "true", "yes", "on"},
        )
        status = PublicStatusHandlers(
            state_root=host.state_root, application=host.application, mind=host.mind,
            wakeful=host.wakeful, started_at=host.started_at, poll_seconds=host.poll_seconds,
            snapshot=self.snapshot, degraded=self.degraded,
            activity_reader=read_public_activity, snapshot_timeout=self.snapshot_timeout,
        )
        chat = PublicChatHandlers(
            state_root=host.state_root, application=host.application, mind=host.mind,
            wakeful=host.wakeful, started_at=host.started_at, poll_seconds=host.poll_seconds,
            backend=host.public_backend, limiter=host._limiter, ports=self.chat_ports,
            state_timeout=self.snapshot_timeout, reply_timeout=self.reply_timeout,
            max_chars=self.max_chars,
        )
        return {
            **{name: getattr(views, name) for name in (
                "live_page", "talk_page", "neural_life_page", "brain_lab_page",
                "screeps_page", "screeps_status", "brain_lab_status", "neural_telemetry_status",
            )},
            "status": status.status, "stream": status.stream,
            "chat": chat.chat, "chat_stream": chat.chat_stream,
        }

    def _operator_endpoints(self):
        from ..browser_session_stream import BrowserSessionStream
        host, dep = self.service, self.operator
        auth, write = self._authorized, self._write_allowed
        browser = OperatorBrowserHandlers(
            state_root=host.state_root, console=dep.console, account_manager=dep.accounts,
            control=dep.browser, novnc=dep.novnc, authorized=auth,
            websocket_authorized=self._websocket_authorized, write_allowed=write,
            stream_factory=BrowserSessionStream,
        )
        login = OperatorLoginHandlers(
            state_root=host.state_root, console=dep.console,
            audience_analytics=dep.analytics, oauth_manager=dep.oauth,
            ip_limiter=host._operator_login_ip_limiter,
            global_limiter=host._operator_login_global_limiter,
            authorized=auth, write_allowed=write,
        )
        owner = OperatorPrivateChatHandlers(
            state_root=host.state_root, application=host.application,
            backend=host.backend, mind=host.mind, wakeful=host.wakeful,
            started_at=host.started_at, poll_seconds=host.poll_seconds,
            account_manager=dep.accounts, conversation=dep.conversation,
            memory=dep.memory, memory_runtime=dep.memory_runtime,
            authorized=auth, write_allowed=write, snapshot=self.snapshot,
        )
        pages = OperatorPages(
            console=dep.console, authorized=auth, analytics=dep.analytics, audit=dep.audit,
        )
        resources = OperatorResourceHandlers(
            console=dep.console, wakeful=host.wakeful,
            authorized=auth, write_allowed=write,
        )
        return {
            **browser.routes(), **login.routes(), **owner.routes(),
            **pages.routes(), **resources.routes(),
        }

    def create(self):
        self.operator = self._operator_services()
        endpoints = {**self._public_endpoints(), **self._operator_endpoints()}
        routes = public_routes(endpoints)
        if self.operator.console is not None:
            routes.extend(operator_routes(endpoints))
        app = Starlette(routes=routes)
        if self.operator.console is not None:
            public_url = str(os.environ.get("CONTENT_OPERATOR_PUBLIC_URL") or "").strip()
            app.add_middleware(
                SessionMiddleware, secret_key=self.operator.console.auth.session_secret,
                session_cookie="ferro_operator_session", max_age=1800,
                path="/operator", same_site="strict",
                https_only=public_url.startswith("https://"),
            )
        return app
