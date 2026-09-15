"""幂等交稿：同键同文复用原结果，同键异文冲突，缺键拒绝。"""
from helpers import claim, create_session, submit


def test_same_key_same_content_reuses_result(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    first = submit(client, sid, 1, token, "key-1", "小猫跳上屋顶。")
    assert first["idempotency"] == "applied"

    replay = submit(client, sid, 1, token, "key-1", "小猫跳上屋顶。")
    assert replay["idempotency"] == "replayed"
    assert replay["round"] == first["round"]
    assert replay["author"] == first["author"]
    assert replay["phase"] == first["phase"]

    # 状态没有被重复推进
    status = client.get(f"/sessions/{sid}").json()
    assert status["phase"] == "submitted"
    assert status["current_round"] == 1


def test_same_key_different_content_conflicts(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token, "key-1", "小猫跳上屋顶。")

    resp = client.post(f"/sessions/{sid}/rounds/1/submit",
                       json={"text": "完全不同的稿件。"},
                       headers={"Authorization": f"Bearer {token}",
                                "Idempotency-Key": "key-1"})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "IDEMPOTENCY_CONFLICT"
    assert err["round"] == 1
    assert err["author"] == "小明"
    assert err["next_step"]

    # 轮次未被推进
    status = client.get(f"/sessions/{sid}").json()
    assert status["phase"] == "submitted"
    assert status["current_round"] == 1


def test_different_key_after_submit_rejected(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token, "key-1", "小猫跳上屋顶。")

    resp = client.post(f"/sessions/{sid}/rounds/1/submit",
                       json={"text": "换个键再交一次。"},
                       headers={"Authorization": f"Bearer {token}",
                                "Idempotency-Key": "key-2"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ALREADY_SUBMITTED"


def test_missing_idempotency_key_rejected(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    resp = client.post(f"/sessions/{sid}/rounds/1/submit",
                       json={"text": "没有键的稿件。"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_submit_after_completion_rejected_even_with_original_key(client):
    sid = create_session(client, rounds=[{"author": "小明", "visible_tail": 1}])
    token = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token, "key-1", "唯一的一段。")
    client.post(f"/sessions/{sid}/complete")

    # 令牌已随完成作废：即使键与内容都与首次一致，也必须拒绝而非复用原结果
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": "唯一的一段。"},
                       headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "key-1"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SESSION_COMPLETED"
    status = client.get(f"/sessions/{sid}").json()
    assert status["phase"] == "completed"
