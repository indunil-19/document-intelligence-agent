"""Sparse (BM25 / keyword) search.

A plain in-memory rank_bm25 index over whatever document set it's built with. Pure
Python, synchronous, no external dependency - the async wrapper in store.py exists
only so its call sites look the same as the async dense path.
"""
import re

from rank_bm25 import BM25Okapi

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "what",
    "how", "do", "does", "we", "our", "i", "me", "my", "it", "be", "with", "about",
    "which", "who", "when", "where", "this", "that", "from", "by", "as", "at", "can",
}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


class BM25Index:
    """BM25 over a fixed list of (key, text) pairs, built once at construction."""

    def __init__(self, entries: list[tuple[str, str]]):
        self._keys = [key for key, _ in entries]
        corpus = [tokenize(text) for _, text in entries]
        # BM25Okapi requires a non-empty corpus; an empty document set is a valid
        # (if useless) state, e.g. in tests - guard rather than let it raise.
        self._index = BM25Okapi(corpus) if corpus else None

    def scores(self, query: str) -> dict[str, float]:
        """BM25 score per key for this query. Unscored keys simply aren't unusual -
        a zero-overlap query with the whole corpus is a normal outcome, not scored."""
        if self._index is None:
            return {}
        raw = self._index.get_scores(tokenize(query))
        return dict(zip(self._keys, (float(s) for s in raw)))

    def top_k(self, query: str, k: int) -> list[tuple[str, float]]:
        scored = [(key, score) for key, score in self.scores(query).items() if score > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]
