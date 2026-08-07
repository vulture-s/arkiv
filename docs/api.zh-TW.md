# arkiv API — 驗證與 Chat（繁體中文）

> 這頁是 day-2 內容：先看 [README](../README.zh-TW.md) 把 arkiv 跑起來、匯入素材，再回來接 API。
> English: [docs/api.md](api.md)

---

## API 驗證

所有 `/api/*` 端點都需要帶有正確 scope 的 Bearer token。這種以 scope 為基礎的 token 可以把整個機器群組拆開管理：只讀審片機可用 `videos_read` 或 `media_read`，匯入機可用 `ingest_write`，管理機可用 `admin`。

第一次啟動時先做 bootstrap：

```bash
export ARKIV_ADMIN_BOOTSTRAP_TOKEN=$(openssl rand -base64 32)
python server.py
```

第一次啟動時，server 會用這個 env var 建立一組 `admin` token。先用它建立各機器專用 token，之後再移除該 env 並撤銷 bootstrap token。

直接用 CLI 建立與管理 token：

```bash
python arkiv_token.py create --name "PC-dev" --scopes videos_read,videos_write --ip-allowlist 127.0.0.1/32,100.64.0.0/10 --expires-in 90
python arkiv_token.py list
python arkiv_token.py show <token-id>
python arkiv_token.py revoke <token-id>
```

在請求中使用 token：

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8501/api/media
```

可用 scopes：`videos_read`、`videos_write`、`media_read`、`collections_read`、`collections_write`、`projects_read`、`projects_write`、`ingest_write`、`chat_read`、`chat_write`、`admin`

### Chat API — 素材庫 RAG 問答

你可以用自然語言詢問素材庫。分類器會把每個 prompt 交給五個 handler 其中之一：

| Intent | 範例 | 做什麼 |
|--------|------|--------|
| `compilation` | 「給我五月所有黃昏鏡頭」 | 語意搜尋 → 排序後的 scene 清單 |
| `refinement` | 「只要室內的」 | 在對話中對*上一輪結果*再篩選 |
| `similarity` | 「找跟 scene 42 類似的」 | 對參考鏡頭做向量最近鄰 |
| `analytics` | 「這個月總共拍了幾小時？」 | 對素材庫做統計查詢 |
| `general` | 「你能幫我做什麼？」 | 純 LLM 問答，不查庫 |

對話歷史（最近 10 則）會帶入每次後續回應，所以 `refinement` 是對上一輪傳回的結果做篩選。

**模型需求**：chat 用 `ARKIV_CHAT_MODEL`（預設 `qwen2.5:14b`）同時處理*意圖分類與回答* —— 一個 `ollama pull qwen2.5:14b` 就夠。只有當較小模型（例如 `qwen2.5:7b-instruct`）確實已裝在 Ollama 主機上時，才設 `ARKIV_INTENT_MODEL`。模型缺失時 `/api/chat` 會回清楚的「請 ollama pull …」訊息而非 500。

**前置條件 —— 先 ingest + 建索引**：chat 查的是*已建索引*的素材庫，不是獨立聊天機器人。先 ingest 素材（Step 1）+ 跑 `python embed.py` 建索引（Step 2）再用 chat。`compilation` / `refinement` / `similarity` 需要向量索引；`analytics` 只要 ingest 過；`general` 是唯一空庫也能用的 intent。空庫 / 未建索引時 chat 不會報錯，只會回「找到 0 個」。

```bash
# 建立對話
curl -X POST http://localhost:8501/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "給我所有黃昏鏡頭"}'
# → {"conversation_id":"…", "assistant_text":"…", "scene_ids":[…], "intent":"compilation", …}

# 延續同一個對話 —— refinement 會對上一輪結果操作
curl -X POST http://localhost:8501/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "只要室內的", "conversation_id": "abc123"}'

# 把對話限定在特定 project
curl -X POST http://localhost:8501/api/chat -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "寬景鏡頭", "project_scope": ["client-acme"]}'
```

用 `GET /api/chat/history/{conversation_id}` 讀回歷史、`GET /api/chat/conversations` 列出對話（都需要 `chat_read`）。
