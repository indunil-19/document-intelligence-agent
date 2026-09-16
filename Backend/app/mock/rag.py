"""Mock RAG client.

Deliberately behind a narrow async interface so the real vector store can be
dropped in later without touching the agents or tools. Scoring is naive keyword
overlap; that is all a mock needs.
"""
import asyncio
import logging
import re
from collections import Counter

from app.mock.documents import DOCUMENTS

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "what",
    "how", "do", "does", "we", "our", "i", "me", "my", "it", "be", "with", "about",
    "which", "who", "when", "where", "this", "that", "from", "by", "as", "at", "can",
}

DOCUMENT_TYPES = sorted({d["document_type"] for d in DOCUMENTS})


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


def _score(query_tokens: list[str], doc: dict) -> float:
    haystack = Counter(
        _tokenize(f"{doc['title']} {doc['document_type']} {doc['content']}")
    )
    hits = sum(haystack[t] for t in query_tokens)
    if not hits:
        return 0.0
    # Normalise so long documents do not dominate.
    return round(hits / (len(query_tokens) + 2), 4)


def _public(doc: dict, *, include_content: bool = True) -> dict:
    out = {
        "document_id": doc["document_id"],
        "document_type": doc["document_type"],
        "title": doc["title"],
        "metadata": doc["metadata"],
    }
    if include_content:
        out["content"] = doc["content"]
    return out


class MockRagStore:
    """Async facade over the seed corpus."""

    def __init__(self, documents: list[dict] | None = None, latency: float = 0.02):
        self._documents = documents if documents is not None else DOCUMENTS
        self._latency = latency

    async def _io(self) -> None:
        """Stand in for network latency so concurrency is actually exercised."""
        await asyncio.sleep(self._latency)

    async def search(
        self, query: str, *, document_type: str | None = None, limit: int = 5
    ) -> list[dict]:
        await self._io()
        tokens = _tokenize(query)
        candidates = [
            d for d in self._documents
            if document_type is None or d["document_type"] == document_type
        ]
        scored = [(d, _score(tokens, d)) for d in candidates]
        scored = [(d, s) for d, s in scored if s > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        if not scored:  # fall back to type listing rather than returning nothing
            scored = [(d, 0.0) for d in candidates[:limit]]
        return [{**_public(d), "score": s} for d, s in scored[:limit]]

    async def metadata_for_type(self, document_type: str) -> dict:
        """Metadata field names plus their observed values for one document type."""
        await self._io()
        docs = [d for d in self._documents if d["document_type"] == document_type]
        if not docs:
            return {
                "document_type": document_type,
                "known_document_types": DOCUMENT_TYPES,
                "document_count": 0,
                "fields": {},
            }
        fields: dict[str, set[str]] = {}
        for d in docs:
            for key, value in d["metadata"].items():
                fields.setdefault(key, set()).add(str(value))
        return {
            "document_type": document_type,
            "document_count": len(docs),
            "fields": {k: sorted(v) for k, v in sorted(fields.items())},
        }

    async def filter_by_metadata(self, filters: dict[str, str]) -> list[dict]:
        """Match on document_type plus any metadata key. Comparison is case-insensitive."""
        await self._io()
        matched = []
        for d in self._documents:
            merged = {"document_type": d["document_type"], **d["metadata"]}
            if all(
                str(merged.get(k, "")).lower() == str(v).lower()
                for k, v in filters.items()
            ):
                matched.append(_public(d))
        return matched

    async def get_many(self, document_ids: list[str]) -> list[dict]:
        await self._io()
        wanted = set(document_ids)
        return [_public(d) for d in self._documents if d["document_id"] in wanted]


_store = MockRagStore()


def get_rag_store() -> MockRagStore:
    return _store
