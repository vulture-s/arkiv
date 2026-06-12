from __future__ import annotations
"""
Vector DB module — ChromaDB + Ollama bge-m3 (multilingual, default)
"""
import json
import logging
import re
import threading
import requests
from typing import Any, Dict, List, Optional

import chromadb

from config import CHROMA_PATH, COLLECTION_NAME, EMBED_DIM, EMBED_MODEL, OLLAMA_URL
from llm import embed

OLLAMA_EMBED_URL = f"{OLLAMA_URL}/api/embeddings"
CHUNK_SIZE = 180      # words per chunk (Latin) or chars×4 (CJK)
CHUNK_OVERLAP = 20    # words / chars overlap
CHUNK_CHARS = 500     # character limit per chunk for CJK
EMBED_MAX_CHARS = 2000  # hard cap before sending to Ollama

_LOGGER = logging.getLogger(__name__)

_REBUILD_HINT = (
    "Run `python embed.py --rebuild` to rebuild the index for the current "
    "embedding model."
)


class EmbeddingDimensionMismatch(RuntimeError):
    """The active embedding model/dimension disagrees with the persisted ChromaDB
    collection. Subclasses RuntimeError so existing broad ``except Exception``
    handlers still catch it; always carries the rebuild hint."""


def _is_dimension_error(exc: Exception) -> bool:
    """ChromaDB signals a vector-dimension mismatch differently across versions
    (``InvalidDimensionException`` in older releases; ``InvalidArgumentError``
    with a ``"dimension"`` message in 1.5.x). Detect by message, with the typed
    exception as a fallback."""
    if "dimension" in str(exc).lower():
        return True
    inv = getattr(getattr(chromadb, "errors", None), "InvalidDimensionException", None)
    return inv is not None and isinstance(exc, inv)


def _reraise_dim_error(exc: Exception) -> None:
    """Re-raise a chromadb dimension error as ``EmbeddingDimensionMismatch`` (with
    the rebuild hint); pass anything else through unchanged. Always raises."""
    if isinstance(exc, EmbeddingDimensionMismatch):
        raise exc
    if _is_dimension_error(exc):
        raise EmbeddingDimensionMismatch(f"{exc} — {_REBUILD_HINT}") from exc
    raise exc


# ── Embedding ────────────────────────────────────────────────────────────────

# audit M27: reuse one TCP connection for embedding calls instead of a bare
# requests.post per chunk (connection setup dominated rebuild wall-clock time).
_EMBED_SESSION = requests.Session()
# None = unprobed; False = this Ollama has no batch /api/embed (pre-0.1.32) —
# don't re-probe a 404 on every record.
_BATCH_EMBED_SUPPORTED: Optional[bool] = None


