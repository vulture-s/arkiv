# arkiv API — Authentication & Chat

> Day-2 material: get arkiv running and ingest some footage first (see the [README](../README.md)), then come back for the API.
> 繁體中文: [docs/api.zh-TW.md](api.zh-TW.md)

---

## API Authentication

All `/api/*` endpoints require a Bearer token with the correct scope. Scope-based tokens let you split a fleet by machine role: read-only review stations can use `videos_read` or `media_read`, ingest machines can use `ingest_write`, and admin machines can manage tokens.

First-time bootstrap:

```bash
export ARKIV_ADMIN_BOOTSTRAP_TOKEN=$(openssl rand -base64 32)
python server.py
```

On first startup, the server seeds a single `admin` token from that env var. Use it once to create per-machine tokens, then unset it and revoke the bootstrap token.

Create and manage tokens directly with the CLI:

```bash
python arkiv_token.py create --name "PC-dev" --scopes videos_read,videos_write --ip-allowlist 127.0.0.1/32,100.64.0.0/10 --expires-in 90
python arkiv_token.py list
python arkiv_token.py show <token-id>
python arkiv_token.py revoke <token-id>
```

Use the token in requests:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8501/api/media
```

Available scopes: `videos_read`, `videos_write`, `media_read`, `collections_read`, `collections_write`, `projects_read`, `projects_write`, `ingest_write`, `chat_read`, `chat_write`, `admin`

### Chat API — RAG over your video library

Ask natural-language questions about your archive. The classifier routes each prompt to one of five handlers:

| Intent | Example | What it does |
|--------|---------|--------------|
| `compilation` | "Give me all sunset shots from May" | Semantic search → ranked scene list |
| `refinement` | "Only the indoor ones" | Filters the *previous* result, in-conversation |
| `similarity` | "Similar to scene 42" | Vector nearest-neighbours to a reference clip |
| `analytics` | "How many hours did I shoot this month?" | Aggregate query over the library |
| `general` | "What can you help me with?" | Plain LLM chat, no search |

Conversation history (last 10 messages) is threaded into each follow-up, so `refinement` acts on what the previous turn returned.

**Model requirement:** chat uses `ARKIV_CHAT_MODEL` (default `qwen2.5:14b`) for *both* intent classification and answers — a single `ollama pull qwen2.5:14b` covers it. Only set `ARKIV_INTENT_MODEL` to a smaller model (e.g. `qwen2.5:7b-instruct`) if that model is actually installed on the Ollama host. If the model is missing, `/api/chat` returns a clear "run ollama pull …" message instead of a 500.

**Prerequisite — ingest + index first:** chat queries your *indexed* library, not a standalone chatbot. Ingest media (Step 1) and build the index with `python embed.py` (Step 2) before chatting. `compilation` / `refinement` / `similarity` need the vector index; `analytics` needs ingested media only; `general` is the only intent that works on an empty library. On an empty/unindexed library chat does not error — it just returns "0 results".

```bash
# Create a conversation
curl -X POST http://localhost:8501/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Give me all sunset shots"}'
# → {"conversation_id":"…", "assistant_text":"…", "scene_ids":[…], "intent":"compilation", "tokens_used":…, "latency_ms":…}

# Continue the same conversation — refinement acts on the prior result
curl -X POST http://localhost:8501/api/chat \
  -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Only indoor ones", "conversation_id": "abc123"}'

# Scope a conversation to specific projects
curl -X POST http://localhost:8501/api/chat -H "Authorization: Bearer $ARKIV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "wide establishing shots", "project_scope": ["client-acme"]}'
```

Read history with `GET /api/chat/history/{conversation_id}` and list conversations with `GET /api/chat/conversations` (both need `chat_read`).
