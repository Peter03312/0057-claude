"""生产 HTTP 冒烟：对运行中的 API 走完整接龙流程。

验证：完成前不泄露、末句只读一次、幂等复用与冲突、撤回边界、
迟到操作被拒、冻结全文与 SHA-256 哈希、冻结后拒绝写操作。
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid

import httpx

BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")


def wait_for_api() -> None:
    for _ in range(60):
        try:
            resp = httpx.get(f"{BASE}/health", timeout=2.0)
            if resp.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    raise SystemExit("SMOKE FAIL: API 未就绪，/health 不可达")


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"SMOKE FAIL: {msg}")


def new_key() -> str:
    return f"smoke-{uuid.uuid4()}"


def main() -> None:
    wait_for_api()
    client = httpx.Client(base_url=BASE, timeout=10.0)

    opening = "太阳升起来了。小猫伸了个懒腰！今天会怎么样呢？"
    rounds = [
        {"author": "小明", "visible_tail": 1},
        {"author": "小红", "visible_tail": 2},
        {"author": "小刚", "visible_tail": 1},
    ]
    resp = client.post("/sessions", json={"opening": opening, "rounds": rounds})
    check(resp.status_code == 201, f"创建会话失败: {resp.text}")
    sid = resp.json()["session_id"]

    # 完成前：成稿接口拒绝且不泄露开头
    resp = client.get(f"/sessions/{sid}/story")
    check(resp.status_code == 409, "完成前成稿接口应返回 409")
    check("太阳升起来了" not in resp.text, "完成前泄露了开头内容")

    # 第 1 轮：领取 → 读末句（仅一次）→ 交稿（幂等）
    resp = client.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小明"})
    check(resp.status_code == 200, f"第1轮领取失败: {resp.text}")
    auth1 = {"Authorization": f"Bearer {resp.json()['token']}"}

    resp = client.get(f"/sessions/{sid}/rounds/1/tail", headers=auth1)
    check(resp.status_code == 200 and resp.json()["tail"] == "今天会怎么样呢？",
          f"第1轮可见末句不符: {resp.text}")
    resp = client.get(f"/sessions/{sid}/rounds/1/tail", headers=auth1)
    check(resp.status_code == 409, "末句重复读取应被拒绝")

    seg1 = "小猫跳上了屋顶。它看见了远方的山。"
    key1 = new_key()
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": seg1},
                       headers={**auth1, "Idempotency-Key": key1})
    check(resp.status_code == 200 and resp.json()["idempotency"] == "applied",
          f"第1轮交稿失败: {resp.text}")
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": seg1},
                       headers={**auth1, "Idempotency-Key": key1})
    check(resp.status_code == 200 and resp.json()["idempotency"] == "replayed",
          "同键同内容应复用原结果")
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": "被换掉的内容。"},
                       headers={**auth1, "Idempotency-Key": key1})
    check(resp.status_code == 409, "同键异文应冲突")

    # 撤回边界：下一位未领取前可撤回，撤回后旧令牌失效
    resp = client.post(f"/sessions/{sid}/rounds/1/retract", headers=auth1)
    check(resp.status_code == 200, f"撤回失败: {resp.text}")
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": "迟到稿。"},
                       headers={**auth1, "Idempotency-Key": new_key()})
    check(resp.status_code == 401, "撤回后旧令牌交稿应被拒绝")

    resp = client.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小明"})
    check(resp.status_code == 200, f"重新领取失败: {resp.text}")
    auth1 = {"Authorization": f"Bearer {resp.json()['token']}"}
    resp = client.get(f"/sessions/{sid}/rounds/1/tail", headers=auth1)
    check(resp.status_code == 200, "重新领取后应能再读末句")
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": seg1},
                       headers={**auth1, "Idempotency-Key": new_key()})
    check(resp.status_code == 200, f"重新交稿失败: {resp.text}")

    # 第 2 轮：转交后第 1 轮的迟到撤回必须被拒，且轮次不后退
    resp = client.post(f"/sessions/{sid}/rounds/2/claim", json={"author": "小红"})
    check(resp.status_code == 200, f"第2轮领取失败: {resp.text}")
    auth2 = {"Authorization": f"Bearer {resp.json()['token']}"}
    resp = client.post(f"/sessions/{sid}/rounds/1/retract", headers=auth1)
    check(resp.status_code == 401, "转交后迟到撤回应被拒绝")
    status = client.get(f"/sessions/{sid}").json()
    check(status["current_round"] == 2 and status["phase"] == "claimed",
          "迟到操作不应推进或回退轮次")

    resp = client.get(f"/sessions/{sid}/rounds/2/tail", headers=auth2)
    check(resp.status_code == 200 and resp.json()["tail"] == seg1,
          f"第2轮可见末句不符: {resp.text}")
    seg2 = "山顶上有闪闪发光的星星？不，是灯塔！"
    resp = client.post(f"/sessions/{sid}/rounds/2/submit", json={"text": seg2},
                       headers={**auth2, "Idempotency-Key": new_key()})
    check(resp.status_code == 200, f"第2轮交稿失败: {resp.text}")

    # 第 3 轮
    resp = client.post(f"/sessions/{sid}/rounds/3/claim", json={"author": "小刚"})
    check(resp.status_code == 200, f"第3轮领取失败: {resp.text}")
    auth3 = {"Authorization": f"Bearer {resp.json()['token']}"}
    resp = client.get(f"/sessions/{sid}/rounds/3/tail", headers=auth3)
    check(resp.status_code == 200 and resp.json()["tail"] == "不，是灯塔！",
          f"第3轮可见末句不符: {resp.text}")
    seg3 = "他们一起点亮了灯塔。故事圆满结束！"
    resp = client.post(f"/sessions/{sid}/rounds/3/submit", json={"text": seg3},
                       headers={**auth3, "Idempotency-Key": new_key()})
    check(resp.status_code == 200, f"第3轮交稿失败: {resp.text}")

    # 完成并冻结：校验拼接全文、署名与哈希
    resp = client.post(f"/sessions/{sid}/complete")
    check(resp.status_code == 200, f"完成失败: {resp.text}")
    story = resp.json()
    full = opening + seg1 + seg2 + seg3
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()
    check(story["full_text"] == full, "冻结全文拼接不符")
    check(story["sha256"] == digest, "冻结哈希不符")
    check([s["author"] for s in story["signatures"]] == ["小明", "小红", "小刚"],
          "署名不符")

    resp = client.get(f"/sessions/{sid}/story")
    check(resp.status_code == 200 and resp.json()["sha256"] == digest, "成稿读取不符")

    # 冻结后任何写操作被拒
    resp = client.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小明"})
    check(resp.status_code == 409, "完成后领取应被拒绝")
    resp = client.post(f"/sessions/{sid}/rounds/3/retract", headers=auth3)
    check(resp.status_code == 409, "完成后撤回应被拒绝")
    resp = client.post(f"/sessions/{sid}/complete")
    check(resp.status_code == 409, "重复完成应被拒绝")

    print("SMOKE OK: 共同故事未被偷看、漏段，也没有被迟到操作改写")


if __name__ == "__main__":
    main()