def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed many texts in ONE Ollama ``/api/embed`` call (batch ``input``) over a
    shared Session (audit M27). Falls back to per-text ``embed()`` when the batch
    endpoint is unavailable (older Ollama) or returns an unexpected shape.

    Single-text calls delegate straight to ``embed()`` — no batch win there, and
    it keeps the module-level ``embed`` the single seam tests monkeypatch."""
    global _BATCH_EMBED_SUPPORTED
    if not texts:
        return []
    if len(texts) == 1 or _BATCH_EMBED_SUPPORTED is False:
        return [embed(t) for t in texts]

    truncated = [t[:EMBED_MAX_CHARS] for t in texts]
    try:
        resp = _EMBED_SESSION.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": truncated},
            timeout=max(30, 5 * len(truncated)),
        )
        if resp.status_code == 404:
            _BATCH_EMBED_SUPPORTED = False
        else:
            resp.raise_for_status()
            vectors = resp.json().get("embeddings")
            if (isinstance(vectors, list) and len(vectors) == len(truncated)
                    and all(isinstance(v, list) and v for v in vectors)):
                _BATCH_EMBED_SUPPORTED = True
                return vectors
            # Responded but with a shape we don't trust — stop using it.
            _BATCH_EMBED_SUPPORTED = False
            _LOGGER.warning("/api/embed returned unexpected shape; falling back to per-text embeds")
    except requests.RequestException:
        # Transient (timeout / connection refused): don't mark unsupported —
        # fall back for this call only; per-text embed surfaces the real error.
        pass
    return [embed(t) for t in texts]


# ── Chunking ─────────────────────────────────────────────────────────────────

def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。？！.?!])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _is_cjk(text: str) -> bool:
    cjk = sum(1 for c in text[:100] if "\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff")
    return cjk > 20


def chunk_text(text: str) -> List[str]:
    """Split text into overlapping chunks. Uses char-count for CJK, word-count for Latin."""
    sentences = _split_sentences(text)
    cjk = _is_cjk(text)
    size = CHUNK_CHARS if cjk else CHUNK_SIZE
    overlap = CHUNK_CHARS // 4 if cjk else CHUNK_OVERLAP
    join_sep = "" if cjk else " "

    def measure(s): return len(s) if cjk else len(s.split())

    chunks, current, count = [], [], 0
    for sent in sentences:
        m = measure(sent)
        if count + m > size and current:
            chunk_str = join_sep.join(current)
            chunks.append(chunk_str)
            # keep tail as overlap
            tail = chunk_str[-overlap:] if cjk else join_sep.join(chunk_str.split()[-overlap:])
            current = [tail, sent] if tail else [sent]
            count = measure(tail) + m
        else:
            current.append(sent)
            count += m

    if current:
        chunks.append(join_sep.join(current))

    return chunks if chunks else [text[:CHUNK_CHARS]]


# ── ChromaDB client ───────────────────────────────────────────────────────────

def _assert_collection_compatible(col) -> None:
    """Fail loud if the persisted collection was built with a different embedding
    model than the active config. Legacy collections with no stamp can't be
    verified here — they fall through to the defensive query/upsert catch."""
    meta = getattr(col, "metadata", None) or {}
    stamped = meta.get("embed_model")
    if stamped is not None:
        if stamped != EMBED_MODEL:
            raise EmbeddingDimensionMismatch(
                f"Vector index was built with embed_model={stamped!r} "
                f"(dim {meta.get('embed_dim')}), but the active config is "
                f"{EMBED_MODEL!r} (dim {EMBED_DIM}). {_REBUILD_HINT}"
            )
        return

    # Legacy / unstamped collection (built before the stamp existed). We can't
    # read the model name, but we CAN check the stored vector dimension — without
    # this, an incremental `embed.py` on a pre-bge-m3 index sees every id already
    # indexed, reports "up to date", and leaves semantic search silently broken
    # on the 768-vs-1024 mismatch (Codex P2). Best-effort: a collection we can't
    # introspect (empty, or the test stub) falls through to the query/upsert catch.
    try:
        sample = col.get(include=["embeddings"], limit=1)
        embeddings = sample.get("embeddings") if isinstance(sample, dict) else None
    except Exception:
        return
    if embeddings is not None and len(embeddings) > 0 and embeddings[0] is not None:
        stored_dim = len(embeddings[0])
        if stored_dim != EMBED_DIM:
            raise EmbeddingDimensionMismatch(
                f"Legacy vector index has dimension {stored_dim}, but the active "
                f"model {EMBED_MODEL!r} produces dimension {EMBED_DIM}. {_REBUILD_HINT}"
            )


# audit M11: chromadb's PersistentClient/get_or_create_collection is a
# check-then-create on shared on-disk state — concurrent callers (FastAPI
# threadpool workers, federation, embed rebuild) can race it and leak System
# instances. Serialize client + collection acquisition behind one lock.
_CHROMA_LOCK = threading.Lock()


def get_collection(reset: bool = False):
    with _CHROMA_LOCK:  # audit M11
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        if reset:
            try:
                client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass
        col = client.get_or_create_collection(
            COLLECTION_NAME,
            # Stamp the embedding model + dim so a later model change is detected and
            # fails loud instead of throwing a raw chromadb dimension error. On an
            # EXISTING collection chromadb ignores these extra metadata keys (verified
            # on 1.5.x), so the stamp only lands when the collection is created/reset.
            metadata={"hnsw:space": "cosine", "embed_model": EMBED_MODEL, "embed_dim": EMBED_DIM},
        )
    _assert_collection_compatible(col)
    return col


# ── Index operations ──────────────────────────────────────────────────────────

def build_doc_text(record: dict) -> str:
    """Compose a searchable text from a media record (for frame-tags only records).

    audit Round-1 critical fix（2026-05-05）：原本只讀 frame_tags 裡的 `keywords`
    key，但 production vision pipeline（vision.py）寫的是 `description` + `tags`，
    根本沒有 `keywords` 欄。實測恬馨庫 427 row × 5 frame 的 vision 描述 + tag
    全部 silently 沒進 vector index，semantic search 對視覺內容召回極差。

    現在順序：description（每 frame 完整敘述，最有 semantic value）+ tags list
    + 保留 keywords legacy fallback（早期 schema 的舊資料 / 向後兼容）。
    """
    parts = [f"[{record['filename']}]"]
    if record.get("transcript"):
        parts.append(record["transcript"])
    if record.get("frame_tags"):
        try:
            frames = json.loads(record["frame_tags"])
        except (json.JSONDecodeError, TypeError):
            frames = []
        if isinstance(frames, list):
            chunks = []
            for f in frames:
                if not isinstance(f, dict):
                    continue
                desc = f.get("description")
                if isinstance(desc, str) and desc.strip():
                    chunks.append(desc.strip())
                tags_field = f.get("tags")
                if isinstance(tags_field, list):
                    chunks.extend(t.strip() for t in tags_field
                                  if isinstance(t, str) and t.strip())
                # Legacy schema fallback: 早期版本把 vision 結果壓成 "keywords" 字串
                kw = f.get("keywords")
                if isinstance(kw, str) and kw.strip():
                    chunks.append(kw.strip())
            if chunks:
                parts.append(" ".join(chunks))
    return " ".join(parts)


def delete_media(col, media_id) -> None:
    """Remove all chunks for a media_id. Used before re-embedding (refresh) and
    by reconcile to drop rows deleted from SQLite (H5)."""
    try:
        col.delete(where={"media_id": str(media_id)})
    except Exception:
        pass


def upsert_record(col, record: dict) -> int:
    """Upsert one SQLite media record into ChromaDB. Returns chunk count."""
    media_id = str(record["id"])
    # Delete existing chunks first: a re-embed of a shrunk transcript (5→2
    # chunks) otherwise leaves orphan {id}_t2.. behind, and a refresh re-embed
    # would stack stale vectors (H5).
    delete_media(col, media_id)
    transcript = record.get("transcript") or ""
    frame_doc = build_doc_text(record)

    meta_base = {
        "media_id": media_id,
        "path": record["path"],
        "filename": record["filename"],
        "duration_s": record.get("duration_s") or 0,
        "lang": record.get("lang") or "",
        "has_audio": record.get("has_audio") or 0,
        "project_name": record.get("project_name") or "",
    }

    ids, embeddings, documents, metadatas = [], [], [], []

    if transcript:
        chunks = chunk_text(transcript)
        vectors = embed_batch(chunks)  # audit M27: one HTTP call for all chunks
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            doc_id = f"{media_id}_t{i}"
            ids.append(doc_id)
            documents.append(chunk)
            embeddings.append(vec)
            metadatas.append({**meta_base, "chunk_type": "transcript", "chunk_idx": i})
    else:
        # No transcript — embed frame tags / filename only
        doc_id = f"{media_id}_f0"
        ids.append(doc_id)
        documents.append(frame_doc)
        embeddings.append(embed(frame_doc))
        metadatas.append({**meta_base, "chunk_type": "frame_tags", "chunk_idx": 0})

    try:
        col.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
    except Exception as exc:  # defensive: a dim mismatch here means a stale index
        _reraise_dim_error(exc)
    return len(ids)


def _scope_where(project_scope: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    if not project_scope:
        return None
    return {"project_name": {"$in": project_scope}}


def _query_collection(col, query_embeddings, n_results, project_scope=None):
    kwargs = {
        "query_embeddings": query_embeddings,
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    where = _scope_where(project_scope)
    if where:
        kwargs["where"] = where
    try:
        return col.query(**kwargs)
    except Exception as exc:  # defensive: surface a dim mismatch as a clear error
        _reraise_dim_error(exc)


def _process_query_results(raw: Dict[str, Any], n_results: int, skip_media_ids=None) -> List[Dict[str, Any]]:
    skip = skip_media_ids or set()
    seen = set()
    results = []
    documents = raw.get("documents") or [[]]
    metadatas = raw.get("metadatas") or [[]]
    distances = raw.get("distances") or [[]]

    for doc, meta, dist in zip(documents[0], metadatas[0], distances[0]):
        media_id = meta["media_id"]
        if media_id in skip or media_id in seen:
            continue
        seen.add(media_id)
        results.append({
            "media_id": media_id,
            "filename": meta["filename"],
            "path": meta["path"],
            "duration_s": meta["duration_s"],
            "lang": meta["lang"],
            "excerpt": doc[:300],
            "score": round(1 - dist, 4),  # cosine similarity
            "chunk_type": meta["chunk_type"],
        })
        if len(results) >= n_results:
            break

    return results


# ── Search ────────────────────────────────────────────────────────────────────

def search(
    query: str,
    n_results: int = 10,
    project_scope: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Semantic search. Returns deduplicated results (one per media file),
    sorted by best cosine similarity.
    """
    col = get_collection()
    q_embed = embed(query)
    raw = _query_collection(
        col,
        [q_embed],
        min(n_results * 3, col.count() or 1),
        project_scope=project_scope,
    )
    return _process_query_results(raw, n_results)


def find_similar(
    media_id: int,
    n_results: int = 10,
    project_scope: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Find media near the first indexed chunk for media_id.
    """
    col = get_collection()
    media_id_str = str(media_id)
    ref = col.get(
        where={"media_id": media_id_str},
        include=["embeddings"],
        limit=1,
    )
    embeddings = ref.get("embeddings")
    if embeddings is None or len(embeddings) == 0:
        return []

    raw = _query_collection(
        col,
        [embeddings[0]],
        min((n_results + 1) * 3, col.count() or 1),
        project_scope=project_scope,
    )
    return _process_query_results(
        raw,
        n_results,
        skip_media_ids=set([media_id_str]),
    )
