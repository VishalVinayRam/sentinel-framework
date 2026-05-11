import logging
from typing import Iterator

from sentinel.providers.base.llm import BaseLLMProvider
from sentinel.rag.indexer import Chunk, CodebaseIndexer
from sentinel.rag.store import VectorStore

logger = logging.getLogger(__name__)

BATCH_SIZE = 32


class Embedder:
    """Orchestrates indexing: chunks files, embeds them, stores in pgvector."""

    def __init__(self, llm: BaseLLMProvider, store: VectorStore):
        self._llm = llm
        self._store = store

    def index_codebase(self, root: str, chunk_size: int = 512, overlap: int = 64, exclude: list = None) -> int:
        indexer = CodebaseIndexer(root=root, chunk_size=chunk_size, overlap=overlap, exclude=exclude)
        total = 0
        batch = []

        for chunk in indexer.iter_chunks():
            batch.append(chunk)
            if len(batch) >= BATCH_SIZE:
                total += self._flush(batch)
                batch = []

        if batch:
            total += self._flush(batch)

        logger.info("Indexed %d chunks from %s", total, root)
        return total

    def _flush(self, chunks: list[Chunk]) -> int:
        texts = [c.text for c in chunks]
        try:
            embeddings = self._llm.embed_batch(texts)
        except NotImplementedError:
            embeddings = [self._llm.embed(t) for t in texts]
        except Exception as e:
            logger.warning("Embedding batch failed: %s — skipping", e)
            return 0

        pairs = list(zip(chunks, embeddings))
        self._store.upsert_chunks(pairs)
        return len(pairs)
