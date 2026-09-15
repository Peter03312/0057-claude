"""请求模型（Pydantic）。"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _non_empty_stripped(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label}不能只包含空白字符")
    return value


class RoundSpec(BaseModel):
    author: str = Field(min_length=1, max_length=40, description="本轮作者姓名")
    visible_tail: int = Field(ge=1, le=20, description="本轮作者可读取的上一段末句数")

    @field_validator("author")
    @classmethod
    def _strip_author(cls, value: str) -> str:
        return _non_empty_stripped(value, "作者姓名")


class CreateSessionRequest(BaseModel):
    opening: str = Field(min_length=1, max_length=4000, description="故事开头")
    rounds: list[RoundSpec] = Field(min_length=1, max_length=20, description="逐轮作者列表")


class ClaimRequest(BaseModel):
    author: str = Field(min_length=1, max_length=40, description="领取人姓名，必须等于本轮指定作者")

    @field_validator("author")
    @classmethod
    def _strip_author(cls, value: str) -> str:
        return _non_empty_stripped(value, "作者姓名")


class SubmitRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="本段稿件，至少包含一个以。！？结尾的句子")
