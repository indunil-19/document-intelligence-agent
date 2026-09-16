"""In-memory session store.

Holds the conversation history and the documents cached by `filter_by_metadata`,
so the analytics tool can work on a filtered set without re-fetching it. Swap for
Redis when this needs to outlive the process.
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10
SESSION_TTL_SECONDS = 60 * 60


@dataclass
class Session:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)
    # filter signature -> cached documents
    document_cache: dict[str, list[dict]] = field(default_factory=dict)
    last_filter: str | None = None

    def add_turn(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        del self.history[: max(0, len(self.history) - MAX_HISTORY_TURNS * 2)]

    def cache_documents(self, signature: str, documents: list[dict]) -> None:
        self.document_cache[signature] = documents
        self.last_filter = signature

    def cached_documents(self, signature: str | None = None) -> list[dict]:
        key = signature or self.last_filter
        if key is None:
            return []
        return self.document_cache.get(key, [])


class SessionStore:
    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS):
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def get_or_create(self, session_id: str | None) -> Session:
        async with self._lock:
            self._evict_expired()
            if session_id and session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_seen = time.time()
                return session
            new_id = session_id or uuid.uuid4().hex
            session = Session(session_id=new_id)
            self._sessions[new_id] = session
            logger.info("session created", extra={"event": "session.created"})
            return session

    async def get(self, session_id: str) -> Session | None:
        async with self._lock:
            return self._sessions.get(session_id)

    def _evict_expired(self) -> None:
        cutoff = time.time() - self._ttl
        expired = [k for k, v in self._sessions.items() if v.last_seen < cutoff]
        for key in expired:
            del self._sessions[key]
        if expired:
            logger.info(
                "sessions evicted", extra={"event": "session.evicted", "count": len(expired)}
            )


_store = SessionStore()


def get_session_store() -> SessionStore:
    return _store
