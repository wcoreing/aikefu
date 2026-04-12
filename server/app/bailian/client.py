"""阿里云百炼智能体应用（DashScope HTTP）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class BailianError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class BailianAppClient:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._s = settings or get_settings()
        base = self._s.bailian_base_url.rstrip("/")
        if not self._s.bailian_app_id:
            self._url = ""
        else:
            self._url = f"{base}/api/v1/apps/{self._s.bailian_app_id}/completion"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._s.dashscope_api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        *,
        prompt: str,
        session_id: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, Optional[str]]:
        """
        返回 (reply_text, session_id)。
        优先使用 session_id 多轮；若百炼侧返回新 session_id 则更新。
        """
        if not self._url:
            raise BailianError("BAILIAN_APP_ID 未配置")
        if not self._s.dashscope_api_key:
            raise BailianError("DASHSCOPE_API_KEY 未配置")

        inp: Dict[str, Any] = {}
        if messages:
            inp["messages"] = messages
        else:
            inp["prompt"] = prompt
        if session_id:
            inp["session_id"] = session_id

        body: Dict[str, Any] = {"input": inp, "parameters": {}, "debug": {}}

        last_exc: Optional[Exception] = None
        async with httpx.AsyncClient(
            timeout=self._s.bailian_http_timeout_sec
        ) as client:
            for attempt in range(self._s.bailian_max_retries + 1):
                try:
                    r = await client.post(
                        self._url,
                        headers=self._headers(),
                        json=body,
                    )
                    if r.status_code >= 400:
                        raise BailianError(
                            f"HTTP {r.status_code}: {r.text[:500]}",
                            status_code=r.status_code,
                        )
                    data = r.json()
                    out = data.get("output")
                    if out is None:
                        code = data.get("code") or data.get("message")
                        raise BailianError(f"百炼错误: {code or data!r:.400}")
                    out = out or {}
                    text = out.get("text")
                    if text is None:
                        raise BailianError(f"unexpected response: {data!r:.500}")
                    new_sid = out.get("session_id")
                    return str(text), new_sid if new_sid else session_id
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    logger.warning("bailian request retry %s: %s", attempt, e)
        raise BailianError(f"百炼请求失败: {last_exc}") from last_exc
