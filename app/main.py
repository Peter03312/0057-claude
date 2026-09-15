"""应用工厂：uvicorn app.main:create_app --factory"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .db import Store
from .errors import ApiError
from .routes import router

DEFAULT_DB_PATH = "./story.db"


def create_app(db_path: str | None = None) -> FastAPI:
    path = db_path or os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    store = Store(path)
    app = FastAPI(title="只看上一段末尾 · 接龙故事 API", version="1.0.0")
    app.state.store = store

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"error": {
            "code": "INVALID_REQUEST",
            "message": "请求参数不合法",
            "next_step": "对照 README 的接口契约检查 JSON 字段与类型",
            "details": {"errors": errors},
        }})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": {
            "code": "HTTP_ERROR",
            "message": str(exc.detail),
            "next_step": "检查请求路径与方法是否正确",
        }})

    app.include_router(router)
    return app
