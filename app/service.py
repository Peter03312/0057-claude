"""业务编排：会话接口、有序状态机、可见内容投影与成稿投影。

不变量：
- 完成前任何响应都不携带隐藏段落（开头与各轮稿件），唯一的内容出口是
  当前作者凭一次性令牌读到的、按本轮 visible_tail 截取的上一段末句；
- 令牌在领取时签发，撤回 / 转交 / 完成时立即作废，旧令牌一律 401；
- 交稿按 (会话, 轮次, Idempotency-Key) 去重：同键同文复用原结果，
  同键异文 409，且两种情况都不推进轮次；
- 完成时冻结按轮拼接的全文与署名，哈希 = 冻结全文 UTF-8 字节的
  SHA-256 小写十六进制，落盘后重启读取不变。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone

from .db import Store
from .errors import ApiError
from .models import CreateSessionRequest
from .sentences import split_sentences, tail_sentences
from .statemachine import Event, Phase, apply


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ApiError(
            404, "SESSION_NOT_FOUND", f"会话 {session_id} 不存在",
            next_step="先 POST /sessions 创建会话，再使用返回的 session_id",
        )
    return row


def _load_round(conn: sqlite3.Connection, session_id: str, round_no: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM rounds WHERE session_id = ? AND round_no = ?",
        (session_id, round_no),
    ).fetchone()
    if row is None:
        total = _load_session(conn, session_id)["round_count"]
        raise ApiError(
            404, "ROUND_NOT_FOUND", f"第 {round_no} 轮不存在",
            round_no=round_no,
            next_step=f"本会话共 {total} 轮，轮次范围是 1..{total}",
        )
    return row


def _rounds(conn: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM rounds WHERE session_id = ? ORDER BY round_no", (session_id,)
    ).fetchall()


def _progress_next_step(session: sqlite3.Row, rounds: list[sqlite3.Row]) -> str:
    """根据当前状态给出可执行的下一步。"""
    sid = session["id"]
    phase = session["phase"]
    cur = session["current_round"]
    author = rounds[cur - 1]["author"]
    if phase == Phase.AWAITING_CLAIM.value:
        return f"由{author}领取第{cur}轮：POST /sessions/{sid}/rounds/{cur}/claim"
    if phase == Phase.CLAIMED.value:
        return f"等待{author}凭令牌读末句并交第{cur}轮稿"
    if phase == Phase.SUBMITTED.value:
        if cur < len(rounds):
            nxt = rounds[cur]["author"]
            return (f"等待{nxt}领取第{cur + 1}轮（转交）；"
                    f"{author}在其领取前仍可撤回第{cur}轮")
        return (f"末轮已交稿：{author}仍可撤回，"
                f"或主持人 POST /sessions/{sid}/complete 完成冻结")
    return f"故事已完成：GET /sessions/{sid}/story 查看成稿"


def _token_or_401(stored: str | None, presented: str, session, round_row, rounds) -> None:
    if stored is None or stored != presented:
        raise ApiError(
            401, "TOKEN_INVALID",
            "令牌无效或已过期（可能已被撤回、转交或会话已完成），本次操作被拒绝，轮次不变",
            round_no=round_row["round_no"], author=round_row["author"],
            next_step=_progress_next_step(session, rounds),
        )


# ---------------------------------------------------------------- 会话创建与状态投影

def create_session(store: Store, req: CreateSessionRequest) -> dict:
    if not split_sentences(req.opening):
        raise ApiError(
            400, "OPENING_WITHOUT_SENTENCE",
            "开头里没有以。！？结尾的完整句子，第一位作者将读不到任何末句",
            next_step="给开头补上至少一个以。！？结尾的句子",
        )
    session_id = secrets.token_hex(16)
    with store.write() as conn:
        conn.execute(
            "INSERT INTO sessions (id, opening, round_count, current_round, phase, created_at)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            (session_id, req.opening, len(req.rounds), Phase.AWAITING_CLAIM.value, _now()),
        )
        for i, spec in enumerate(req.rounds, start=1):
            conn.execute(
                "INSERT INTO rounds (session_id, round_no, author, visible_tail)"
                " VALUES (?, ?, ?, ?)",
                (session_id, i, spec.author, spec.visible_tail),
            )
    return get_session_status(store, session_id)


def get_session_status(store: Store, session_id: str) -> dict:
    """进度投影：只暴露状态，不暴露任何段落内容。"""
    with store.read() as conn:
        s = _load_session(conn, session_id)
        rounds = _rounds(conn, session_id)
        round_views = []
        for r in rounds:
            if s["phase"] == Phase.COMPLETED.value:
                state = "frozen"
            elif r["round_no"] < s["current_round"]:
                state = "submitted"
            elif r["round_no"] == s["current_round"]:
                state = s["phase"]
            else:
                state = "pending"
            round_views.append({
                "round": r["round_no"],
                "author": r["author"],
                "visible_tail": r["visible_tail"],
                "state": state,
            })
        body = {
            "session_id": s["id"],
            "phase": s["phase"],
            "current_round": s["current_round"],
            "round_count": s["round_count"],
            "rounds": round_views,
            "next_step": _progress_next_step(s, rounds),
            "created_at": s["created_at"],
        }
        if s["phase"] == Phase.COMPLETED.value:
            body["completed_at"] = s["completed_at"]
            body["sha256"] = s["frozen_hash"]
        return body


# ---------------------------------------------------------------- 领取（含转交）

def claim_round(store: Store, session_id: str, round_no: int, author: str) -> dict:
    with store.write() as conn:
        s = _load_session(conn, session_id)
        rounds = _rounds(conn, session_id)
        if s["phase"] == Phase.COMPLETED.value:
            raise ApiError(
                409, "SESSION_COMPLETED", "故事已完成并冻结，不能再领取",
                round_no=round_no, author=author,
                next_step=f"GET /sessions/{session_id}/story 查看成稿",
            )
        r = _load_round(conn, session_id, round_no)
        cur = s["current_round"]
        phase = Phase(s["phase"])
        handoff = phase == Phase.SUBMITTED and round_no == cur + 1
        direct = phase == Phase.AWAITING_CLAIM and round_no == cur
        if not (handoff or direct):
            raise ApiError(
                409, "ROUND_NOT_CLAIMABLE",
                f"当前进行第{cur}轮（{phase.value}），第{round_no}轮现在不能领取",
                round_no=round_no, author=author,
                next_step=_progress_next_step(s, rounds),
                details={"current_round": cur, "phase": phase.value,
                         "expected_author": r["author"]},
            )
        if author != r["author"]:
            raise ApiError(
                409, "WRONG_AUTHOR",
                f"第{round_no}轮的作者是{r['author']}，{author}不能代领",
                round_no=round_no, author=author,
                next_step=f"由{r['author']}本人领取第{round_no}轮",
                details={"expected_author": r["author"]},
            )
        if handoff:
            apply(Phase.SUBMITTED, Event.HANDOFF)
            updated = conn.execute(
                "UPDATE sessions SET phase = ?, current_round = ?"
                " WHERE id = ? AND phase = ? AND current_round = ?",
                (Phase.CLAIMED.value, round_no, session_id, Phase.SUBMITTED.value, cur),
            ).rowcount
            # 转交即作废上一轮令牌：迟到撤回与旧令牌提交随之失效
            conn.execute(
                "UPDATE rounds SET token = NULL, token_read_used = 0"
                " WHERE session_id = ? AND round_no = ?",
                (session_id, cur),
            )
        else:
            apply(Phase.AWAITING_CLAIM, Event.CLAIM)
            updated = conn.execute(
                "UPDATE sessions SET phase = ?"
                " WHERE id = ? AND phase = ? AND current_round = ?",
                (Phase.CLAIMED.value, session_id, Phase.AWAITING_CLAIM.value, round_no),
            ).rowcount
        if updated != 1:
            raise ApiError(
                409, "CLAIM_RACE_LOST", "本轮刚刚被其他人领取",
                round_no=round_no, author=author,
                next_step=f"GET /sessions/{session_id} 刷新进度",
            )
        token = secrets.token_urlsafe(24)
        conn.execute(
            "UPDATE rounds SET token = ?, token_read_used = 0"
            " WHERE session_id = ? AND round_no = ?",
            (token, session_id, round_no),
        )
    return {
        "session_id": session_id,
        "round": round_no,
        "author": r["author"],
        "phase": Phase.CLAIMED.value,
        "token": token,
        "visible_tail": r["visible_tail"],
        "next_step": (f"GET /sessions/{session_id}/rounds/{round_no}/tail 凭令牌读上一段末句"
                      f"（仅一次），然后 POST /sessions/{session_id}/rounds/{round_no}/submit 交稿"),
    }


# ---------------------------------------------------------------- 读取可见末句（一次性）

def read_tail(store: Store, session_id: str, round_no: int, token: str) -> dict:
    with store.write() as conn:
        s = _load_session(conn, session_id)
        rounds = _rounds(conn, session_id)
        r = _load_round(conn, session_id, round_no)
        _token_or_401(r["token"], token, s, r, rounds)
        if s["phase"] != Phase.CLAIMED.value or s["current_round"] != round_no:
            raise ApiError(
                409, "ROUND_NOT_READABLE",
                f"第{round_no}轮当前不可读取（会话状态 {s['phase']}）",
                round_no=round_no, author=r["author"],
                next_step=_progress_next_step(s, rounds),
            )
        if r["token_read_used"]:
            raise ApiError(
                409, "TAIL_ALREADY_READ", "末句只允许读取一次，本令牌已经读过",
                round_no=round_no, author=r["author"],
                next_step=f"凭同一令牌 POST /sessions/{session_id}/rounds/{round_no}/submit 交稿",
            )
        if round_no == 1:
            prev_text = s["opening"]
        else:
            prev = conn.execute(
                "SELECT segment FROM rounds WHERE session_id = ? AND round_no = ?",
                (session_id, round_no - 1),
            ).fetchone()
            prev_text = prev["segment"] or ""
        sentences = tail_sentences(prev_text, r["visible_tail"])
        conn.execute(
            "UPDATE rounds SET token_read_used = 1 WHERE session_id = ? AND round_no = ?",
            (session_id, round_no),
        )
    return {
        "session_id": session_id,
        "round": round_no,
        "author": r["author"],
        "visible_tail": r["visible_tail"],
        "sentences": sentences,
        "tail": "".join(sentences),
        "next_step": f"POST /sessions/{session_id}/rounds/{round_no}/submit 带 Idempotency-Key 交稿",
    }


# ---------------------------------------------------------------- 交稿（幂等）

def submit_round(store: Store, session_id: str, round_no: int,
                 token: str, idem_key: str, text: str) -> dict:
    if not split_sentences(text):
        raise ApiError(
            400, "NO_COMPLETE_SENTENCE",
            "稿件里至少要有一个以。！？结尾的完整句子，否则下一位读不到末句",
            round_no=round_no,
            next_step="补写后以。！？结束，再用同一 Idempotency-Key 重试",
        )
    text_hash = _sha256(text)
    with store.write() as conn:
        s = _load_session(conn, session_id)
        rounds = _rounds(conn, session_id)
        r = _load_round(conn, session_id, round_no)
        existing = conn.execute(
            "SELECT * FROM idempotency_keys WHERE session_id = ? AND round_no = ? AND key = ?",
            (session_id, round_no, idem_key),
        ).fetchone()
        if existing is not None:
            if existing["text_hash"] == text_hash:
                response = json.loads(existing["response_json"])
                response["idempotency"] = "replayed"
                return response
            raise ApiError(
                409, "IDEMPOTENCY_CONFLICT",
                "同一 Idempotency-Key 已用于不同内容，本次提交被拒绝，轮次不变",
                round_no=round_no, author=r["author"],
                next_step="换一个新的 Idempotency-Key，或改回与首次提交完全相同的内容",
            )
        if s["phase"] == Phase.COMPLETED.value:
            raise ApiError(
                409, "SESSION_COMPLETED", "故事已完成并冻结，不能再交稿",
                round_no=round_no, author=r["author"],
                next_step=f"GET /sessions/{session_id}/story 查看成稿",
            )
        _token_or_401(r["token"], token, s, r, rounds)
        phase = Phase(s["phase"])
        if phase != Phase.CLAIMED or s["current_round"] != round_no:
            if phase == Phase.SUBMITTED and s["current_round"] == round_no:
                raise ApiError(
                    409, "ALREADY_SUBMITTED", "本轮已经交过稿",
                    round_no=round_no, author=r["author"],
                    next_step="要修改就先撤回（POST retract），否则等待下一位领取",
                )
            raise ApiError(
                409, "ROUND_NOT_SUBMITTABLE",
                f"当前进行第{s['current_round']}轮（{phase.value}），第{round_no}轮不能交稿",
                round_no=round_no, author=r["author"],
                next_step=_progress_next_step(s, rounds),
            )
        apply(Phase.CLAIMED, Event.SUBMIT)
        now = _now()
        updated = conn.execute(
            "UPDATE sessions SET phase = ? WHERE id = ? AND phase = ? AND current_round = ?",
            (Phase.SUBMITTED.value, session_id, Phase.CLAIMED.value, round_no),
        ).rowcount
        if updated != 1:
            raise ApiError(
                409, "SUBMIT_RACE_LOST", "本轮状态刚被改变，本次提交未生效",
                round_no=round_no, author=r["author"],
                next_step=f"GET /sessions/{session_id} 刷新进度后重试",
            )
        conn.execute(
            "UPDATE rounds SET segment = ?, idempotency_key = ?, submitted_at = ?"
            " WHERE session_id = ? AND round_no = ?",
            (text, idem_key, now, session_id, round_no),
        )
        if round_no < s["round_count"]:
            nxt_author = rounds[round_no]["author"]
            next_step = (f"等待{nxt_author}领取第{round_no + 1}轮；"
                         f"{r['author']}在其领取前仍可撤回")
        else:
            next_step = (f"末轮已交稿：{r['author']}仍可撤回，"
                         f"或主持人 POST /sessions/{session_id}/complete 完成冻结")
        response = {
            "session_id": session_id,
            "round": round_no,
            "author": r["author"],
            "phase": Phase.SUBMITTED.value,
            "idempotency": "applied",
            "next_step": next_step,
        }
        conn.execute(
            "INSERT INTO idempotency_keys"
            " (session_id, round_no, key, text_hash, response_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, round_no, idem_key, text_hash,
             json.dumps(response, ensure_ascii=False), now),
        )
    return response


# ---------------------------------------------------------------- 撤回

def retract_round(store: Store, session_id: str, round_no: int, token: str) -> dict:
    with store.write() as conn:
        s = _load_session(conn, session_id)
        rounds = _rounds(conn, session_id)
        r = _load_round(conn, session_id, round_no)
        if s["phase"] == Phase.COMPLETED.value:
            raise ApiError(
                409, "SESSION_COMPLETED", "故事已冻结，不能撤回",
                round_no=round_no, author=r["author"],
                next_step=f"GET /sessions/{session_id}/story 查看成稿",
            )
        _token_or_401(r["token"], token, s, r, rounds)
        if s["phase"] != Phase.SUBMITTED.value or s["current_round"] != round_no:
            raise ApiError(
                409, "NOTHING_TO_RETRACT", "本轮还没有已提交的稿件可撤回",
                round_no=round_no, author=r["author"],
                next_step=_progress_next_step(s, rounds),
            )
        apply(Phase.SUBMITTED, Event.RETRACT)
        conn.execute(
            "UPDATE sessions SET phase = ? WHERE id = ? AND phase = ? AND current_round = ?",
            (Phase.AWAITING_CLAIM.value, session_id, Phase.SUBMITTED.value, round_no),
        )
        conn.execute(
            "UPDATE rounds SET segment = NULL, token = NULL, token_read_used = 0,"
            " idempotency_key = NULL, submitted_at = NULL"
            " WHERE session_id = ? AND round_no = ?",
            (session_id, round_no),
        )
        # 撤回的稿件连同其幂等记录一起作废；旧令牌已失效，不存在重放入口
        conn.execute(
            "DELETE FROM idempotency_keys WHERE session_id = ? AND round_no = ?",
            (session_id, round_no),
        )
    return {
        "session_id": session_id,
        "round": round_no,
        "author": r["author"],
        "phase": Phase.AWAITING_CLAIM.value,
        "next_step": f"由{r['author']}重新领取第{round_no}轮：POST /sessions/{session_id}/rounds/{round_no}/claim",
    }


# ---------------------------------------------------------------- 完成与成稿投影

def complete_session(store: Store, session_id: str) -> dict:
    with store.write() as conn:
        s = _load_session(conn, session_id)
        rounds = _rounds(conn, session_id)
        if s["phase"] == Phase.COMPLETED.value:
            raise ApiError(
                409, "ALREADY_COMPLETED", "会话已经完成并冻结",
                next_step=f"GET /sessions/{session_id}/story 查看成稿",
                details={"sha256": s["frozen_hash"]},
            )
        last = len(rounds)
        if s["phase"] != Phase.SUBMITTED.value or s["current_round"] != last:
            cur = s["current_round"]
            raise ApiError(
                409, "NOT_ALL_ROUNDS_SUBMITTED",
                f"还有轮次未完成（当前第{cur}轮，共{last}轮），不能完成",
                round_no=cur, author=rounds[cur - 1]["author"],
                next_step=_progress_next_step(s, rounds),
            )
        apply(Phase.SUBMITTED, Event.COMPLETE)
        full_text = s["opening"] + "".join(r["segment"] or "" for r in rounds)
        digest = _sha256(full_text)
        conn.execute(
            "UPDATE sessions SET phase = ?, frozen_text = ?, frozen_hash = ?, completed_at = ?"
            " WHERE id = ?",
            (Phase.COMPLETED.value, full_text, digest, _now(), session_id),
        )
        conn.execute(
            "UPDATE rounds SET token = NULL, token_read_used = 0 WHERE session_id = ?",
            (session_id,),
        )
    return get_story(store, session_id)


def get_story(store: Store, session_id: str) -> dict:
    """成稿投影：仅在冻结后可用；完成前不泄露任何段落。"""
    with store.read() as conn:
        s = _load_session(conn, session_id)
        rounds = _rounds(conn, session_id)
        if s["phase"] != Phase.COMPLETED.value:
            cur = s["current_round"]
            raise ApiError(
                409, "STORY_NOT_FROZEN", "故事尚未完成，全文与各段内容保密",
                round_no=cur, author=rounds[cur - 1]["author"],
                next_step=_progress_next_step(s, rounds),
            )
        return {
            "session_id": session_id,
            "phase": Phase.COMPLETED.value,
            "opening": s["opening"],
            "rounds": [
                {"round": r["round_no"], "author": r["author"], "text": r["segment"]}
                for r in rounds
            ],
            "full_text": s["frozen_text"],
            "signatures": [
                {"round": r["round_no"], "author": r["author"]} for r in rounds
            ],
            "sha256": s["frozen_hash"],
            "completed_at": s["completed_at"],
        }
