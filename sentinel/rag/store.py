import json
from dataclasses import dataclass
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values


@dataclass
class SearchResult:
    text: str
    source_file: str
    start_line: int
    end_line: int
    score: float
    metadata: dict


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sentinel_chunks (
    id          BIGSERIAL PRIMARY KEY,
    namespace   TEXT NOT NULL DEFAULT 'default',
    source_file TEXT NOT NULL,
    start_line  INT,
    end_line    INT,
    chunk_index INT,
    text        TEXT NOT NULL,
    metadata    JSONB,
    embedding   VECTOR(1536)
);

CREATE INDEX IF NOT EXISTS idx_chunks_namespace
    ON sentinel_chunks (namespace);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON sentinel_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
"""


class VectorStore:
    """pgvector-backed store for codebase chunk embeddings."""

    def __init__(self, dsn: str, namespace: str = "default"):
        self._dsn = dsn
        self._namespace = namespace
        self._conn = None

    def connect(self) -> None:
        self._conn = psycopg2.connect(self._dsn)
        with self._conn.cursor() as cur:
            cur.execute(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    def upsert_chunks(self, chunks_with_embeddings: list[tuple]) -> None:
        """Insert or update chunks. Each tuple: (chunk, embedding_list)."""
        rows = [
            (
                self._namespace,
                chunk.source_file,
                chunk.start_line,
                chunk.end_line,
                chunk.chunk_index,
                chunk.text,
                json.dumps(chunk.metadata or {}),
                embedding,
            )
            for chunk, embedding in chunks_with_embeddings
        ]
        with self._conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO sentinel_chunks
                    (namespace, source_file, start_line, end_line, chunk_index, text, metadata, embedding)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s::vector)",
            )
        self._conn.commit()

    def similarity_search(self, embedding: list[float], top_k: int = 5) -> list[SearchResult]:
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT text, source_file, start_line, end_line, metadata,
                       1 - (embedding <=> %s::vector) AS score
                FROM sentinel_chunks
                WHERE namespace = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vec_str, self._namespace, vec_str, top_k),
            )
            rows = cur.fetchall()

        return [
            SearchResult(
                text=row[0],
                source_file=row[1],
                start_line=row[2],
                end_line=row[3],
                metadata=row[4] or {},
                score=float(row[5]),
            )
            for row in rows
        ]

    def delete_namespace(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM sentinel_chunks WHERE namespace = %s", (self._namespace,))
        self._conn.commit()
