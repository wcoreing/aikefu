"""企微「客户联系」API：标签解析、按标签枚举客户（需客户联系权限的 Secret）。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_QYAPI = "https://qyapi.weixin.qq.com"


class WecomContactAPIError(Exception):
    def __init__(self, message: str, errcode: Optional[int] = None) -> None:
        self.errcode = errcode
        super().__init__(message)


class WecomContactClient:
    """使用客户联系 Secret（可与客服 Secret 不同）。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._s = settings or get_settings()
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def _secret(self) -> str:
        return self._s.wecom_contact_secret or self._s.wecom_corp_secret

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        async with self._lock:
            now = time.time()
            if self._token and now < self._token_expires_at - 120:
                return self._token
            if not self._s.wecom_corp_id or not self._secret():
                raise WecomContactAPIError("WECOM_CORP_ID / WECOM_CONTACT_SECRET 未配置")
            r = await client.get(
                f"{_QYAPI}/cgi-bin/gettoken",
                params={"corpid": self._s.wecom_corp_id, "corpsecret": self._secret()},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomContactAPIError(
                    data.get("errmsg", "gettoken failed"),
                    errcode=data.get("errcode"),
                )
            self._token = data["access_token"]
            self._token_expires_at = now + int(data.get("expires_in", 7200))
            return self._token

    async def get_follow_user_list(self) -> List[str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            r = await client.get(
                f"{_QYAPI}/cgi-bin/externalcontact/get_follow_user_list",
                params={"access_token": access},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomContactAPIError(
                    data.get("errmsg", "get_follow_user_list failed"),
                    errcode=data.get("errcode"),
                )
            return list(data.get("follow_user") or [])

    async def list_external_contacts(
        self, userid: str, cursor: str = "", limit: int = 100
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            body: Dict[str, Any] = {"userid": userid, "limit": min(limit, 100)}
            if cursor:
                body["cursor"] = cursor
            r = await client.post(
                f"{_QYAPI}/cgi-bin/externalcontact/list",
                params={"access_token": access},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomContactAPIError(
                    data.get("errmsg", "externalcontact/list failed"),
                    errcode=data.get("errcode"),
                )
            return data

    async def get_external_contact(self, external_userid: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            r = await client.post(
                f"{_QYAPI}/cgi-bin/externalcontact/get",
                params={"access_token": access},
                json={"external_userid": external_userid},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomContactAPIError(
                    data.get("errmsg", "externalcontact/get failed"),
                    errcode=data.get("errcode"),
                )
            return data

    async def get_corp_tag_list(
        self, tag_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            body: Dict[str, Any] = {}
            if tag_ids:
                body["tag_id"] = tag_ids
            r = await client.post(
                f"{_QYAPI}/cgi-bin/externalcontact/get_corp_tag_list",
                params={"access_token": access},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomContactAPIError(
                    data.get("errmsg", "get_corp_tag_list failed"),
                    errcode=data.get("errcode"),
                )
            return data

    async def resolve_tag_id(self, tag: str) -> str:
        """先按标签 id 校验；否则按标签名称精确匹配。"""
        t = tag.strip()
        if not t:
            raise WecomContactAPIError("tag 为空")
        by_id = await self.get_corp_tag_list(tag_ids=[t])
        for group in by_id.get("tag_group") or []:
            for item in group.get("tag") or []:
                if str(item.get("id")) == t:
                    return t
        data = await self.get_corp_tag_list()
        for group in data.get("tag_group") or []:
            for item in group.get("tag") or []:
                if item.get("name") == t:
                    tid = item.get("id")
                    if tid:
                        return str(tid)
        raise WecomContactAPIError(f"未找到标签 id 或名称为「{t}」的企业标签")

    def _follow_user_has_tag(
        self, detail: Dict[str, Any], follow_userid: str, tag_id: str
    ) -> bool:
        for fu in detail.get("follow_user") or []:
            if str(fu.get("userid")) != follow_userid:
                continue
            tags = fu.get("tag_id") or []
            return tag_id in [str(x) for x in tags]
        return False

    async def iter_external_in_tag(
        self, tag_id: str, follow_userids: Optional[List[str]] = None
    ) -> AsyncIterator[Tuple[str, str]]:
        """
        遍历带指定企业标签的客户（external_userid, 对外展示名）。
        通过「配置了客户联系」的成员客户列表 + 客户详情中的 tag 过滤。
        """
        users = follow_userids
        if not users:
            users = await self.get_follow_user_list()
        if self._s.wecom_group_follow_userids.strip():
            allow = {
                x.strip()
                for x in self._s.wecom_group_follow_userids.split(",")
                if x.strip()
            }
            users = [u for u in users if u in allow]
        seen: set[str] = set()
        for userid in users:
            cursor = ""
            while True:
                page = await self.list_external_contacts(userid, cursor=cursor)
                for row in page.get("external_contact_list") or []:
                    ext = row.get("external_userid")
                    if not ext or ext in seen:
                        continue
                    try:
                        detail = await self.get_external_contact(str(ext))
                    except WecomContactAPIError as e:
                        logger.warning("get external %s: %s", ext, e)
                        continue
                    if not self._follow_user_has_tag(detail, userid, tag_id):
                        continue
                    seen.add(str(ext))
                    name = (detail.get("external_contact") or {}).get("name") or ""
                    yield str(ext), str(name)
                    await asyncio.sleep(self._s.wecom_group_api_interval_sec)
                cursor = page.get("next_cursor") or ""
                if not cursor:
                    break
                await asyncio.sleep(self._s.wecom_group_api_interval_sec)
