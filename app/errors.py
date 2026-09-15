"""统一错误模型：每个错误都定位轮次、姓名和可执行的下一步。"""
from __future__ import annotations


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        round_no: int | None = None,
        author: str | None = None,
        next_step: str | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.round_no = round_no
        self.author = author
        self.next_step = next_step
        self.details = details or {}

    def body(self) -> dict:
        error: dict = {"code": self.code, "message": self.message}
        if self.round_no is not None:
            error["round"] = self.round_no
        if self.author is not None:
            error["author"] = self.author
        if self.next_step is not None:
            error["next_step"] = self.next_step
        if self.details:
            error["details"] = self.details
        return {"error": error}
