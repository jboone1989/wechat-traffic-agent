"""Session authentication, delivery and X OAuth adapter endpoints."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import time
from starlette.requests import Request
from starlette.responses import JSONResponse
from wechat_traffic_agent import operator_email
from wechat_traffic_agent.operator_email import OperatorEmailSettings
from wechat_traffic_agent.x_oauth_headless import XOAuthSessionError


class OperatorLoginHandlers:
    def __init__(self, *, state_root, console, audience_analytics, oauth_manager,
                 ip_limiter, global_limiter, authorized, write_allowed):
        self.state_root = state_root
        self.console = console
        self.audience_analytics = audience_analytics
        self.oauth_manager = oauth_manager
        self.ip_limiter = ip_limiter
        self.global_limiter = global_limiter
        self.authorized = authorized
        self.write_allowed = write_allowed

    def routes(self):
        return {
            'operator_request_login': self.operator_request_login,
            'operator_email_settings': self.operator_email_settings,
            'operator_login': self.operator_login,
            'operator_session_status': self.operator_session_status,
            'operator_status': self.operator_status,
            'operator_x_oauth_start': self.operator_x_oauth_start,
            'operator_x_oauth_complete': self.operator_x_oauth_complete,
            'operator_x_oauth_cancel': self.operator_x_oauth_cancel,
        }

    def session_response(self, request: Request):
        request.session.clear()
        request.session["operator"] = True
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        forwarded_https = request.headers.get("x-forwarded-proto") == "https"
        secure_cookie = request.url.scheme == "https" or forwarded_https
        response.set_cookie(
            "ferro_operator_audience",
            self.audience_analytics.store.operator_audience_marker(),
            max_age=1800,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            path="/",
        )
        return response

    async def operator_request_login(self, request: Request):
        if self.console is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 2048:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
        except Exception:
            body = {}
        channel = str(
            body.get("channel") or "wecom"
            if isinstance(body, dict)
            else "wecom"
        ).strip().lower()
        if channel not in {"wecom", "email"}:
            return JSONResponse({"error": "unsupported_login_delivery_channel"}, status_code=400)
        key = request.client.host if request.client else "unknown"
        now_mono = time.monotonic()
        ip_bucket = f"{key}:{channel}"
        global_bucket = f"global:{channel}"
        if not self.ip_limiter.allow(ip_bucket, now_mono):
            return JSONResponse(
                {
                    "error": "login_request_rate_limited",
                    "channel": channel.upper(),
                    "scope": "IP_CHANNEL",
                },
                status_code=429,
                headers={"Retry-After": "600"},
            )
        if not self.global_limiter.allow(global_bucket, now_mono):
            return JSONResponse(
                {
                    "error": "login_request_rate_limited",
                    "channel": channel.upper(),
                    "scope": "GLOBAL_CHANNEL",
                },
                status_code=429,
                headers={"Retry-After": "30"},
            )
        token = self.console.auth.issue_one_time_access(ttl=timedelta(minutes=10))
        try:
            if channel == "email":
                delivered = await asyncio.to_thread(
                    operator_email.OperatorEmailNotifier(self.state_root).send_login_token,
                    token=token,
                    expires_minutes=10,
                )
            else:
                from wechat_traffic_agent.human_assistance import operator_notifier
                message = (
                    "Ferro Operator Console 登录请求\n"
                    "一次性登录码，10 分钟内有效且仅可使用一次：\n"
                    + token
                    + "\n如果这不是你发起的请求，请忽略。"
                )
                delivered = operator_notifier().send_text(message)
            if str(delivered.get("status") or "") != "SENT":
                raise RuntimeError("operator delivery unavailable")
        except Exception:
            self.console.auth.revoke_one_time_access(token)
            return JSONResponse(
                {
                    "error": (
                        "email_delivery_not_configured"
                        if channel == "email"
                        and not operator_email.OperatorEmailNotifier(self.state_root).status()["configured"]
                        else "login_delivery_unavailable"
                    )
                },
                status_code=503,
            )
        return JSONResponse(
            {
                "ok": True,
                "status": "SENT",
                "channel": channel.upper(),
                "expires_in_seconds": 600,
                "delivery_id": delivered.get("delivery_id"),
            },
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )

    async def operator_email_settings(self, request: Request):
        if self.console is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 8192:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("invalid email settings")
            recipient = str(body.get("recipient") or "").strip()
            config = OperatorEmailSettings(self.state_root).save_recipient(recipient)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)[:240]}, status_code=400)
        return JSONResponse(
            {
                "ok": True,
                "email": operator_email.OperatorEmailNotifier(self.state_root).status(),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def operator_login(self, request: Request):
        if self.console is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 4096:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        token = str(body.get("token") or "") if isinstance(body, dict) else ""
        authenticated = self.console.auth.verify_static_token(token)
        if not authenticated:
            authenticated = self.console.auth.consume_one_time_access(token)
        if not authenticated:
            return JSONResponse({"error": "invalid_or_expired_operator_token"}, status_code=401)
        return self.session_response(request)

    async def operator_session_status(self, request: Request):
        if self.console is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        return JSONResponse(
            {"ok": True, "authenticated": True},
            headers={"Cache-Control": "no-store"},
        )

    async def operator_status(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        payload = await asyncio.to_thread(self.console.status)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def operator_x_oauth_start(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 2048:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            username = str(body.get("username") or "") if isinstance(body, dict) else ""
            result = await asyncio.to_thread(self.oauth_manager.start, username)
        except (ValueError, XOAuthSessionError) as exc:
            return JSONResponse(
                {"error": str(exc)[:240]},
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            return JSONResponse(
                {"error": "x_oauth_start_failed"},
                status_code=502,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_x_oauth_complete(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 8192:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            session_id = str(body.get("session_id") or "") if isinstance(body, dict) else ""
            callback = str(body.get("callback") or "") if isinstance(body, dict) else ""
            result = await asyncio.to_thread(
                self.oauth_manager.complete, session_id, callback
            )
        except (ValueError, XOAuthSessionError) as exc:
            return JSONResponse(
                {"error": str(exc)[:240]},
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        except Exception:
            return JSONResponse(
                {"error": "x_oauth_complete_failed"},
                status_code=502,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_x_oauth_cancel(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            body = await request.json()
            session_id = str(body.get("session_id") or "") if isinstance(body, dict) else ""
            await asyncio.to_thread(self.oauth_manager.cancel, session_id)
        except Exception:
            return JSONResponse({"error": "x_oauth_cancel_failed"}, status_code=502)
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
