"""企微微信客服 HTTP API：access_token、sync_msg、send_msg。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_QYAPI = "https://qyapi.weixin.qq.com"


class WecomAPIError(Exception):
    def __init__(self, message: str, errcode: Optional[int] = None) -> None:
        self.errcode = errcode
        super().__init__(message)


class WecomKFClient:
    """带内存 token 缓存；单进程够用，多进程需 Redis 或 sticky。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._s = settings or get_settings()
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        async with self._lock:
            now = time.time()
            if self._token and now < self._token_expires_at - 120:
                return self._token
            if not self._s.wecom_corp_id or not self._s.wecom_corp_secret:
                raise WecomAPIError("WECOM_CORP_ID / WECOM_CORP_SECRET 未配置")
            url = f"{_QYAPI}/cgi-bin/gettoken"
            r = await client.get(
                url,
                params={
                    "corpid": self._s.wecom_corp_id,
                    "corpsecret": self._s.wecom_corp_secret,
                },
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomAPIError(
                    data.get("errmsg", "gettoken failed"),
                    errcode=data.get("errcode"),
                )
            self._token = data["access_token"]
            self._token_expires_at = now + int(data.get("expires_in", 7200))
            return self._token

    async def sync_msg(
        self,
        *,
        open_kfid: str,
        token: str,
        cursor: str = "",
        limit: int = 1000,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            url = f"{_QYAPI}/cgi-bin/kf/sync_msg"
            body: Dict[str, Any] = {
                "open_kfid": open_kfid,
                "token": token,
                "limit": limit,
            }
            if cursor:
                body["cursor"] = cursor
            r = await client.post(url, params={"access_token": access}, json=body)
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomAPIError(
                    data.get("errmsg", "sync_msg failed"),
                    errcode=data.get("errcode"),
                )
            return data

    async def send_text(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        content: str,
        msgid: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            url = f"{_QYAPI}/cgi-bin/kf/send_msg"
            payload: Dict[str, Any] = {
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgtype": "text",
                "text": {"content": content},
            }
            if msgid:
                payload["msgid"] = msgid
            r = await client.post(url, params={"access_token": access}, json=payload)
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomAPIError(
                    data.get("errmsg", "send_msg failed"),
                    errcode=data.get("errcode"),
                )
            return data

    async def sync_msg_all_pages(
        self,
        *,
        open_kfid: str,
        token: str,
        initial_cursor: str = "",
    ) -> tuple[List[Dict[str, Any]], str]:
        """同一 token 下分页拉取，直到 has_more 为 0。返回 (消息列表, 最后响应的 next_cursor)。"""
        out: List[Dict[str, Any]] = []
        cursor = initial_cursor
        last_next_cursor = ""
        while True:
            data = await self.sync_msg(
                open_kfid=open_kfid, token=token, cursor=cursor, limit=1000
            )
            msg_list = data.get("msg_list") or []
            for m in msg_list:
                if isinstance(m, dict):
                    out.append(m)
            last_next_cursor = data.get("next_cursor") or ""
            if not int(data.get("has_more") or 0):
                break
            cursor = last_next_cursor
        return out, last_next_cursor

    async def service_state_trans(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        service_state: int,
        servicer_userid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """变更微信客服会话状态（如转人工 service_state=3 需 servicer_userid）。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            body: Dict[str, Any] = {
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "service_state": service_state,
            }
            if servicer_userid:
                body["servicer_userid"] = servicer_userid
            r = await client.post(
                f"{_QYAPI}/cgi-bin/kf/service_state/trans",
                params={"access_token": access},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomAPIError(
                    data.get("errmsg", "service_state/trans failed"),
                    errcode=data.get("errcode"),
                )
            return data

    async def service_state_get(
        self,
        *,
        open_kfid: str,
        external_userid: str,
    ) -> Dict[str, Any]:
        """获取微信客服会话状态：/cgi-bin/kf/service_state/get"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            body: Dict[str, Any] = {
                "open_kfid": open_kfid,
                "external_userid": external_userid,
            }
            r = await client.post(
                f"{_QYAPI}/cgi-bin/kf/service_state/get",
                params={"access_token": access},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomAPIError(
                    data.get("errmsg", "service_state/get failed"),
                    errcode=data.get("errcode"),
                )
            return data

    async def send_application_text(
        self,
        *,
        touser: str,
        content: str,
        agentid: int,
    ) -> Dict[str, Any]:
        """应用发消息到企业成员（文本），用于通知销售等。"""
        if not agentid:
            raise WecomAPIError("WECOM_AGENT_ID 未配置")
        async with httpx.AsyncClient(timeout=30.0) as client:
            access = await self._ensure_token(client)
            payload: Dict[str, Any] = {
                "touser": touser,
                "msgtype": "text",
                "agentid": agentid,
                "text": {"content": content[:2048]},
            }
            r = await client.post(
                f"{_QYAPI}/cgi-bin/message/send",
                params={"access_token": access},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise WecomAPIError(
                    data.get("errmsg", "message/send failed"),
                    errcode=data.get("errcode"),
                )
            return data
