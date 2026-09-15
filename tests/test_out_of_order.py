"""乱序事件：抢跑、跳轮、重复领取、重复读取、提前完成、越界轮次等都必须被状态机拒绝。"""
from helpers import claim, create_session, read_tail, submit


def test_submit_before_claim_rejected(client):
    sid = create_session(client)
    resp = client.post(f"/sessions/{sid}/rounds/1/submit", json={"text": "抢跑稿。"},
                       headers={"Authorization": "Bearer nobody", "Idempotency-Key": "k1"})
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["code"] == "TOKEN_INVALID"
    assert err["round"] == 1
    assert err["next_step"]


def test_claim_future_round_rejected(client):
    sid = create_session(client)
    resp = client.post(f"/sessions/{sid}/rounds/2/claim", json={"author": "小红"})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "ROUND_NOT_CLAIMABLE"
    assert err["round"] == 2
    assert err["details"]["expected_author"] == "小红"
    assert "小明" in err["next_step"]  # 指向当前应行动的人


def test_wrong_author_claim_rejected(client):
    sid = create_session(client)
    resp = client.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小红"})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "WRONG_AUTHOR"
    assert err["details"]["expected_author"] == "小明"
    assert "小明" in err["next_step"]


def test_double_claim_rejected(client):
    sid = create_session(client)
    claim(client, sid, 1, "小明")
    resp = client.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小明"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ROUND_NOT_CLAIMABLE"


def test_tail_read_only_once(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    read_tail(client, sid, 1, token)
    resp = client.get(f"/sessions/{sid}/rounds/1/tail",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "TAIL_ALREADY_READ"
    assert err["round"] == 1
    assert err["author"] == "小明"


def test_complete_before_all_rounds_rejected(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token, "k1", "第一段。")
    resp = client.post(f"/sessions/{sid}/complete")
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "NOT_ALL_ROUNDS_SUBMITTED"
    assert err["round"] == 1
    assert "小红" in err["next_step"]


def test_story_before_completion_rejected(client):
    sid = create_session(client)
    resp = client.get(f"/sessions/{sid}/story")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "STORY_NOT_FROZEN"


def test_unknown_session_and_round(client):
    resp = client.get("/sessions/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"

    sid = create_session(client)
    resp = client.post(f"/sessions/{sid}/rounds/99/claim", json={"author": "小明"})
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "ROUND_NOT_FOUND"
    assert err["round"] == 99


def test_opening_without_sentence_rejected(client):
    resp = client.post("/sessions", json={
        "opening": "没有句末符号的开头",
        "rounds": [{"author": "小明", "visible_tail": 1}],
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "OPENING_WITHOUT_SENTENCE"


def test_submit_without_sentence_rejected(client):
    sid = create_session(client)
    token = claim(client, sid, 1, "小明")
    resp = client.post(f"/sessions/{sid}/rounds/1/submit",
                       json={"text": "写了一半没有结尾"},
                       headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k1"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "NO_COMPLETE_SENTENCE"
