"""cursor、msg 去重、百炼 session 存储。"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ContextStore(Protocol):
    async def get_kf_cursor(self, open_kfid: str) -> str: ...
    async def set_kf_cursor(self, open_kfid: str, cursor: str) -> None: ...
    async def try_claim_msg(self, msgid: str, ttl_sec: int = 259200) -> bool: ...
    async def get_bailian_session(
        self, open_kfid: str, external_userid: str
    ) -> Optional[str]: ...
    async def set_bailian_session(
        self, open_kfid: str, external_userid: str, session_id: str
    ) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._cursors: dict[str, str] = {}
        self._claimed: dict[str, float] = {}
        self._sessions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_kf_cursor(self, open_kfid: str) -> str:
        return self._cursors.get(open_kfid, "")

    async def set_kf_cursor(self, open_kfid: str, cursor: str) -> None:
        async with self._lock:
            self._cursors[open_kfid] = cursor

    async def try_claim_msg(self, msgid: str, ttl_sec: int = 259200) -> bool:
        async with self._lock:
            if msgid in self._claimed:
                return False
            self._claimed[msgid] = ttl_sec
            return True

    def _sk(self, open_kfid: str, external_userid: str) -> str:
        return f"{open_kfid}:{external_userid}"

    async def get_bailian_session(
        self, open_kfid: str, external_userid: str
    ) -> Optional[str]:
        return self._sessions.get(self._sk(open_kfid, external_userid))

    async def set_bailian_session(
        self, open_kfid: str, external_userid: str, session_id: str
    ) -> None:
        async with self._lock:
            self._sessions[self._sk(open_kfid, external_userid)] = session_id


class RedisStore:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)

    async def get_kf_cursor(self, open_kfid: str) -> str:
        v = await self._redis.get(f"qiwei:kf_cursor:{open_kfid}")
        return v or ""

    async def set_kf_cursor(self, open_kfid: str, cursor: str) -> None:
        if cursor:
            await self._redis.set(f"qiwei:kf_cursor:{open_kfid}", cursor)
        else:
            await self._redis.delete(f"qiwei:kf_cursor:{open_kfid}")

    async def try_claim_msg(self, msgid: str, ttl_sec: int = 259200) -> bool:
        ok = await self._redis.set(
            f"qiwei:msg:{msgid}",
            "1",
            nx=True,
            ex=ttl_sec,
        )
        return bool(ok)

    async def get_bailian_session(
        self, open_kfid: str, external_userid: str
    ) -> Optional[str]:
        return await self._redis.get(f"qiwei:bailian_session:{open_kfid}:{external_userid}")

    async def set_bailian_session(
        self, open_kfid: str, external_userid: str, session_id: str
    ) -> None:
        await self._redis.set(
            f"qiwei:bailian_session:{open_kfid}:{external_userid}",
            session_id,
            ex=86400 * 7,
        )


def build_store(settings: Optional[Settings] = None) -> ContextStore:
    s = settings or get_settings()
    if s.redis_url:
        try:
            return RedisStore(s.redis_url)
        except Exception as e:  # noqa: BLE001
            logger.exception("Redis 连接失败，降级内存: %s", e)
    return MemoryStore()
