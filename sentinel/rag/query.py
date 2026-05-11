from dataclasses import dataclass

from sentinel.providers.base.llm import BaseLLMProvider
from sentinel.rag.store import SearchResult, VectorStore


@dataclass
class RAGResponse:
    answer: str
    sources: list[SearchResult]
    query: str


class RAGQuery:
    """Query the knowledge base with a natural language question."""

    def __init__(self, llm: BaseLLMProvider, store: VectorStore):
        self._llm = llm
        self._store = store

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        embedding = self._llm.embed(query)
        return self._store.similarity_search(embedding, top_k=top_k)

    def answer(self, query: str, top_k: int = 5) -> RAGResponse:
        results = self.search(query, top_k=top_k)
        context = "\n\n---\n\n".join(
            f"File: {r.source_file} (lines {r.start_line}–{r.end_line})\n{r.text}"
            for r in results
        )
        prompt = (
            f"You are an expert SRE analyzing a codebase. "
            f"Using ONLY the following code context, answer the question concisely.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            f"Answer:"
        )
        response = self._llm.complete(prompt, max_tokens=512)
        return RAGResponse(answer=response.text.strip(), sources=results, query=query)

    def find_similar_incidents(self, incident_description: str, top_k: int = 5) -> list[SearchResult]:
        """Specialized search for past incident runbooks and post-mortems."""
        enriched = f"incident runbook postmortem resolution: {incident_description}"
        return self.search(enriched, top_k=top_k)
