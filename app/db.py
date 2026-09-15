"""SQLite 存储层。

单连接 + 可重入写锁：所有写事务以 BEGIN IMMEDIATE 开启，
配合条件 UPDATE 的受影响行数判定，保证并发领取只有一个成功。
状态全部落盘，进程重启后读取不变。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    opening       TEXT NOT NULL,
    round_count   INTEGER NOT NULL,
    current_round INTEGER NOT NULL,
    phase         TEXT NOT NULL,
    frozen_text   TEXT,
    frozen_hash   TEXT,
    created_at    TEXT NOT NULL,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS rounds (
    session_id      TEXT NOT NULL REFERENCES sessions (id),
    round_no        INTEGER NOT NULL,
    author          TEXT NOT NULL,
    visible_tail    INTEGER NOT NULL,
    segment         TEXT,
    token           TEXT,
    token_read_used INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    submitted_at    TEXT,
    PRIMARY KEY (session_id, round_no)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    session_id    TEXT NOT NULL,
    round_no      INTEGER NOT NULL,
    key           TEXT NOT NULL,
    text_hash     TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, round_no, key)
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """互斥写事务：进入即 BEGIN IMMEDIATE，异常自动回滚。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()
