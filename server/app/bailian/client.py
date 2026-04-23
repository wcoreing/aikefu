"""阿里云百炼应用（DashScope SDK）：支持 Agent / 工作流应用调用。"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

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
        # DashScope SDK 的 output 往往是对象（含 .text / .session_id）
        text = getattr(output, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
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

    def _mask(self, s: str, keep: int = 4) -> str:
        if not s:
            return ""
        if len(s) <= keep:
            return "*" * len(s)
        return f"{s[:keep]}****"

    async def chat(
        self,
        *,
        prompt: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        open_kfid: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        """
        返回 (reply_text, session_id)。
        workflow：SDK 为 prompt + biz_params（biz_params 与开始节点自定义参数对齐）。
        agent：input 为 prompt + session_id。
        """
        if not (self._s.bailian_app_id or "").strip():
            raise BailianError("BAILIAN_APP_ID 未配置")
        if not self._s.dashscope_api_key:
            raise BailianError("DASHSCOPE_API_KEY 未配置")

        p = (prompt or "").strip()
        if not p and not messages:
            raise BailianError("prompt 为空，无法调用百炼")

        from dashscope import Application  # type: ignore

        if messages:
            raise BailianError("当前实现未启用 messages 入参，请使用 prompt/session_id 方式")

        mode = self._s.bailian_invoke_mode
        app_id = (self._s.bailian_app_id or "").strip()
        masked_app = self._mask(app_id)

        if mode == "workflow":
            # 官方 SDK：prompt + biz_params（biz_params 键名需与开始节点参数一致）
            biz_params: Dict[str, Any] = {}
            qk = (self._s.bailian_workflow_query_key or "").strip()
            if qk and qk != "prompt":
                biz_params[qk] = p
            uk = (self._s.bailian_workflow_user_key or "").strip()
            if uk:
                biz_params[uk] = user_id or ""
            ok = (self._s.bailian_workflow_open_kfid_key or "").strip()
            if ok:
                biz_params[ok] = open_kfid or ""
            sk = (self._s.bailian_workflow_summary_key or "").strip()
            if sk:
                biz_params[sk] = summary if summary is not None else ""
            sess_k = (self._s.bailian_workflow_session_key or "").strip()
            if session_id and sess_k:
                biz_params[sess_k] = session_id
            biz_keys = sorted(list((biz_params or {}).keys()))[:32]
            logger.info(
                "bailian sdk call workflow app_id=%s prompt_len=%s session=%s biz_keys=%s",
                masked_app,
                len(p),
                bool(session_id),
                biz_keys,
            )
            resp = Application.call(
                api_key=self._s.dashscope_api_key,
                app_id=app_id,
                prompt=p,
                biz_params=biz_params or None,
            )
        else:
            logger.info(
                "bailian sdk call agent app_id=%s prompt_len=%s session=%s",
                masked_app,
                len(p),
                bool(session_id),
            )
            resp = Application.call(
                api_key=self._s.dashscope_api_key,
                app_id=app_id,
                prompt=p,
                session_id=session_id,
            )

        status = getattr(resp, "status_code", None)
        request_id = getattr(resp, "request_id", None)
        code = getattr(resp, "code", None)
        message = getattr(resp, "message", None)
        logger.info(
            "bailian sdk response app_id=%s status=%s request_id=%s code=%s",
            masked_app,
            status,
            request_id,
            code,
        )

        if status is not None and int(status) != 200:
            raise BailianError(
                f"HTTP {status}: {code or ''} {message or ''} (request_id={request_id})".strip(),
                status_code=int(status),
            )

        out = getattr(resp, "output", None)
        if out is None:
            raise BailianError(
                f"百炼输出为空 code={code} message={message} request_id={request_id}"
            )
        text = extract_reply_text(getattr(out, "text", None) or out)
        new_sid = getattr(out, "session_id", None)
        return text, (new_sid if new_sid else session_id)

    async def invoke_app_completion(
        self,
        *,
        app_id: str,
        input_obj: Dict[str, Any],
    ) -> tuple[str, Optional[str]]:
        """调用任意应用 ID（SDK 版本）。input_obj 支持 prompt / biz_params / session_id。"""
        if not app_id:
            raise BailianError("app_id 为空")
        if not self._s.dashscope_api_key:
            raise BailianError("DASHSCOPE_API_KEY 未配置")

        from dashscope import Application  # type: ignore

        prompt = (input_obj.get("prompt") or "").strip()
        if not prompt:
            raise BailianError("input_obj.prompt 为空")
        biz_params = input_obj.get("biz_params")
        session_id = input_obj.get("session_id")

        masked_app = self._mask(app_id)
        biz_keys = sorted(list((biz_params or {}).keys()))[:32] if isinstance(biz_params, dict) else []
        logger.info(
            "bailian sdk invoke app_id=%s prompt_len=%s session=%s biz_keys=%s",
            masked_app,
            len(prompt),
            bool(session_id),
            biz_keys,
        )
        resp = Application.call(
            api_key=self._s.dashscope_api_key,
            app_id=app_id,
            prompt=prompt,
            biz_params=biz_params if isinstance(biz_params, dict) else None,
            session_id=session_id,
        )
        status = getattr(resp, "status_code", None)
        request_id = getattr(resp, "request_id", None)
        code = getattr(resp, "code", None)
        message = getattr(resp, "message", None)
        logger.info(
            "bailian sdk invoke response app_id=%s status=%s request_id=%s code=%s",
            masked_app,
            status,
            request_id,
            code,
        )
        if status is not None and int(status) != 200:
            raise BailianError(
                f"HTTP {status}: {code or ''} {message or ''} (request_id={request_id})".strip(),
                status_code=int(status),
            )
        out = getattr(resp, "output", None)
        if out is None:
            raise BailianError(
                f"百炼输出为空 code={code} message={message} request_id={request_id}"
            )
        text = extract_reply_text(getattr(out, "text", None) or out)
        new_sid = getattr(out, "session_id", None)
        return text, new_sid

    async def invoke_group_workflow(self, *, tag: str, content: str) -> str:
        """群发极简工作流：开始节点仅 tag + content。"""
        aid = self._s.bailian_group_app_id
        if not aid:
            raise BailianError("BAILIAN_GROUP_APP_ID 未配置")
        inp = {
            "prompt": content,
            "biz_params": {
                self._s.bailian_group_tag_key: tag,
                self._s.bailian_group_content_key: content,
            },
        }
        text, _ = await self.invoke_app_completion(app_id=aid, input_obj=inp)
        return text
