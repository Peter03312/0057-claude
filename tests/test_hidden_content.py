"""隐藏内容：完成前任何接口不得泄露隐藏段落；可见末句严格按本轮配置截取。"""
from helpers import claim, create_session, read_tail, submit


def test_tail_only_exposes_allowed_sentences(client):
    opening = "第一句开场。第二句继续！！第三句提问？未闭合的尾巴"
    sid = create_session(client, opening=opening,
                         rounds=[{"author": "小明", "visible_tail": 2},
                                 {"author": "小红", "visible_tail": 1}])
    token = claim(client, sid, 1, "小明")
    tail = read_tail(client, sid, 1, token)
    # 连续句末符归同一句；未闭合片段不计
    assert tail["sentences"] == ["第二句继续！！", "第三句提问？"]
    assert "第一句开场" not in tail["tail"]
    assert "未闭合" not in tail["tail"]


def test_hidden_segments_never_leak_before_completion(client):
    sid = create_session(client)
    token1 = claim(client, sid, 1, "小明")
    submit(client, sid, 1, token1, "k1", "秘密段落一号。藏起来的句子。")

    # 进度接口不含任何段落内容
    status_resp = client.get(f"/sessions/{sid}")
    assert status_resp.status_code == 200
    assert "秘密段落" not in status_resp.text
    assert "藏起来" not in status_resp.text
    assert "清晨" not in status_resp.text  # 开头同样不泄露

    # 成稿接口在完成前拒绝且不泄露
    story_resp = client.get(f"/sessions/{sid}/story")
    assert story_resp.status_code == 409
    assert "秘密段落" not in story_resp.text
    assert "清晨" not in story_resp.text

    # 第 2 棒 visible_tail=2：只能读到上一段的两个末句，看不到开头
    token2 = claim(client, sid, 2, "小红")
    tail = read_tail(client, sid, 2, token2)
    assert tail["sentences"] == ["秘密段落一号。", "藏起来的句子。"]
    assert "清晨" not in tail["tail"]

    # 第 3 棒 visible_tail=1：只见上一段最后一句
    submit(client, sid, 2, token2, "k2", "第二段第一句。第二段第二句！")
    token3 = claim(client, sid, 3, "小刚")
    tail3 = read_tail(client, sid, 3, token3)
    assert tail3["sentences"] == ["第二段第二句！"]
    assert "第二段第一句" not in tail3["tail"]
    assert "秘密段落" not in tail3["tail"]


def test_tail_requires_token(client):
    sid = create_session(client)
    resp = client.get(f"/sessions/{sid}/rounds/1/tail")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_MISSING"

    resp = client.get(f"/sessions/{sid}/rounds/1/tail",
                      headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"
