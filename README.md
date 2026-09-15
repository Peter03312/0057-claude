# 只看上一段末尾 · 接龙故事 API

面向 9–12 岁孩子的家庭 / 阅读小组共同创作游戏：主持人开好头并排定每轮作者，
每位作者**只能看到上一段的末尾几句**，写完自己的一段后依次传递，最后冻结成稿。

纯后端 API：Python + FastAPI + Pydantic + SQLite。

## 游戏规则与保证

- 主持人用 JSON 提交**开头**、**逐轮作者列表**和**每轮可见末句数**；
- 句界只按 `。！？` 划分：**连续句末符归同一句**（`太好了！！` 是一句），
  **未闭合片段不计**（末尾没有句末符的内容不算句子，也不会被下一位读到）；
- 当前作者凭**一次性令牌**读取允许公开的尾句（只读一次），
  并以 `Idempotency-Key` 交稿；
- 显式状态机约束**领取、交稿、撤回、转交、完成**：
  - 同键同内容复用原结果（`idempotency: "replayed"`），同键异文 `409` 冲突；
  - 幂等复用只在令牌有效的窗口内成立：令牌一旦作废（撤回 / 转交 / 完成），
    先验授权再谈幂等——即使键与内容完全相同，旧令牌的提交也一律 `401`；
  - 并发领取同一轮**只有一个成功**（条件 UPDATE + BEGIN IMMEDIATE）；
  - **下一位未领取前可撤回**；下一位领取（转交）后，迟到撤回或旧令牌提交
    一律 `401` 拒绝，且**不推进轮次**；
- **完成前任何接口不泄露隐藏段落**：进度接口只暴露状态，成稿接口 409，
  唯一的内容出口是当前作者凭令牌读到的、按本轮配置截取的末句；
- 完成后**冻结**按轮拼接的全文与署名，哈希 = 冻结全文 UTF-8 字节的
  **SHA-256 小写十六进制**；状态全部落盘 SQLite，**重启读取不变**。

## 状态机

```
                 claim                submit
 awaiting_claim ────────► claimed ──────────► submitted
       ▲                                            │
       │                 retract                    │ handoff（下一位领取）
       └────────────────────────────────────────────┤
                                                    │ complete（仅末轮）
                                                    ▼
                                                completed（冻结）
```

令牌生命周期：领取时签发 → 撤回 / 转交 / 完成时立即作废。

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/sessions` | 创建会话：`{opening, rounds: [{author, visible_tail}]}` |
| GET | `/sessions/{sid}` | 进度投影（不含任何段落内容） |
| POST | `/sessions/{sid}/rounds/{n}/claim` | 领取第 n 轮，返回一次性 `token` |
| GET | `/sessions/{sid}/rounds/{n}/tail` | 凭令牌读上一段末句（**仅一次**） |
| POST | `/sessions/{sid}/rounds/{n}/submit` | 凭令牌 + `Idempotency-Key` 交稿 |
| POST | `/sessions/{sid}/rounds/{n}/retract` | 凭令牌撤回（下一位领取前） |
| POST | `/sessions/{sid}/complete` | 末轮交稿后完成并冻结 |
| GET | `/sessions/{sid}/story` | 成稿投影：全文、署名、SHA-256（完成前 409） |
| GET | `/health` | 健康检查 |

### 示例

```bash
# 1. 主持人创建会话
curl -s -X POST localhost:8000/sessions -H 'Content-Type: application/json' -d '{
  "opening": "清晨，森林醒了。小鸟开始唱歌！",
  "rounds": [
    {"author": "小明", "visible_tail": 1},
    {"author": "小红", "visible_tail": 2}
  ]}'
# => {"session_id": "...", "phase": "awaiting_claim", ...}

# 2. 小明领取第 1 轮
curl -s -X POST localhost:8000/$SID/rounds/1/claim -d '{"author": "小明"}' ...
# => {"token": "...", "visible_tail": 1, ...}

