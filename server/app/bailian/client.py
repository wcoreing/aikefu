"""阿里云百炼应用（DashScope HTTP）：支持纯 Agent 与方案1 工作流入参。"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class BailianError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        self.status_code = status_code
        super().__init__(message)


def stable_kf_user_session_key(open_kfid: str, external_userid: str) -> str:
    """
    企微客服场景下，为同一 (open_kfid, external_userid) 生成稳定 session_id，
    与工作流「用户隔离」对齐；首条消息后仍以 API 返回的 session_id 续写多轮。
    """
    raw = f"{open_kfid}\x1f{external_userid}".encode("utf-8")
    return "kf-" + hashlib.sha256(raw).hexdigest()


def extract_reply_text(output: Any, *, _depth: int = 0) -> str:
    """从百炼 output 对象解析最终给客户的话术（兼容统一输出节点多种字段名）。"""
    if _depth > 6:
        raise BailianError("output 嵌套过深，无法解析回复")
    if output is None:
        raise BailianError("百炼 output 为空")
    if isinstance(output, str):
        s = output.strip()
        if not s:
            raise BailianError("百炼 output 文本为空")
        return s
    if not isinstance(output, dict):
        raise BailianError(f"百炼 output 类型异常: {type(output)}")

    for key in ("text", "final_reply", "reply", "answer", "content", "message"):
        v = output.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    for nest in ("result", "data", "output", "response"):
        inner = output.get(nest)
        if isinstance(inner, dict):
            try:
                return extract_reply_text(inner, _depth=_depth + 1)
            except BailianError:
                continue
        if isinstance(inner, str) and inner.strip():
            return inner.strip()

    keys = list(output.keys())[:24]
    raise BailianError(f"百炼 output 中未找到可回复文本，keys={keys}")


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

    def _build_input(
        self,
        *,
        prompt: str,
        session_id: Optional[str],
        user_id: Optional[str],
        messages: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        if self._s.bailian_invoke_mode == "workflow":
            if messages:
                raise BailianError("workflow 模式下不支持传入 messages，请改用 agent 模式")
            inp: Dict[str, Any] = {
                self._s.bailian_workflow_query_key: prompt,
                self._s.bailian_workflow_user_key: user_id or "",
            }
            if session_id:
                inp[self._s.bailian_workflow_session_key] = session_id
            return inp

        inp2: Dict[str, Any] = {}
        if messages:
            inp2["messages"] = messages
        else:
            inp2["prompt"] = prompt
        if session_id:
            inp2["session_id"] = session_id
        return inp2

    async def chat(
        self,
        *,
        prompt: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, Optional[str]]:
        """
        返回 (reply_text, session_id)。
        workflow：input 为 query + user_id + session_id（与方案1 开始节点对齐）。
        agent：input 为 prompt + session_id。
        """
        if not self._url:
            raise BailianError("BAILIAN_APP_ID 未配置")
        if not self._s.dashscope_api_key:
            raise BailianError("DASHSCOPE_API_KEY 未配置")

        inp = self._build_input(
            prompt=prompt,
            session_id=session_id,
            user_id=user_id,
            messages=messages,
        )
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
                    text = extract_reply_text(out)
                    new_sid = None
                    if isinstance(out, dict):
                        new_sid = out.get("session_id")
                    return text, new_sid if new_sid else session_id
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    logger.warning("bailian request retry %s: %s", attempt, e)
        raise BailianError(f"百炼请求失败: {last_exc}") from last_exc

    def _completion_url(self, app_id: str) -> str:
        base = self._s.bailian_base_url.rstrip("/")
        return f"{base}/api/v1/apps/{app_id}/completion"

    async def invoke_app_completion(
        self,
        *,
        app_id: str,
        input_obj: Dict[str, Any],
    ) -> tuple[str, Optional[str]]:
        """调用任意应用 ID（如群发工作流），自定义 input 字典。"""
        if not app_id:
            raise BailianError("app_id 为空")
        if not self._s.dashscope_api_key:
            raise BailianError("DASHSCOPE_API_KEY 未配置")
        url = self._completion_url(app_id)
        body: Dict[str, Any] = {"input": input_obj, "parameters": {}, "debug": {}}
        last_exc: Optional[Exception] = None
        async with httpx.AsyncClient(
            timeout=self._s.bailian_http_timeout_sec
        ) as client:
            for attempt in range(self._s.bailian_max_retries + 1):
                try:
                    r = await client.post(
                        url,
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
                    text = extract_reply_text(out)
                    new_sid = None
                    if isinstance(out, dict):
                        new_sid = out.get("session_id")
                    return text, new_sid
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    logger.warning("bailian invoke_app retry %s: %s", attempt, e)
        raise BailianError(f"百炼请求失败: {last_exc}") from last_exc

    async def invoke_group_workflow(self, *, tag: str, content: str) -> str:
        """群发极简工作流：开始节点仅 tag + content。"""
        aid = self._s.bailian_group_app_id
        if not aid:
            raise BailianError("BAILIAN_GROUP_APP_ID 未配置")
        inp = {
            self._s.bailian_group_tag_key: tag,
            self._s.bailian_group_content_key: content,
        }
        text, _ = await self.invoke_app_completion(app_id=aid, input_obj=inp)
        return text
