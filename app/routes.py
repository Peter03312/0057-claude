"""HTTP 路由：薄层，只做鉴权头解析与参数校验，业务全部在 service。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from . import service
from .db import Store
from .errors import ApiError
from .models import ClaimRequest, CreateSessionRequest, SubmitRequest

router = APIRouter()


def get_store(request: Request) -> Store:
    return request.app.state.store


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise ApiError(
            401, "TOKEN_MISSING", "缺少 Authorization 头",
            next_step="先领取本轮拿到一次性令牌，再带 Authorization: Bearer <令牌> 调用",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(
            401, "TOKEN_MISSING", "Authorization 头必须是 Bearer <令牌>",
            next_step="使用领取接口返回的 token",
        )
    return token.strip()


@router.post("/sessions", status_code=201)
def create_session(req: CreateSessionRequest, store: Store = Depends(get_store)):
    return service.create_session(store, req)


@router.get("/sessions/{session_id}")
def session_status(session_id: str, store: Store = Depends(get_store)):
    return service.get_session_status(store, session_id)


@router.post("/sessions/{session_id}/rounds/{round_no}/claim")
def claim_round(session_id: str, round_no: int, req: ClaimRequest,
                store: Store = Depends(get_store)):
    return service.claim_round(store, session_id, round_no, req.author)


@router.get("/sessions/{session_id}/rounds/{round_no}/tail")
def read_tail(session_id: str, round_no: int,
              authorization: str | None = Header(default=None),
              store: Store = Depends(get_store)):
    return service.read_tail(store, session_id, round_no, bearer_token(authorization))


@router.post("/sessions/{session_id}/rounds/{round_no}/submit")
def submit_round(session_id: str, round_no: int, req: SubmitRequest,
                 authorization: str | None = Header(default=None),
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                 store: Store = Depends(get_store)):
    if idempotency_key is None or not idempotency_key.strip():
        raise ApiError(
            400, "IDEMPOTENCY_KEY_REQUIRED", "交稿必须携带 Idempotency-Key 头",
            round_no=round_no,
            next_step="为本次交稿生成一个唯一 Idempotency-Key（如 UUID），重试时保持不变",
        )
    return service.submit_round(store, session_id, round_no, bearer_token(authorization),
                                idempotency_key.strip(), req.text)


@router.post("/sessions/{session_id}/rounds/{round_no}/retract")
def retract_round(session_id: str, round_no: int,
                  authorization: str | None = Header(default=None),
                  store: Store = Depends(get_store)):
    return service.retract_round(store, session_id, round_no, bearer_token(authorization))


@router.post("/sessions/{session_id}/complete")
def complete_session(session_id: str, store: Store = Depends(get_store)):
    return service.complete_session(store, session_id)


@router.get("/sessions/{session_id}/story")
def get_story(session_id: str, store: Store = Depends(get_store)):
    return service.get_story(store, session_id)


@router.get("/health")
def health():
    return {"status": "ok"}
