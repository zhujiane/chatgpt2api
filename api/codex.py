from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from api.support import require_admin
from services.codex_service import codex_service


class CodexLoginRequest(BaseModel):
    name: str = ""
    mode: str = "browser"


class CodexUserUpdateRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class CodexExecRequest(BaseModel):
    prompt: str = ""
    user_id: str | None = None
    cwd: str | None = None
    model: str | None = None
    sandbox: str = "workspace-write"
    timeout_secs: int = 1800


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/codex/users")
    async def list_codex_users(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {
            "items": codex_service.list_users(),
            "default_user_id": codex_service.get_default_user_id(),
        }

    @router.post("/api/codex/login")
    async def start_codex_login(body: CodexLoginRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = await run_in_threadpool(codex_service.start_login, body.name, body.mode)
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc
        return {"item": item, "items": codex_service.list_users()}

    @router.get("/api/codex/users/{user_id}")
    async def get_codex_user(user_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = codex_service.login_state(user_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"item": item}

    @router.post("/api/codex/users/{user_id}")
    async def update_codex_user(
        user_id: str,
        body: CodexUserUpdateRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        updates = {key: value for key, value in body.model_dump().items() if value is not None}
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "还没有检测到改动，请修改后再保存"})
        try:
            item = codex_service.update_user(user_id, updates)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"item": item, "items": codex_service.list_users()}

    @router.delete("/api/codex/users/{user_id}")
    async def delete_codex_user(user_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            removed = codex_service.delete_user(user_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if not removed:
            raise HTTPException(status_code=404, detail={"error": "codex user not found"})
        return {
            "removed": 1,
            "items": codex_service.list_users(),
            "default_user_id": codex_service.get_default_user_id(),
        }

    @router.post("/api/codex/users/{user_id}/default")
    async def set_default_codex_user(user_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = codex_service.set_default_user(user_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {
            "item": item,
            "items": codex_service.list_users(),
            "default_user_id": codex_service.get_default_user_id(),
        }

    @router.post("/api/codex/users/{user_id}/refresh")
    async def refresh_codex_user(user_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = await run_in_threadpool(codex_service.refresh_status, user_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"item": item, "items": codex_service.list_users()}

    @router.post("/api/codex/exec")
    async def run_codex_exec(body: CodexExecRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            result = await run_in_threadpool(
                codex_service.run_exec,
                body.prompt,
                body.user_id,
                body.cwd,
                body.model,
                body.sandbox,
                body.timeout_secs,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc
        return {"result": result}

    return router
