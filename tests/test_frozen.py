"""冻结结果：按轮拼接的全文、署名、SHA-256 小写十六进制哈希；冻结后拒绝一切写操作；重启读取不变。"""
import hashlib

from fastapi.testclient import TestClient

from app.main import create_app
from helpers import claim, complete, create_session, submit

OPENING = "清晨，森林醒了。小鸟开始唱歌！雾气散开了吗？"
SEGMENTS = ["第一段情节。", "第二段情节！", "第三段结局。"]
AUTHORS = ["小明", "小红", "小刚"]


def _run_full_story(client, sid, segments=SEGMENTS):
    for i, (author, text) in enumerate(zip(AUTHORS, segments), start=1):
        token = claim(client, sid, i, author)
        submit(client, sid, i, token, f"key-{i}", text)
    return complete(client, sid)


def test_frozen_story_signatures_and_hash(client):
    sid = create_session(client, opening=OPENING)
    story = _run_full_story(client, sid)

    full = OPENING + "".join(SEGMENTS)
    assert story["full_text"] == full
    assert story["sha256"] == hashlib.sha256(full.encode("utf-8")).hexdigest()
    assert story["sha256"] == story["sha256"].lower()
    assert story["signatures"] == [{"round": 1, "author": "小明"},
                                   {"round": 2, "author": "小红"},
                                   {"round": 3, "author": "小刚"}]
    assert [r["text"] for r in story["rounds"]] == SEGMENTS

    # 再读一次，结果一致
    again = client.get(f"/sessions/{sid}/story").json()
    assert again["full_text"] == full
    assert again["sha256"] == story["sha256"]


def test_mutations_after_completion_rejected(client):
    sid = create_session(client)
    _run_full_story(client, sid)

    resp = client.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小明"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SESSION_COMPLETED"

    resp = client.post(f"/sessions/{sid}/complete")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ALREADY_COMPLETED"

    resp = client.post(f"/sessions/{sid}/rounds/3/retract",
                       headers={"Authorization": "Bearer x"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SESSION_COMPLETED"

    resp = client.post(f"/sessions/{sid}/rounds/3/submit", json={"text": "改写。"},
                       headers={"Authorization": "Bearer x", "Idempotency-Key": "late"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SESSION_COMPLETED"

    # 成稿不被迟到操作改写
    story = client.get(f"/sessions/{sid}/story").json()
    assert "改写" not in story["full_text"]


def test_restart_preserves_frozen_story(tmp_path):
    db = str(tmp_path / "restart.db")
    app1 = create_app(db)
    with TestClient(app1) as client1:
        sid = create_session(client1, opening=OPENING)
        _run_full_story(client1, sid)

    # 新进程（新应用实例）读同一数据库文件
    app2 = create_app(db)
    with TestClient(app2) as client2:
        story = client2.get(f"/sessions/{sid}/story").json()
        full = OPENING + "".join(SEGMENTS)
        assert story["full_text"] == full
        assert story["sha256"] == hashlib.sha256(full.encode("utf-8")).hexdigest()
        status = client2.get(f"/sessions/{sid}").json()
        assert status["phase"] == "completed"
        assert status["sha256"] == story["sha256"]


def test_restart_preserves_in_progress_state(tmp_path):
    db = str(tmp_path / "restart2.db")
    app1 = create_app(db)
    with TestClient(app1) as client1:
        sid = create_session(client1)
        token = claim(client1, sid, 1, "小明")

    app2 = create_app(db)
    with TestClient(app2) as client2:
        # 重启后令牌仍然有效，流程可继续
        resp = client2.get(f"/sessions/{sid}/rounds/1/tail",
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        status = client2.get(f"/sessions/{sid}").json()
        assert status["phase"] == "claimed"
        assert status["current_round"] == 1
