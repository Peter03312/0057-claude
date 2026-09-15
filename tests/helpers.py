"""测试共用的流程辅助函数：每一步都断言成功，失败即测试失败。"""
from __future__ import annotations

DEFAULT_OPENING = "清晨，森林醒了。小鸟开始唱歌！雾气散开了吗？"
DEFAULT_ROUNDS = [
    {"author": "小明", "visible_tail": 1},
    {"author": "小红", "visible_tail": 2},
    {"author": "小刚", "visible_tail": 1},
]


def create_session(client, opening=DEFAULT_OPENING, rounds=None):
    resp = client.post("/sessions", json={
        "opening": opening,
        "rounds": rounds if rounds is not None else DEFAULT_ROUNDS,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def claim(client, sid, round_no, author):
    resp = client.post(f"/sessions/{sid}/rounds/{round_no}/claim", json={"author": author})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def read_tail(client, sid, round_no, token):
    resp = client.get(f"/sessions/{sid}/rounds/{round_no}/tail",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def submit(client, sid, round_no, token, key, text):
    resp = client.post(f"/sessions/{sid}/rounds/{round_no}/submit",
                       json={"text": text},
                       headers={"Authorization": f"Bearer {token}",
                                "Idempotency-Key": key})
    assert resp.status_code == 200, resp.text
    return resp.json()


def retract(client, sid, round_no, token):
    resp = client.post(f"/sessions/{sid}/rounds/{round_no}/retract",
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def complete(client, sid):
    resp = client.post(f"/sessions/{sid}/complete")
    assert resp.status_code == 200, resp.text
    return resp.json()
