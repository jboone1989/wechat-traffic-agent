"""Session-aware browser endpoints, independent of operator chat and resource APIs."""
from __future__ import annotations

import asyncio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect
from wechat_traffic_agent.local_browser_control import LocalBrowserControlUnavailable
from wechat_traffic_agent.novnc_takeover import NoVNCUnavailable
from wechat_traffic_agent.browser_session_stream import BrowserStreamUnavailable


class OperatorBrowserHandlers:
    def __init__(self, *, state_root, console, account_manager, control, novnc,
                 authorized, websocket_authorized, write_allowed, stream_factory):
        self.state_root = state_root
        self.console = console
        self.account_manager = account_manager
        self.control = control
        self.novnc = novnc
        self.authorized = authorized
        self.websocket_authorized = websocket_authorized
        self.write_allowed = write_allowed
        self.stream_factory = stream_factory

    def routes(self):
        return {
            'operator_account_instance_delete': self.operator_account_instance_delete,
            'operator_account_stream': self.operator_account_stream,
            'operator_novnc_asset': self.operator_novnc_asset,
            'operator_novnc_websocket': self.operator_novnc_websocket,
            'operator_browser_status': self.operator_browser_status,
            'operator_account_connect': self.operator_account_connect,
            'operator_account_verify': self.operator_account_verify,
            'operator_account_snapshot': self.operator_account_snapshot,
            'operator_account_login_click': self.operator_account_login_click,
            'operator_account_login_type': self.operator_account_login_type,
            'operator_account_login_key': self.operator_account_login_key,
            'operator_account_login_stop': self.operator_account_login_stop,
            'operator_local_browser_start': self.operator_local_browser_start,
            'operator_local_browser_snapshot': self.operator_local_browser_snapshot,
            'operator_local_browser_click': self.operator_local_browser_click,
            'operator_local_browser_type': self.operator_local_browser_type,
            'operator_local_browser_key': self.operator_local_browser_key,
            'operator_local_browser_stop': self.operator_local_browser_stop,
        }

    async def operator_account_instance_delete(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 2048:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        account_id = str(request.path_params["account_id"]).strip()
        try:
            body = await request.json()
        except Exception:
            body = {}
        confirm_identity = (
            str(body.get("confirm_identity") or "").strip().lstrip("@")
            if isinstance(body, dict)
            else ""
        )
        try:
            from wechat_traffic_agent.account_registry import AccountRegistry

            registry = AccountRegistry(self.state_root)
            record = registry.get(account_id)
            if confirm_identity != record.identity:
                return JSONResponse(
                    {"error": "account_delete_confirmation_mismatch"},
                    status_code=409,
                )
            deleted = registry.remove(account_id)
        except (ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)[:240]}, status_code=404)
        except Exception as exc:
            return JSONResponse(
                {
                    "error": "account_delete_failed",
                    "error_type": type(exc).__name__,
                },
                status_code=502,
            )
        return JSONResponse(
            {
                "ok": True,
                "account_id": deleted.account_id,
                "provider_id": deleted.provider_id,
                "identity": deleted.identity,
                "status": "DELETED",
                "external_authorization_retained": (
                    deleted.provider_id == "x"
                    and deleted.oauth_status == "CONNECTED"
                ),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def operator_account_stream(self, websocket: WebSocket):
        if self.console is None:
            await websocket.close(code=4404)
            return
        if not self.websocket_authorized(websocket):
            await websocket.close(code=4401)
            return
        origin = str(websocket.headers.get("origin") or "")
        host = str(websocket.headers.get("host") or "")
        if origin:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            if parsed.netloc and parsed.netloc != host:
                await websocket.close(code=4403)
                return
        provider_id = str(websocket.path_params["provider_id"]).strip().lower()
        try:
            spec = self.account_manager.provider(provider_id)
            if not spec.implemented or provider_id != "x":
                raise ValueError("account adapter is not installed")
            self.control.target_id()
        except (ValueError, LocalBrowserControlUnavailable):
            await websocket.close(code=4409)
            return
        await websocket.accept()
        try:
            stream = self.stream_factory(self.control)
            await stream.run(
                websocket,
                authorize=lambda: self.websocket_authorized(websocket),
            )
        except WebSocketDisconnect:
            return
        except BrowserStreamUnavailable as exc:
            try:
                await websocket.send_json({
                    "type": "error",
                    "error": str(exc)[:160],
                })
            except Exception:
                pass
            try:
                await websocket.close(code=1011)
            except Exception:
                pass

    async def operator_novnc_asset(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if request.path_params["path"] in {"defaults.json", "mandatory.json"}:
            settings = {} if request.path_params["path"] == "defaults.json" else {
                "host": request.url.hostname,
                "port": request.url.port or (443 if request.url.scheme == "https" else 80),
                "encrypt": request.url.scheme == "https",
                "path": "operator/browser/x/websockify",
                "shared": True, "reconnect": True, "resize": "scale",
            }
            return JSONResponse(settings, headers={"Cache-Control": "no-store"})
        try:
            status, body, content_type = await self.novnc.asset(request.path_params["path"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (NoVNCUnavailable, OSError, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return Response(body, status_code=status, headers={
            "Content-Type": content_type, "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; "
                "base-uri 'self'; form-action 'self'"
            ),
        })

    async def operator_novnc_websocket(self, websocket: WebSocket):
        if not self.websocket_authorized(websocket):
            await websocket.close(code=4401)
            return
        from urllib.parse import urlsplit
        origin = str(websocket.headers.get("origin") or "")
        if origin and urlsplit(origin).netloc != str(websocket.headers.get("host") or ""):
            await websocket.close(code=4403)
            return
        try:
            await self.novnc.relay(
                websocket, authorize=lambda: self.websocket_authorized(websocket),
            )
        except WebSocketDisconnect:
            return
        except (NoVNCUnavailable, OSError, RuntimeError) as exc:
            import logging
            logging.getLogger(__name__).exception("Agent Runtime X noVNC failed: %s", exc)
            if websocket.application_state.name != "DISCONNECTED":
                await websocket.close(code=1011, reason=str(exc)[:120])

    def account_browser(self, request, body=None):
        account_id = str((body or {}).get("account_id") or request.query_params.get("account_id") or "").strip()
        if not account_id:
            return self.control
        try:
            return self.control.for_account(account_id)
        except KeyError as exc:
            raise ValueError("selected account is unavailable") from exc

    async def operator_browser_status(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        try:
            control = self.account_browser(request)
            await asyncio.to_thread(control.target_id)
            result = await asyncio.to_thread(control.status)
            if result.get("x_authenticated"):
                self.account_manager.record_connected("x", identity=str(result.get("account_identity") or ""))
            return JSONResponse({"browser": result}, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    async def operator_account_connect(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        provider_id = str(request.path_params["provider_id"]).strip().lower()
        try:
            spec = self.account_manager.provider(provider_id)
            if not spec.implemented or provider_id != "x":
                raise ValueError("account adapter is not installed")
            body = await request.json()
            control = self.account_browser(request, body)
            connection = await asyncio.to_thread(self.novnc.connection)
            result = await asyncio.to_thread(control.open)
            result.update(connection)
            if result.get("x_authenticated"):
                account = self.account_manager.record_connected(
                    provider_id, identity=str(result.get("account_identity") or "")
                )
            else:
                account = self.account_manager.mark_authenticating(provider_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (LocalBrowserControlUnavailable, NoVNCUnavailable) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(
            {"account": account, "browser": result},
            headers={"Cache-Control": "no-store"},
        )

    async def operator_account_verify(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        provider_id = str(request.path_params["provider_id"]).strip().lower()
        try:
            result = await asyncio.to_thread(self.account_manager.verify, provider_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_account_snapshot(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        provider_id = str(request.path_params["provider_id"]).strip().lower()
        try:
            spec = self.account_manager.provider(provider_id)
            if not spec.implemented or provider_id != "x":
                raise ValueError("account adapter is not installed")
            snap = await asyncio.to_thread(self.account_browser(request).snapshot)
            if snap.x_authenticated:
                self.account_manager.record_connected(
                    provider_id, identity=snap.account_identity
                )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "account_login_not_running"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "account_snapshot_failed"}, status_code=502)
        return JSONResponse({
            "provider_id": provider_id,
            "url": snap.url,
            "title": snap.title,
            "width": snap.width,
            "height": snap.height,
            "authenticated": snap.x_authenticated,
            "identity": snap.account_identity,
            "screenshot_b64": snap.screenshot_b64,
        }, headers={"Cache-Control": "no-store"})

    async def operator_account_login_click(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        provider_id = str(request.path_params["provider_id"]).strip().lower()
        try:
            spec = self.account_manager.provider(provider_id)
            if not spec.implemented or provider_id != "x":
                raise ValueError("account adapter is not installed")
            body = await request.json()
            result = await asyncio.to_thread(
                self.account_browser(request, body).click,
                x_ratio=float(body.get("x_ratio")),
                y_ratio=float(body.get("y_ratio")),
            )
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "account_login_not_running"}, status_code=409)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_account_login_type(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 4096:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        provider_id = str(request.path_params["provider_id"]).strip().lower()
        try:
            spec = self.account_manager.provider(provider_id)
            if not spec.implemented or provider_id != "x":
                raise ValueError("account adapter is not installed")
            body = await request.json()
            text = str(body.get("text") or "") if isinstance(body, dict) else ""
            result = await asyncio.to_thread(self.account_browser(request, body).type_text, text)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "account_login_not_running"}, status_code=409)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_account_login_key(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        provider_id = str(request.path_params["provider_id"]).strip().lower()
        try:
            spec = self.account_manager.provider(provider_id)
            if not spec.implemented or provider_id != "x":
                raise ValueError("account adapter is not installed")
            body = await request.json()
            key = str(body.get("key") or "") if isinstance(body, dict) else ""
            result = await asyncio.to_thread(self.account_browser(request, body).key, key)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "account_login_not_running"}, status_code=409)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_account_login_stop(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        provider_id = str(request.path_params["provider_id"]).strip().lower()
        try:
            spec = self.account_manager.provider(provider_id)
            if not spec.implemented or provider_id != "x":
                raise ValueError("account adapter is not installed")
            result = await asyncio.to_thread(self.account_browser(request).stop)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_local_browser_start(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            body = await request.json()
            url = str(body.get("url") or "https://x.com/i/flow/login") if isinstance(body, dict) else "https://x.com/i/flow/login"
            result = await asyncio.to_thread(self.control.start, url)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "local_browser_unavailable"}, status_code=503)
        except Exception:
            return JSONResponse({"error": "local_browser_start_failed"}, status_code=502)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_local_browser_snapshot(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        try:
            snap = await asyncio.to_thread(self.control.snapshot)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "local_browser_not_running"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "local_browser_snapshot_failed"}, status_code=502)
        return JSONResponse({
            "url": snap.url,
            "title": snap.title,
            "width": snap.width,
            "height": snap.height,
            "x_authenticated": snap.x_authenticated,
            "screenshot_b64": snap.screenshot_b64,
        }, headers={"Cache-Control": "no-store"})

    async def operator_local_browser_click(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            body = await request.json()
            result = await asyncio.to_thread(
                self.control.click,
                x_ratio=float(body.get("x_ratio")),
                y_ratio=float(body.get("y_ratio")),
            )
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "local_browser_not_running"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "local_browser_click_failed"}, status_code=502)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_local_browser_type(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 4096:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            text = str(body.get("text") or "") if isinstance(body, dict) else ""
            result = await asyncio.to_thread(self.control.type_text, text)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "local_browser_not_running"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "local_browser_type_failed"}, status_code=502)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_local_browser_key(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            body = await request.json()
            key = str(body.get("key") or "") if isinstance(body, dict) else ""
            result = await asyncio.to_thread(self.control.key, key)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except LocalBrowserControlUnavailable:
            return JSONResponse({"error": "local_browser_not_running"}, status_code=409)
        except Exception:
            return JSONResponse({"error": "local_browser_key_failed"}, status_code=502)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_local_browser_stop(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            result = await asyncio.to_thread(self.control.stop)
        except Exception:
            return JSONResponse({"error": "local_browser_stop_failed"}, status_code=502)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
