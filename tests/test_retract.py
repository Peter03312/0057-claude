"""撤回边界：下一位未领取前可撤回；领取后迟到撤回与旧令牌提交必须拒绝且不推进轮次。"""
from helpers import claim, create_session, read_tail, retract, submit


def test_retract_before_next_claim_succeeds(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token, "k1", "第一段。继续。")

    body = retract(client, sid, 1, token)
    assert body["phase"] == "awaiting_claim"
    status = client.get(f"/sessions/{sid}").json()
    assert status["phase"] == "awaiting_claim"
    assert status["current_round"] == 1

    # 撤回后旧令牌全部失效
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": "迟到的稿。"},
                       headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k2"})
    assert resp.status_code == 401
    resp = client.get(f"/sessions/{sid}/rounds/1/tail",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401

    # 重新领取后可读可交
    token2 = claim(client, sid, 1, "小明")
    tail = read_tail(client, sid, 1, token2)
    assert tail["sentences"]
    submit(client, sid, 1, token2, "k3", "重写的第一段。")
    status = client.get(f"/sessions/{sid}").json()
    assert status["phase"] == "submitted"


def test_late_retract_and_stale_token_after_handoff_rejected(client):
    sid = create_session(client)
    token1 = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token1, "k1", "第一段定稿。")
    token2 = claim(client, sid, 2, "小红")  # 转交发生，旧令牌作废

    # 迟到撤回被拒
    resp = client.post(f"/sessions/{sid}/rounds/1/retract",
                       headers={"Authorization": f"Bearer {token1}"})
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["code"] == "TOKEN_INVALID"
    assert err["round"] == 1
    assert err["author"] == "小明"
    assert err["next_step"]

    # 旧令牌提交被拒
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": "迟到稿。"},
                       headers={"Authorization": f"Bearer {token1}", "Idempotency-Key": "k9"})
    assert resp.status_code == 401

    # 轮次未被推进也未回退：仍在第 2 轮 claimed
    status = client.get(f"/sessions/{sid}").json()
    assert status["current_round"] == 2
    assert status["phase"] == "claimed"

    # 第 1 段内容未被迟到操作改写
    submit(client, sid, 2, token2, "k2", "第二段来了。")
    token3 = claim(client, sid, 3, "小刚")
    submit(client, sid, 3, token3, "k3", "最后一段。")
    story = client.post(f"/sessions/{sid}/complete").json()
    assert "第一段定稿。" in story["full_text"]
    assert "迟到稿。" not in story["full_text"]


def test_retract_without_submit_rejected(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    resp = client.post(f"/sessions/{sid}/rounds/1/retract",
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NOTHING_TO_RETRACT"


def test_retract_last_round_before_completion(client):
    sid = create_session(client, rounds=[{"author": "小明", "visible_tail": 1}])
    token = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token, "k1", "唯一的一段。")

    # 末轮在完成前仍可撤回
    body = retract(client, sid, 1, token)
    assert body["phase"] == "awaiting_claim"

    # 撤回后不能完成
    resp = client.post(f"/sessions/{sid}/complete")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NOT_ALL_ROUNDS_SUBMITTED"
