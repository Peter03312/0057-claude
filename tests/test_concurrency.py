"""并发争抢：多人同时领取同一轮只能一个成功；并发交稿只生效一次。"""
import threading

from fastapi.testclient import TestClient

from app.main import create_app
from helpers import create_session


def test_concurrent_claim_only_one_wins(tmp_path):
    app = create_app(str(tmp_path / "race.db"))
    with TestClient(app) as client:
        sid = create_session(client)

    results = []

    def worker():
        with TestClient(app) as c:
            resp = c.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小明"})
            results.append(resp.status_code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(200) == 1
    assert results.count(409) == 7

    with TestClient(app) as client:
        status = client.get(f"/sessions/{sid}").json()
        assert status["phase"] == "claimed"
        assert status["current_round"] == 1


def test_concurrent_submit_only_one_applies(tmp_path):
    app = create_app(str(tmp_path / "race2.db"))
    with TestClient(app) as client:
        sid = create_session(client)
        resp = client.post(f"/sessions/{sid}/rounds/1/claim", json={"author": "小明"})
        token = resp.json()["token"]

    outcomes = []

    def worker(i):
        with TestClient(app) as c:
            resp = c.post(f"/sessions/{sid}/rounds/1/submit",
                          json={"text": f"第{i}个并发稿件。"},
                          headers={"Authorization": f"Bearer {token}",
                                   "Idempotency-Key": f"race-{i}"})
            outcomes.append(resp.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count(200) == 1
    assert outcomes.count(409) == 5

    with TestClient(app) as client:
        status = client.get(f"/sessions/{sid}").json()
        assert status["phase"] == "submitted"
        assert status["current_round"] == 1

        # 只推进了一次：走完全程，全文里恰好包含一份并发稿件
        resp = client.post(f"/sessions/{sid}/rounds/2/claim", json={"author": "小红"})
        token2 = resp.json()["token"]
        client.post(f"/sessions/{sid}/rounds/2/submit", json={"text": "第二段。"},
                    headers={"Authorization": f"Bearer {token2}", "Idempotency-Key": "k2"})
        resp = client.post(f"/sessions/{sid}/rounds/3/claim", json={"author": "小刚"})
        token3 = resp.json()["token"]
        client.post(f"/sessions/{sid}/rounds/3/submit", json={"text": "第三段。"},
                    headers={"Authorization": f"Bearer {token3}", "Idempotency-Key": "k3"})
        story = client.post(f"/sessions/{sid}/complete").json()
        assert story["full_text"].count("并发稿件") == 1
