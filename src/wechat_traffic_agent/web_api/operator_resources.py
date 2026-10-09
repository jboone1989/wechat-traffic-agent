"""Resource, survival and credential endpoints; independent of browser/chat APIs."""
from __future__ import annotations

from datetime import datetime, timezone
from starlette.requests import Request
from starlette.responses import JSONResponse


class OperatorResourceHandlers:
    def __init__(self, *, console, wakeful, authorized, write_allowed):
        self.console = console
        self.wakeful = wakeful
        self.authorized = authorized
        self.write_allowed = write_allowed

    def routes(self):
        return {
            'operator_resource_decision': self.operator_resource_decision,
            'operator_survival_need_decision': self.operator_survival_need_decision,
            'operator_bootstrap_remote_host': self.operator_bootstrap_remote_host,
            'operator_register_remote_host': self.operator_register_remote_host,
            'operator_verify_remote_host': self.operator_verify_remote_host,
            'operator_plan_service_offload': self.operator_plan_service_offload,
            'operator_execute_service_offload': self.operator_execute_service_offload,
            'operator_store_secret': self.operator_store_secret,
            'operator_store_resource_metadata': self.operator_store_resource_metadata,
            'operator_logout': self.operator_logout,
        }

    def schedule_onboarding(self) -> bool:
        scheduler = getattr(self.wakeful, "schedule_resource_followup", None)
        if not callable(scheduler):
            return False
        scheduler(datetime.now(timezone.utc), seconds=5)
        return True

    async def operator_resource_decision(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 4096:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            decision = str(body.get("decision") or "").strip().upper() if isinstance(body, dict) else ""
            if decision not in {"APPROVE", "REJECT"}:
                raise ValueError("decision must be APPROVE or REJECT")
            result = self.console.authorize_resource_request(
                str(request.path_params["request_id"]),
                approved=(decision == "APPROVE"),
            )
            result["onboarding_scheduled"] = (
                self.schedule_onboarding() if decision == "APPROVE" else False
            )
        except FileNotFoundError:
            return JSONResponse({"error": "resource_request_not_found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "resource_decision_failed"}, status_code=500)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_survival_need_decision(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            body = await request.json()
            decision = str(body.get("decision") or "").strip().upper()
            if decision not in {"APPROVE", "REJECT"}:
                raise ValueError("decision must be APPROVE or REJECT")
            result = self.console.decide_survival_need(
                str(request.path_params["need_id"]),
                approved=(decision == "APPROVE"),
            )
            result["followup_scheduled"] = (
                self.schedule_onboarding() if decision == "APPROVE" else False
            )
        except FileNotFoundError:
            return JSONResponse({"error": "survival_need_not_found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_bootstrap_remote_host(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 8192:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("remote host bootstrap body must be an object")
            result = self.console.bootstrap_remote_host(
                host=str(body.get("host") or ""),
                port=int(body.get("port") or 22),
                username=str(body.get("username") or "root"),
                password=str(body.get("password") or ""),
                name=str(body.get("name") or ""),
            )
            result["followup_scheduled"] = self.schedule_onboarding()
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({
                "error": "remote_host_bootstrap_failed",
                "error_type": type(exc).__name__,
            }, status_code=502)
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    async def operator_register_remote_host(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("remote host body must be an object")
            capacity = body.get("capacity") if isinstance(body.get("capacity"), dict) else {}
            result = self.console.register_remote_host(
                name=str(body.get("name") or ""),
                health_url=str(body.get("health_url") or ""),
                control_url=str(body.get("control_url") or ""),
                capacity=capacity,
                reliability=str(body.get("reliability") or "UNKNOWN"),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    async def operator_verify_remote_host(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            result = self.console.verify_remote_host(str(request.path_params["host_id"]))
        except FileNotFoundError:
            return JSONResponse({"error": "remote_host_not_found"}, status_code=404)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_plan_service_offload(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("offload body must be an object")
            result = self.console.plan_service_offload(
                str(body.get("service_id") or ""),
                str(body.get("host_id") or ""),
            )
        except FileNotFoundError as exc:
            return JSONResponse({"error": "resource_not_found", "resource": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    async def operator_execute_service_offload(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        try:
            result = self.console.execute_service_offload(
                str(request.path_params["action_id"])
            )
        except FileNotFoundError:
            return JSONResponse({"error": "offload_action_not_found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({
                "error": "offload_execution_failed",
                "error_type": type(exc).__name__,
            }, status_code=502)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_store_secret(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 9216:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            value = str(body.get("value") or "") if isinstance(body, dict) else ""
            result = self.console.store_secret(str(request.path_params["env_name"]), value)
            result["onboarding_scheduled"] = self.schedule_onboarding()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "secret_store_failed"}, status_code=500)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_store_resource_metadata(self, request: Request):
        if not self.authorized(request):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > 8192:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("metadata body must be an object")
            result = self.console.store_resource_metadata(
                str(request.path_params["env_name"]), body
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "resource_metadata_store_failed"}, status_code=500)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operator_logout(self, request: Request):
        if self.console is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not self.write_allowed(request):
            return JSONResponse({"error": "operator_header_required"}, status_code=403)
        request.session.clear()
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.delete_cookie("ferro_operator_audience", path="/")
        return response
