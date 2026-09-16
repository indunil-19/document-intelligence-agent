"""Dense embeddings for hybrid search.

Local ONNX model via fastembed - no API key, no network call at query time. The
KodeKloud gateway this project otherwise uses for chat has no embeddings endpoint
for our key (confirmed: every model returns 403/400 on /embeddings), so dense
search is self-contained rather than routed through it.

fastembed's TextEmbedding is a synchronous, CPU-bound object (ONNX Runtime
inference); every call here goes through asyncio.to_thread so it never blocks the
event loop. Model weights download once on first use and are cached to disk by
fastembed itself - no code here needs to know where.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(Exception):
    """Raised when the embedding model could not be loaded or run."""


class Embedder:
    """Thin async wrapper around fastembed.TextEmbedding.

    Swappable in tests: anything with the same embed_documents/embed_query surface
    can stand in, so tests don't need to load a real ONNX model.
    """

    def __init__(self, model_name: str):
        self._model_name = model_name
        self._model = None
        self._load_lock = asyncio.Lock()

    async def _get_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:  # re-check: another waiter may have loaded it
                try:
                    self._model = await asyncio.to_thread(self._load_model)
                except Exception as exc:  # noqa: BLE001 - surfaced as a typed error
                    raise EmbeddingUnavailableError(
                        f"could not load embedding model '{self._model_name}': {exc}"
                    ) from exc
        return self._model

    def _load_model(self):
        from fastembed import TextEmbedding

        logger.info(
            "loading embedding model",
            extra={"event": "embeddings.loading", "model": self._model_name},
        )
        model = TextEmbedding(model_name=self._model_name)
        logger.info(
            "embedding model ready",
            extra={"event": "embeddings.ready", "model": self._model_name},
        )
        return model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._get_model()
        vectors = await asyncio.to_thread(lambda: list(model.embed(texts)))
        return [v.tolist() for v in vectors]

    async def embed_query(self, text: str) -> list[float]:
        model = await self._get_model()
        vectors = await asyncio.to_thread(lambda: list(model.query_embed(text)))
        return vectors[0].tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain-Python cosine similarity - the corpus is small enough that numpy would
    be pure overhead for this project's scale."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
