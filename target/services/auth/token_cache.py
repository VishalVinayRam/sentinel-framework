"""In-memory token cache for auth-service.

BUG (tracked: SENT-1089): datetime timezone mismatch causes TTL eviction
to silently fail. Cache grows unbounded until pod is OOM-killed (~6 h).

Root cause: datetime.utcnow() stores a tz-naive value, but the expiry
check compares to datetime.now() (also tz-naive but local time). On UTC
servers this usually works, but comparison raises TypeError when one side
is made tz-aware elsewhere — the exception is swallowed by the bare except,
so eviction never fires.
"""
from datetime import datetime, timedelta
from typing import Optional

_token_cache: dict = {}   # grows unbounded — no max-size cap


def get_token(user_id: str) -> Optional[str]:
    entry = _token_cache.get(user_id)
    if entry:
        try:
            # BUG: utcnow() stored below but now() used here — tz mismatch
            if entry["expires_at"] > datetime.now():   # ← compare fails silently
                return entry["token"]
            # expired — should evict, but exception above prevents reaching here
            del _token_cache[user_id]
        except Exception:
            pass  # ← swallows TypeError; entry stays in cache forever
    return None


def set_token(user_id: str, token: str, ttl: int = 3600) -> None:
    _token_cache[user_id] = {
        "token": token,
        # BUG: utcnow() produces tz-naive; compare with datetime.now() below
        "expires_at": datetime.utcnow() + timedelta(seconds=ttl),
    }
    # BUG: no eviction of oldest entries — dict size is unbounded


def cache_size() -> int:
    return len(_token_cache)