# 3. 读上一段末句（一次性）
curl -s localhost:8000/$SID/rounds/1/tail -H "Authorization: Bearer $TOKEN"
# => {"sentences": ["小鸟开始唱歌！"], "tail": "小鸟开始唱歌！", ...}

# 4. 交稿（幂等）
curl -s -X POST localhost:8000/$SID/rounds/1/submit \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" \
  -d '{"text": "歌声惊醒了一朵蘑菇。"}'

# 5. 小红领取第 2 轮（转交）→ 读 → 交稿 … 末轮交稿后：
curl -s -X POST localhost:8000/$SID/complete
curl -s localhost:8000/$SID/story
# => {"full_text": "...", "signatures": [...], "sha256": "..."}
```

### 错误格式

所有错误都定位**轮次、姓名和可执行的下一步**：

```json
{
  "error": {
    "code": "WRONG_AUTHOR",
    "message": "第1轮的作者是小明，小红不能代领",
    "round": 1,
    "author": "小红",
    "next_step": "由小明本人领取第1轮",
    "details": {"expected_author": "小明"}
  }
}
```

主要错误码：`SESSION_NOT_FOUND` / `ROUND_NOT_FOUND`(404)，`TOKEN_MISSING` /
`TOKEN_INVALID`(401，旧令牌、迟到撤回)，`IDEMPOTENCY_KEY_REQUIRED` /
`NO_COMPLETE_SENTENCE` / `OPENING_WITHOUT_SENTENCE`(400)，
`ROUND_NOT_CLAIMABLE` / `WRONG_AUTHOR` / `CLAIM_RACE_LOST` / `ALREADY_SUBMITTED` /
`IDEMPOTENCY_CONFLICT` / `TAIL_ALREADY_READ` / `NOTHING_TO_RETRACT` /
`NOT_ALL_ROUNDS_SUBMITTED` / `SESSION_COMPLETED` / `ALREADY_COMPLETED` /
`STORY_NOT_FROZEN`(409)。

## 运行

### 本地

```bash
pip install -r requirements.txt
uvicorn app.main:create_app --factory --port 8000
# 数据库路径默认 ./story.db，可用 DATABASE_PATH 覆盖
```

### Docker Compose

```bash
API_PORT=8080 docker compose up -d api   # 只有 api 保持运行，宿主 8080 → 容器 8000
docker compose run --rm verify           # 一次性：测试套件 + 生产 HTTP 冒烟
```

`verify` 服务等 api 健康后执行 `pytest` 与 `scripts/smoke.py`（对运行中的
api 走完整接龙流程），随后退出；api 数据保存在命名卷 `story-data` 中，
容器重启后成稿与哈希读取不变。

## 测试

```bash
python -m pytest tests -q
```

覆盖：并发争抢（8 线程抢领只有 1 个成功、并发交稿只生效一次）、
幂等冲突（同键同文复用 / 同键异文 409）、撤回边界（下一位领取前后、
旧令牌提交、末轮完成前）、乱序事件（抢跑、跳轮、重复领取、提前完成等）、
隐藏内容（完成前任何接口不泄露、末句严格按配置截取）、
冻结结果（拼接全文、署名、SHA-256、冻结后拒绝写操作、重启读取不变），
以及句界切分单元测试。

## 项目结构

```
app/
  sentences.py     句界切分（。！？，连续归并，未闭合不计）
  statemachine.py  显式状态机（Phase × Event → Phase）
  db.py            SQLite 存储（BEGIN IMMEDIATE 写事务 + 写锁）
  service.py       业务编排：会话、令牌、幂等、投影、冻结
  routes.py        HTTP 路由（薄层）
  models.py        Pydantic 请求模型
  errors.py        统一错误模型（轮次 / 姓名 / 下一步）
  main.py          应用工厂
tests/             pytest 套件
scripts/smoke.py   生产 HTTP 冒烟
```
