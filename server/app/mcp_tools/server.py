from __future__ import annotations

import logging
import sys
import time
import uuid

from starlette.applications import Starlette
from starlette.routing import Mount

from fastmcp import FastMCP
import uvicorn

from app.config import get_settings
from app.services.group_broadcast import send_group_msg_by_tag as run_group_broadcast
from app.wecom.api import WecomAPIError, WecomKFClient
from app.wecom.contact_client import WecomContactAPIError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)

mcp = FastMCP("qiwei-wecom")

REPLY_NOTIFY = "已为您同步专属销售顾问，会尽快联系您~"
REPLY_TRANSFER = "已为您转接人工客服，请稍候~"


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in {"authorization", "cookie", "set-cookie", "x-api-key"}:
            redacted[k] = "***"
        else:
            redacted[k] = v
    return redacted


def _truncate_bytes(data: bytes, limit: int = 2048) -> str:
    if not data:
        return ""
    if len(data) <= limit:
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return repr(data)
    head = data[:limit]
    try:
        prefix = head.decode("utf-8", errors="replace")
    except Exception:
        prefix = repr(head)
    return f"{prefix}...[truncated {len(data) - limit} bytes]"


class RequestResponseLogMiddleware:
    """
    采用原生 ASGI Middleware 捕获响应体（避免 BaseHTTPMiddleware 将所有响应包装为 StreamingResponse 导致拿不到 body）。
    """

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger(__name__)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers") or []}
        request_id = headers.get("x-request-id") or uuid.uuid4().hex

        method = scope.get("method", "")
        path = scope.get("path", "")
        query = (scope.get("query_string") or b"").decode("latin-1")
        client = scope.get("client") or (None, None)
        client_host, client_port = client[0], client[1]

        req_content_type = headers.get("content-type", "")
        req_should_capture_body = method not in {"GET", "HEAD"} and (
            "application/json" in req_content_type
            or "application/x-www-form-urlencoded" in req_content_type
            or req_content_type.startswith("text/")
        )

        req_captured = bytearray()
        req_total = 0
        req_limit = 2048

        async def receive_wrapped():
            nonlocal req_total
            message = await receive()
            if req_should_capture_body and message.get("type") == "http.request":
                body = message.get("body") or b""
                req_total += len(body)
                if len(req_captured) < req_limit:
                    remain = req_limit - len(req_captured)
                    req_captured.extend(body[:remain])
            return message

        self.logger.info(
            "mcp http 请求 request_id=%s client=%s:%s method=%s path=%s query=%s headers=%s",
            request_id,
            client_host,
            client_port,
            method,
            path,
            query,
            _redact_headers(headers),
        )

        resp_status: int | None = None
        resp_headers: dict[str, str] = {}
        resp_content_type = ""
        resp_should_capture_body = False
        resp_captured = bytearray()
        resp_total = 0
        resp_limit = 4096

        def _should_log_body(ct: str) -> bool:
            lct = (ct or "").lower()
            return (
                "application/json" in lct
                or lct.startswith("text/")
                or "application/x-www-form-urlencoded" in lct
                or "application/xml" in lct
            )

        async def send_wrapped(message):
            nonlocal resp_status, resp_headers, resp_content_type, resp_should_capture_body, resp_total
            if message.get("type") == "http.response.start":
                resp_status = int(message.get("status") or 0)
                raw_headers = message.get("headers") or []
                resp_headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in raw_headers}
                resp_headers.setdefault("x-request-id", request_id)
                resp_content_type = resp_headers.get("content-type", "")
                resp_should_capture_body = _should_log_body(resp_content_type)

                # 需要把注入的 header 写回 message
                message = {**message, "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in resp_headers.items()]}
            elif message.get("type") == "http.response.body":
                if resp_should_capture_body:
                    body = message.get("body") or b""
                    resp_total += len(body)
                    if len(resp_captured) < resp_limit:
                        remain = resp_limit - len(resp_captured)
                        resp_captured.extend(body[:remain])
            await send(message)

        try:
            await self.app(scope, receive_wrapped, send_wrapped)
        except Exception:
            cost_ms = int((time.perf_counter() - start) * 1000)
            self.logger.exception(
                "mcp http 异常 request_id=%s cost_ms=%s method=%s path=%s",
                request_id,
                cost_ms,
                method,
                path,
            )
            raise
        finally:
            # 请求体日志（预览）
            if req_should_capture_body:
                self.logger.info(
                    "mcp http 请求体 request_id=%s req_len=%s body=%s",
                    request_id,
                    req_total,
                    _truncate_bytes(bytes(req_captured), limit=req_limit),
                )

            cost_ms = int((time.perf_counter() - start) * 1000)
            body_preview = ""
            resp_len: int | None = None
            if resp_should_capture_body:
                resp_len = resp_total
                body_preview = _truncate_bytes(bytes(resp_captured), limit=resp_limit)

            self.logger.info(
                "mcp http 响应 request_id=%s status=%s cost_ms=%s resp_content_type=%s resp_len=%s body=%s",
                request_id,
                resp_status,
                cost_ms,
                resp_content_type,
                resp_len,
                body_preview if resp_should_capture_body else "",
            )


# ------------------------------
# 工具定义（完全不变）
# ------------------------------
@mcp.tool()
async def notify_sales(
    external_userid: str,
    open_kfid: str,
    summary: str = "",
) -> dict:
    """
    高意向通知销售（工作流高意向分支调用）。

    作用：
    - 给内部销售/群组发送一条提醒消息（使用企微应用 `message/send`）。
    - 同时返回给客户的固定话术（供工作流“统一输出节点”直接回复客户）。

    入参：
    - external_userid: 企微微信客户 external_userid（用于在提醒中标识客户）
    - open_kfid: 客服账号 open_kfid（用于在提醒中标识会话来源）
    - summary: 可选摘要（建议由工作流/LLM 生成 1~2 句）

    依赖配置（必须在 `.env` 配好）：
    - WECOM_AGENT_ID: 自建应用 AgentId（整数）
    - WECOM_NOTIFY_TOUSER: 接收提醒的成员 userid（多个用 `|` 分隔）

    返回：
    - reply: 固定话术（给客户）
    - notified: 是否已成功发送内部提醒
    - warning:（可选）未配置或发送失败说明
    """
    s = get_settings()
    kf = WecomKFClient(s)
    detail = (
        f"【高意向客户】\n客户 external_userid：{external_userid}\n"
        f"客服账号 open_kfid：{open_kfid}\n摘要：{summary or '（无）'}"
    )
    notified = False
    err = ""
    if s.wecom_agent_id and (s.wecom_notify_touser or "").strip():
        try:
            await kf.send_application_text(
                touser=s.wecom_notify_touser.strip(),
                content=detail[:2048],
                agentid=s.wecom_agent_id,
            )
            notified = True
        except WecomAPIError as e:
            err = str(e)
            logging.getLogger(__name__).exception("notify_sales 发送失败")
    else:
        err = "未配置 WECOM_AGENT_ID 或 WECOM_NOTIFY_TOUSER，跳过内部通知"
        logging.getLogger(__name__).warning("%s", err)
    out = {"reply": REPLY_NOTIFY, "notified": notified}
    if err:
        out["warning"] = err
    return out


@mcp.tool()
async def transfer_to_human(
    external_userid: str,
    open_kfid: str,
) -> dict:
    """
    转人工（工作流“转人工请求分支”调用）。

    作用：
    - 调用企微客服会话变更接口：`/cgi-bin/kf/service_state/trans`
      - 若配置了指定接待人：转入人工接待（service_state=3 + servicer_userid）
      - 否则：进入待接入池（service_state=2）
    - 返回给客户的固定话术（供工作流直接回复）。

    入参：
    - external_userid: 微信客户 external_userid
    - open_kfid: 客服账号 open_kfid

    依赖配置（可选）：
    - WECOM_KF_DEFAULT_SERVICER_USERID: 指定接待成员 userid（配置则走 state=3）

    返回：
    - reply: 固定话术（给客户）
    - ok: 是否调用成功
    - service_state:（成功时）实际变更到的状态 2/3
    - error:（失败时）错误信息
    """
    s = get_settings()
    kf = WecomKFClient(s)
    serv = (s.wecom_kf_default_servicer_userid or "").strip() or None
    state = 3 if serv else 2
    try:
        await kf.service_state_trans(
            open_kfid=open_kfid,
            external_userid=external_userid,
            service_state=state,
            servicer_userid=serv,
        )
        return {"reply": REPLY_TRANSFER, "ok": True, "service_state": state}
    except WecomAPIError as e:
        logging.getLogger(__name__).exception("transfer_to_human 失败")
        return {"reply": REPLY_TRANSFER, "ok": False, "error": str(e)}


@mcp.tool()
async def send_group_msg_by_tag(tag: str, content: str) -> dict:
    """
    按企业客户标签群发微信客服消息（群发工作流 MCP 节点调用）。

    作用：
    - 解析标签（支持传标签名或标签 id）
    - 枚举命中该标签的客户 external_userid
    - 对模板 `content` 做变量替换后逐个调用 `kf/send_msg` 下发

    模板变量：
    - {name}: 客户名称（来自 externalcontact/get）
    - {nickname}: 当前实现同 {name}
    - {user_id}: 客户 external_userid

    依赖配置（必须/建议）：
    - WECOM_DEFAULT_OPEN_KFID: 群发使用的客服账号 open_kfid（必须）
    - WECOM_CONTACT_SECRET: 客户联系 Secret（建议；不填则回落用 WECOM_CORP_SECRET，但需具备客户联系权限）
    - WECOM_GROUP_MAX_RECIPIENTS: 单次群发最大客户数（默认 500）
    - WECOM_GROUP_SEND_INTERVAL_SEC: 群发发送间隔（默认 0.25s，降低风控）
    - WECOM_GROUP_FOLLOW_USERIDS:（可选）仅扫描这些成员名下客户（逗号分隔），避免全量扫描

    返回（与方案1约定一致）：
    - success: 成功发送数
    - fail: 失败发送数
    - total: 总客户数（本次尝试处理）
    - error:（失败时）错误信息
    """
    try:
        return await run_group_broadcast(tag=tag, content=content)
    except (WecomContactAPIError, ValueError) as e:
        logging.getLogger(__name__).exception("send_group_msg_by_tag 失败")
        return {"success": 0, "fail": 0, "total": 0, "error": str(e)}


# ------------------------------
# ✅ 修复：主函数（正确 SSE / HTTP 模式）
# ------------------------------
def main():
    s = get_settings()
    if s.mcp_transport == "stdio":
        mcp.run(transport="stdio")
        return

    # 直接使用 fastmcp 的 http_app（参考 bailianmcpdemo），不做额外包装
    mcp_asgi = mcp.http_app(path=s.mcp_path, stateless_http=True)
    # streamable-http 需要把 fastmcp 的 lifespan 传给父 ASGI 应用，
    # 否则会报：task group was not initialized。
    app = Starlette(
        routes=[Mount("/", app=mcp_asgi)],
        lifespan=getattr(mcp_asgi, "lifespan", None),
    )
    app.add_middleware(RequestResponseLogMiddleware)
    uvicorn.run(app, host=s.mcp_host or "0.0.0.0", port=s.mcp_port or 8000)


if __name__ == "__main__":
    main()