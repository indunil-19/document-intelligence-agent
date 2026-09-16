"""Hybrid RAG store: dense (embeddings) + sparse (BM25) + a combined ranking.

search() is the only method that does any ranking; metadata_for_type,
filter_by_metadata and get_many are exact-match lookups carried over unchanged from
the original mock store - hybrid ranking has nothing to add there.

Dense search degrades gracefully: if the embedding model fails to load (no network
on first run to fetch weights, fastembed missing, etc.) search falls back to
sparse-only rather than failing the request - the same "answer with what's
available" posture as the rest of this app (MCP unavailable, LLM model missing).
"""
import asyncio
import logging

from app.config import get_settings
from app.mock.documents import DOCUMENTS
from app.rag.embeddings import Embedder, EmbeddingUnavailableError, cosine_similarity
from app.rag.sparse import BM25Index

logger = logging.getLogger(__name__)

DOCUMENT_TYPES = sorted({d["document_type"] for d in DOCUMENTS})


def _searchable_text(doc: dict) -> str:
    return f"{doc['title']} {doc['document_type']} {doc['content']}"


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


def _min_max_normalize(scores: dict[str, float]) -> dict[str, float]:
    """Scale into [0, 1] so dense (cosine, ~[-1,1]) and sparse (unbounded BM25) can
    be combined meaningfully. A single distinct value normalizes to 1.0 for every
    key - there's no useful signal in a zero-spread set, so don't zero it out either."""
    if not scores:
        return {}
    values = scores.values()
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class HybridRagStore:
    """Dense + sparse + fused ranking over a fixed document set."""

    def __init__(
        self,
        documents: list[dict] | None = None,
        *,
        embedder: Embedder | None = None,
        candidate_k: int | None = None,
        alpha: float | None = None,
        latency: float = 0.02,
    ):
        settings = get_settings()
        self._documents = documents if documents is not None else DOCUMENTS
        self._by_id = {d["document_id"]: d for d in self._documents}
        self._latency = latency
        self._candidate_k = candidate_k if candidate_k is not None else settings.hybrid_candidate_k
        self._alpha = alpha if alpha is not None else settings.hybrid_alpha

        self._sparse = BM25Index(
            [(d["document_id"], _searchable_text(d)) for d in self._documents]
        )

        self._embedder = embedder or Embedder(settings.embedding_model)
        self._dense_vectors: dict[str, list[float]] | None = None
        self._dense_available = True
        self._embed_lock = asyncio.Lock()

    async def _io(self) -> None:
        """Stand in for network latency so concurrency is actually exercised."""
        await asyncio.sleep(self._latency)

    @property
    def dense_available(self) -> bool:
        """Whether dense search is usable - False before warm-up, or permanently
        after the embedding model failed to load."""
        return self._dense_available and self._dense_vectors is not None

    # --- dense embedding warm-up -------------------------------------------------

    async def warm_up(self) -> bool:
        """Embed the whole corpus once and cache the vectors. Safe to call more than
        once (a no-op after the first success) and safe to call concurrently (the
        lock prevents duplicate embedding work). Returns whether dense search ended
        up available - callers that just want it warmed at startup can ignore this.
        """
        if self._dense_vectors is not None or not self._dense_available:
            return self._dense_available
        async with self._embed_lock:
            if self._dense_vectors is not None or not self._dense_available:
                return self._dense_available
            try:
                ids = [d["document_id"] for d in self._documents]
                texts = [_searchable_text(d) for d in self._documents]
                vectors = await self._embedder.embed_documents(texts)
                self._dense_vectors = dict(zip(ids, vectors))
                logger.info(
                    "dense index warmed",
                    extra={"event": "rag.dense_warmed", "document_count": len(ids)},
                )
            except EmbeddingUnavailableError as exc:
                self._dense_available = False
                logger.warning(
                    "dense search unavailable, falling back to sparse-only",
                    extra={"event": "rag.dense_unavailable", "reason": str(exc)},
                )
        return self._dense_available

    async def _dense_scores(self, query: str, candidate_ids: list[str]) -> dict[str, float]:
        if not await self.warm_up() or not candidate_ids:
            return {}
        try:
            query_vector = await self._embedder.embed_query(query)
        except EmbeddingUnavailableError:
            self._dense_available = False
            return {}
        return {
            doc_id: cosine_similarity(query_vector, self._dense_vectors[doc_id])
            for doc_id in candidate_ids
            if doc_id in self._dense_vectors
        }

    # --- search -------------------------------------------------------------------

    async def search(
        self, query: str, *, document_type: str | None = None, limit: int = 5
    ) -> list[dict]:
        await self._io()

        candidates = [
            d for d in self._documents
            if document_type is None or d["document_type"] == document_type
        ]
        candidate_ids = [d["document_id"] for d in candidates]
        if not candidates:
            return []

        # BM25 IDF is computed once over the whole corpus (as it should be - document
        # frequency stats need the full corpus to mean anything), but the top-K cut
        # must happen AFTER restricting to this type's candidates - cutting first
        # and filtering after could starve a type-filtered search of a
        # good-but-not-globally-top-K match.
        candidate_id_set = set(candidate_ids)
        sparse_all = self._sparse.scores(query)
        sparse_in_type = {
            k: v for k, v in sparse_all.items() if k in candidate_id_set and v > 0
        }
        sparse_top = dict(
            sorted(sparse_in_type.items(), key=lambda kv: kv[1], reverse=True)[: self._candidate_k]
        )

        dense_scores = await self._dense_scores(query, candidate_ids)
        dense_top = dict(
            sorted(dense_scores.items(), key=lambda kv: kv[1], reverse=True)[: self._candidate_k]
        )

        # Union of both methods' candidates - a document that only one method rated
        # highly should still be eligible, not excluded for missing the other's cut.
        union_ids = set(sparse_top) | set(dense_top)
        if not union_ids:
            # Neither method found anything: fall back to a plain type listing
            # rather than returning nothing for a query that used unfamiliar wording.
            return [
                {**_public(d), "score": 0.0, "dense_score": 0.0, "sparse_score": 0.0}
                for d in candidates[:limit]
            ]

        sparse_norm = _min_max_normalize({k: sparse_top.get(k, 0.0) for k in union_ids})
        dense_norm = _min_max_normalize({k: dense_scores.get(k, 0.0) for k in union_ids})

        alpha = self._alpha if dense_top else 0.0  # no dense signal -> pure sparse
        ranked = sorted(
            union_ids,
            key=lambda k: alpha * dense_norm.get(k, 0.0) + (1 - alpha) * sparse_norm.get(k, 0.0),
            reverse=True,
        )[:limit]

        results = []
        for doc_id in ranked:
            hybrid = alpha * dense_norm.get(doc_id, 0.0) + (1 - alpha) * sparse_norm.get(doc_id, 0.0)
            results.append({
                **_public(self._by_id[doc_id]),
                "score": round(hybrid, 4),
                "dense_score": round(dense_scores.get(doc_id, 0.0), 4),
                "sparse_score": round(sparse_top.get(doc_id, 0.0), 4),
            })
        return results

    # --- exact-match lookups, unchanged from the mock store -----------------------

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


_store = HybridRagStore()


def get_rag_store() -> HybridRagStore:
    return _store
