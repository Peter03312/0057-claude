"""显式状态机：约束领取、交稿、撤回、转交、完成五个事件。

会话在任意时刻处于某个 Phase，current_round 指向进行中的轮次。
所有写路径必须先经 apply()/allows() 校验，非法迁移抛 InvalidTransition。
"""
from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    AWAITING_CLAIM = "awaiting_claim"  # 等待当前轮作者领取
    CLAIMED = "claimed"                # 已领取，一次性令牌已签发
    SUBMITTED = "submitted"            # 已交稿，下一位未领取前可撤回
    COMPLETED = "completed"            # 已完成并冻结，任何写操作拒绝


class Event(str, Enum):
    CLAIM = "claim"        # 领取：签发本轮一次性令牌
    SUBMIT = "submit"      # 交稿：凭令牌 + Idempotency-Key
    RETRACT = "retract"    # 撤回：仅在下一位领取前
    HANDOFF = "handoff"    # 转交：下一位领取成功时触发，旧令牌作废
    COMPLETE = "complete"  # 完成：末轮交稿后由主持人冻结


TRANSITIONS: dict[tuple[Phase, Event], Phase] = {
    (Phase.AWAITING_CLAIM, Event.CLAIM): Phase.CLAIMED,
    (Phase.CLAIMED, Event.SUBMIT): Phase.SUBMITTED,
    (Phase.SUBMITTED, Event.RETRACT): Phase.AWAITING_CLAIM,
    (Phase.SUBMITTED, Event.HANDOFF): Phase.AWAITING_CLAIM,  # 进入下一轮
    (Phase.SUBMITTED, Event.COMPLETE): Phase.COMPLETED,
}


class InvalidTransition(Exception):
    """状态机上不存在的迁移。"""

    def __init__(self, phase: Phase, event: Event):
        self.phase = phase
        self.event = event
        super().__init__(f"非法状态迁移：{phase.value} 上不能执行 {event.value}")


def allows(phase: Phase, event: Event) -> bool:
    return (phase, event) in TRANSITIONS


def apply(phase: Phase, event: Event) -> Phase:
    """校验迁移并返回目标状态；非法迁移抛 InvalidTransition。"""
    try:
        return TRANSITIONS[(phase, event)]
    except KeyError:
        raise InvalidTransition(phase, event) from None
