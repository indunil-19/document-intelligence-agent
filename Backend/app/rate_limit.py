"""Per-caller token bucket rate limiting.

Applied as a dependency on /chat only. /health and /mock-api are deliberately
exempt: the MCP server calls /mock-api over HTTP while a /chat request is being
handled, so limiting it would make a single chat turn throttle itself.
"""
import asyncio
import logging
import math
import time
from dataclasses import dataclass

from fastapi import Request

from app.config import get_settings
from app.errors import RateLimitError

logger = logging.getLogger(__name__)

# Caps the key length a caller can force into the bucket dict via the header.
MAX_CLIENT_ID_LENGTH = 128


@dataclass
class TokenBucket:
    """A single caller's bucket. Refilled lazily, on read."""

    capacity: float
    refill_rate: float  # tokens per second
    tokens: float
    updated_at: float

    def _refill(self, now: float) -> None:
        elapsed = now - self.updated_at
        # A non-monotonic clock could hand us a negative elapsed; ignore it rather
        # than draining or over-filling the bucket.
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.updated_at = now

    def consume(self, now: float, cost: float = 1.0) -> tuple[bool, float]:
        """Take `cost` tokens if available.

        Returns (allowed, retry_after_seconds). When refused, retry_after is the
        exact time needed to accrue the shortfall.
        """
        self._refill(now)
        if self.tokens >= cost:
            self.tokens -= cost
            return True, 0.0
        return False, (cost - self.tokens) / self.refill_rate


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float


class RateLimiter:
    """Token buckets keyed by caller id.

    Mirrors SessionStore: one asyncio.Lock guarding a dict, with a sync
    _evict_expired() that assumes the lock is already held.
    """

    def __init__(
        self,
        *,
        capacity: float,
        refill_rate: float,
        limit: int,
        idle_ttl_seconds: float,
        clock=time.monotonic,
    ):
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._limit = limit
        self._idle_ttl = idle_ttl_seconds
        # Injected so tests can drive time deterministically.
        self._clock = clock

    async def check(self, key: str, cost: float = 1.0) -> RateLimitResult:
        """Atomically refill, test and consume. The whole body holds the lock:
        splitting read from write would let two concurrent requests both observe
        the same token count and both pass.
        """
        async with self._lock:
            now = self._clock()
            self._evict_expired(now)

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self._capacity,
                    refill_rate=self._refill_rate,
                    tokens=self._capacity,
                    updated_at=now,
                )
                self._buckets[key] = bucket

            allowed, retry_after = bucket.consume(now, cost)
            return RateLimitResult(
                allowed=allowed,
                limit=self._limit,
                remaining=int(bucket.tokens),
                retry_after=retry_after,
            )

    def _evict_expired(self, now: float) -> None:
        """Drop buckets idle long enough to have fully refilled.

        Without this, one-off keys (rotating IPs, spoofed header values) would grow
        the dict without bound. A full bucket is indistinguishable from a fresh one,
        so dropping it changes nothing for the caller.
        """
        cutoff = now - self._idle_ttl
        expired = [k for k, b in self._buckets.items() if b.updated_at < cutoff]
        for key in expired:
            del self._buckets[key]
        if expired:
            logger.info(
                "rate limit buckets evicted",
                extra={"event": "ratelimit.evicted", "count": len(expired)},
            )

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Lazily built so it can read Settings.

    SessionStore builds its singleton at import time, which would be too early to
    see configuration.
    """
    global _limiter
    if _limiter is None:
        settings = get_settings()
        requests = max(1, settings.rate_limit_requests)
        window = max(0.001, settings.rate_limit_window_seconds)
        _limiter = RateLimiter(
            capacity=float(settings.rate_limit_burst or requests),
            refill_rate=requests / window,
            limit=requests,
            idle_ttl_seconds=settings.rate_limit_idle_ttl_seconds,
        )
    return _limiter


def reset_rate_limiter() -> None:
    """Drop the singleton so the next call rebuilds it from current settings."""
    global _limiter
    _limiter = None


def resolve_client_id(request: Request) -> str:
    """Identify the caller.

    There is no authentication in this app yet, so an explicit X-User-Id header is
    the identity, with the peer address as a fallback. Swap this one function when
    real auth arrives.
    """
    user = (request.headers.get("x-user-id") or "").strip()
    if user:
        return f"user:{user[:MAX_CLIENT_ID_LENGTH]}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


async def enforce_rate_limit(request: Request) -> None:
    """FastAPI dependency. Raises RateLimitError when the caller's bucket is empty."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return

    client_id = resolve_client_id(request)
    result = await get_rate_limiter().check(client_id)

    if result.allowed:
        return

    # Round up so the client never retries a fraction of a second too early.
    retry_after = max(1, math.ceil(result.retry_after))
    logger.warning(
        "rate limit exceeded",
        extra={
            "event": "ratelimit.rejected",
            "client_id": client_id,
            "limit": result.limit,
            "window_seconds": settings.rate_limit_window_seconds,
            "retry_after": retry_after,
        },
    )
    raise RateLimitError(
        f"Rate limit exceeded. Try again in {retry_after} seconds.",
        details={
            "limit": result.limit,
            "window_seconds": settings.rate_limit_window_seconds,
            "retry_after_seconds": retry_after,
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": "0",
        },
    )
