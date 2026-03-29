"""
Vector DB module — ChromaDB + Ollama nomic-embed-text
"""
import json
import re
import requests
import chromadb

from config import CHROMA_PATH, COLLECTION_NAME, EMBED_MODEL, OLLAMA_URL

OLLAMA_EMBED_URL = f"{OLLAMA_URL}/api/embeddings"
CHUNK_SIZE = 180      # words per chunk (Latin) or chars×4 (CJK)
CHUNK_OVERLAP = 20    # words / chars overlap
CHUNK_CHARS = 500     # character limit per chunk for CJK
EMBED_MAX_CHARS = 2000  # hard cap before sending to Ollama


# ── Embedding ────────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    """Embed a single text via Ollama."""
    text = text[:EMBED_MAX_CHARS]
    r = requests.post(OLLAMA_EMBED_URL, json={"model": EMBED_MODEL, "prompt": text}, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [embed(t) for t in texts]


# ── Chunking ─────────────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。？！.?!])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _is_cjk(text: str) -> bool:
    cjk = sum(1 for c in text[:100] if "\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff")
    return cjk > 20


def chunk_text(text: str) -> list[str]:
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

def get_collection(reset: bool = False):
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    return client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ── Index operations ──────────────────────────────────────────────────────────

def build_doc_text(record: dict) -> str:
    """Compose a searchable text from a media record (for frame-tags only records)."""
    parts = [f"[{record['filename']}]"]
    if record.get("transcript"):
        parts.append(record["transcript"])
    if record.get("frame_tags"):
        try:
            tags = json.loads(record["frame_tags"])
            kw = " ".join(
                t.get("keywords", "") for t in tags if isinstance(t, dict)
            )
            if kw:
                parts.append(kw)
        except (json.JSONDecodeError, TypeError):
            pass
    return " ".join(parts)


def upsert_record(col, record: dict) -> int:
    """Upsert one SQLite media record into ChromaDB. Returns chunk count."""
    media_id = str(record["id"])
    transcript = record.get("transcript") or ""
    frame_doc = build_doc_text(record)

    meta_base = {
        "media_id": media_id,
        "path": record["path"],
        "filename": record["filename"],
        "duration_s": record.get("duration_s") or 0,
        "lang": record.get("lang") or "",
        "has_audio": record.get("has_audio") or 0,
    }

    ids, embeddings, documents, metadatas = [], [], [], []

    if transcript:
        chunks = chunk_text(transcript)
        for i, chunk in enumerate(chunks):
            doc_id = f"{media_id}_t{i}"
            ids.append(doc_id)
            documents.append(chunk)
            embeddings.append(embed(chunk))
            metadatas.append({**meta_base, "chunk_type": "transcript", "chunk_idx": i})
    else:
        # No transcript — embed frame tags / filename only
        doc_id = f"{media_id}_f0"
        ids.append(doc_id)
        documents.append(frame_doc)
        embeddings.append(embed(frame_doc))
        metadatas.append({**meta_base, "chunk_type": "frame_tags", "chunk_idx": 0})

    col.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
    return len(ids)


# ── Search ────────────────────────────────────────────────────────────────────

def search(query: str, n_results: int = 10) -> list[dict]:
    """
    Semantic search. Returns deduplicated results (one per media file),
    sorted by best cosine similarity.
    """
    col = get_collection()
    q_embed = embed(query)
    raw = col.query(
        query_embeddings=[q_embed],
        n_results=min(n_results * 3, col.count() or 1),
        include=["documents", "metadatas", "distances"],
    )

    seen, results = set(), []
    for doc, meta, dist in zip(
        raw["documents"][0], raw["metadatas"][0], raw["distances"][0]
    ):
        media_id = meta["media_id"]
        if media_id in seen:
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
