from __future__ import annotations
"""
pgvector backend for vectordb.py — a drop-in ``PgCollection`` that mimics the
subset of the ChromaDB collection API that :mod:`vectordb` actually uses
(``metadata`` / ``count`` / ``get`` / ``upsert`` / ``delete`` / ``query``), so
switching ``ARKIV_VECTOR_BACKEND=pg`` reroutes every caller to a shared
Postgres + pgvector/VectorChord store **without touching a single call site**.

Why a shim and not a rewrite: all 7 callers go through ``vectordb.*`` functions
that pass a ``col`` object around. Matching Chroma's method signatures + return
shapes (nested ``[[...]]`` query results, cosine *distance* in ``distances``)
keeps ``upsert_record`` / ``search`` / ``find_similar`` / ``_process_query_results``
byte-for-byte unchanged. Chroma stays the default; this file is only imported
when the pg backend is selected, so ``psycopg`` is an optional dependency.

Storage model: one wide table per embedding dimension (``emb_1024`` for bge-m3),
rows tagged by ``collection`` (arkiv → ``media_assets``) so multiple apps can
share the same store without colliding. ``payload`` (jsonb) holds the same
metadata dict Chroma stored per chunk; ``doc_id`` is Chroma's id (``{mid}_t{i}``);
``(collection, doc_id)`` is unique so upsert is idempotent.
"""
import json
from typing import Any, Dict, List, Optional


def _vec_literal(vec) -> str:
    """pgvector text input format: ``[0.1,0.2,...]``. Full float precision so a
    round-trip through the DB reproduces the source vector."""
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def _parse_vec(text: str) -> List[float]:
    """``embedding::text`` comes back as a JSON-shaped array string."""
    return [float(x) for x in json.loads(text)]


class PgCollection:
    """Chroma-collection look-alike backed by Postgres + pgvector.

    Only the methods/attrs :mod:`vectordb` touches are implemented. Distances
    use the ``<=>`` cosine-distance operator, matching Chroma's ``hnsw:space=cosine``
    so downstream ``score = 1 - dist`` is identical.
    """

    def __init__(self, dsn: str, dim: int, model: str, collection: str,
                 table: Optional[str] = None):
        import psycopg  # optional dep — only needed for the pg backend
        self._psycopg = psycopg
        self.dsn = dsn
        self.dim = int(dim)
        self.collection = collection
        # table name derived from dim (int → safe to interpolate); shared store
        # keeps one table per embedding space (emb_1024 for bge-m3).
        self.table = table or f"emb_{self.dim}"
        # Stamp mirrors the Chroma metadata so vectordb._assert_collection_compatible
        # takes the fast stamped-match path (no legacy dimension probe).
        self.metadata = {"hnsw:space": "cosine", "embed_model": model, "embed_dim": self.dim}
        self._ensure_schema()

    # ── connection / schema ────────────────────────────────────────────────
    def _connect(self):
        return self._psycopg.connect(self.dsn)

    def _ensure_schema(self) -> None:
        """Idempotent DDL so a fresh (vchord-enabled) pg works out of the box.
        No-op against the already-provisioned NAS store. The unique index is
        what makes ``ON CONFLICT (collection, doc_id)`` a real upsert."""
        ddl = f"""
        CREATE TABLE IF NOT EXISTS {self.table} (
            id         bigserial PRIMARY KEY,
            collection text NOT NULL,
            namespace  text,
            doc_id     text,
            content    text,
            payload    jsonb,
            embedding  vector({self.dim}),
            created_at timestamptz DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_{self.table}_coll_docid
            ON {self.table} (collection, doc_id);
        CREATE INDEX IF NOT EXISTS ix_{self.table}_coll
            ON {self.table} (collection);
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(ddl)
            conn.commit()

    def reset(self) -> None:
        """Drop every row for this collection (Chroma ``reset=True`` analogue).
        Scoped to ``collection`` — never touches other apps' rows in the table."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.table} WHERE collection = %s", (self.collection,))
            conn.commit()

    # ── Chroma-collection API subset ───────────────────────────────────────
    def count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {self.table} WHERE collection = %s",
                        (self.collection,))
            return int(cur.fetchone()[0])

    def upsert(self, ids, embeddings, documents, metadatas) -> None:
        rows = []
        for i in range(len(ids)):
            meta = metadatas[i] or {}
            rows.append((
                self.collection,
                (meta.get("project_name") or None),
                ids[i],
                documents[i],
                json.dumps(meta, ensure_ascii=False),
                _vec_literal(embeddings[i]),
            ))
        sql = f"""
            INSERT INTO {self.table}
                (collection, namespace, doc_id, content, payload, embedding)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (collection, doc_id) DO UPDATE SET
                namespace = EXCLUDED.namespace,
                content   = EXCLUDED.content,
                payload   = EXCLUDED.payload,
                embedding = EXCLUDED.embedding
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(sql, rows)
            conn.commit()

    def delete(self, where: Optional[Dict[str, Any]] = None, **_) -> None:
        where = where or {}
        mid = where.get("media_id")
        if mid is None:  # vectordb only ever deletes by media_id
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {self.table} "
                f"WHERE collection = %s AND payload->>'media_id' = %s",
                (self.collection, str(mid)),
            )
            conn.commit()

    def get(self, where: Optional[Dict[str, Any]] = None,
            include: Optional[List[str]] = None,
            limit: Optional[int] = None, **_) -> Dict[str, Any]:
        """Returns ``{"ids": [...], "embeddings": [[...], ...]}`` — the shape
        ``find_similar`` / the legacy compat probe read. Ordered by ``doc_id`` so
        ``limit=1`` deterministically picks a media's first chunk."""
        where = where or {}
        clauses = ["collection = %s"]
        params: List[Any] = [self.collection]
        if "media_id" in where:
            clauses.append("payload->>'media_id' = %s")
            params.append(str(where["media_id"]))
        sql = (f"SELECT doc_id, embedding::text FROM {self.table} "
               f"WHERE {' AND '.join(clauses)} ORDER BY doc_id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            fetched = cur.fetchall()
        return {
            "ids": [r[0] for r in fetched],
            "embeddings": [_parse_vec(r[1]) for r in fetched],
        }

    def query(self, query_embeddings, n_results: int = 10,
              include: Optional[List[str]] = None,
              where: Optional[Dict[str, Any]] = None, **_) -> Dict[str, Any]:
        """kNN by cosine distance. ``query_embeddings`` is a list of vectors
        (vectordb always passes exactly one); results are wrapped one level deep
        (``[[...]]``) to match Chroma's per-query nesting."""
        qlit = _vec_literal(query_embeddings[0])
        clauses = ["collection = %s"]
        where_params: List[Any] = [self.collection]
        if where and "project_name" in where:
            pn = where["project_name"]
            scope = pn.get("$in") if isinstance(pn, dict) else [pn]
            clauses.append("payload->>'project_name' = ANY(%s)")
            where_params.append(list(scope))
        sql = (
            f"SELECT content, payload, embedding <=> %s::vector AS dist "
            f"FROM {self.table} WHERE {' AND '.join(clauses)} "
            f"ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        params = [qlit] + where_params + [qlit, int(n_results)]
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return {
            "documents": [[r[0] for r in rows]],
            "metadatas": [[r[1] for r in rows]],
            "distances": [[float(r[2]) for r in rows]],
        }
