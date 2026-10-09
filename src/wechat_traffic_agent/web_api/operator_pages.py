"""Operator dashboard views and observational API, independent of owner chat."""
from __future__ import annotations

from starlette.responses import HTMLResponse, JSONResponse

from ..channels.operator_console_page import operator_console_document
from ..channels.operator_private_chat_page import operator_private_chat_document
from ..channels.operator_llm_audit_page import operator_llm_audit_document
from ..channels.operator_audience_page import operator_audience_document


class OperatorPages:
    def __init__(self, *, console, authorized, analytics, audit):
        self.console, self.authorized = console, authorized
        self.analytics, self.audit = analytics, audit

    def routes(self):
        return {
            "operator_page": self.operator_page,
            "operator_private_chat_page": self.operator_private_chat_page,
            "operator_llm_audit_page": self.operator_llm_audit_page,
            "operator_audience_page": self.operator_audience_page,
            "operator_audience_api": self.operator_audience_api,
            "operator_llm_requests": self.operator_llm_requests,
        }

    @staticmethod
    def _page(document, policy):
        return HTMLResponse(document(), headers={
            "Cache-Control": "no-store", "Content-Security-Policy": policy,
        })

    async def operator_page(self, request):
        if self.console is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return self._page(
            operator_console_document,
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )

    async def operator_private_chat_page(self, request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        return self._page(
            operator_private_chat_document,
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )

    async def operator_llm_audit_page(self, request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        return self._page(
            operator_llm_audit_document,
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    async def operator_audience_page(self, request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        return self._page(
            operator_audience_document,
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    async def operator_audience_api(self, request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        try:
            payload = self.analytics.snapshot(
                window=str(request.query_params.get("window") or "24h"),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def operator_llm_requests(self, request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        try:
            limit = int(request.query_params.get("limit") or 100)
        except ValueError:
            limit = 100
        return JSONResponse({
            "ok": True, "requests": self.audit.recent(limit=max(1, min(limit, 500))),
        }, headers={"Cache-Control": "no-store"})
