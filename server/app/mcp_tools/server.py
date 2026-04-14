"""
企微相关 MCP 工具（纯 MCP 服务）。

运行：
  python -m app.mcp_tools
  或 qiwei-mcp（安装 editable 后）
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send
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

s = get_settings()
mcp = FastMCP(
    "qiwei-wecom",
    host=s.mcp_host,
    port=s.mcp_port,
    streamable_http_path=s.mcp_path,
    json_response=s.mcp_json_response,
    stateless_http=s.mcp_stateless_http,
)

REPLY_NOTIFY = "已为您同步专属销售顾问，会尽快联系您~"
REPLY_TRANSFER = "已为您转接人工客服，请稍候~"


@mcp.tool()
async def notify_sales(
    external_userid: str,
    open_kfid: str,
    summary: str = "",
) -> dict:
    """
    高意向分支：给内部成员发送应用文本提醒（需配置 WECOM_AGENT_ID、WECOM_NOTIFY_TOUSER）。
    返回固定 reply 供工作流展示给客户。
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
            logging.getLogger(__name__).exception("notify_sales message/send 失败")
    else:
        err = "未配置 WECOM_AGENT_ID 或 WECOM_NOTIFY_TOUSER，跳过内部通知"
        logging.getLogger(__name__).warning("%s", err)
    out: dict = {"reply": REPLY_NOTIFY, "notified": notified}
    if err:
        out["warning"] = err
    return out


@mcp.tool()
async def transfer_to_human(
    external_userid: str,
    open_kfid: str,
) -> dict:
    """
    转人工：调用 kf/service_state/trans。
    若配置 WECOM_KF_DEFAULT_SERVICER_USERID 则转指定接待（state=3），否则进待接入池（state=2）。
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
        return {
            "reply": REPLY_TRANSFER,
            "ok": False,
            "error": str(e),
        }


@mcp.tool()
async def send_group_msg_by_tag(tag: str, content: str) -> dict:
    """
    按企业客户标签群发微信客服文本：支持模板变量 {name}、{nickname}、{user_id}。
    需客户联系权限（WECOM_CONTACT_SECRET）与 WECOM_DEFAULT_OPEN_KFID。
    返回 success / fail / total。
    """
    try:
        return await run_group_broadcast(tag=tag, content=content)
    except (WecomContactAPIError, ValueError) as e:
        logging.getLogger(__name__).exception("send_group_msg_by_tag")
        return {"success": 0, "fail": 0, "total": 0, "error": str(e)}


def main() -> None:
    s2 = get_settings()
    if s2.mcp_transport == "stdio":
        mcp.run(transport="stdio")
        return

    # 百炼在“获取工具”阶段可能会先发空 POST/非 JSON 探测。
    # 这里做一个最小兼容包装：空 body / 非 JSON-RPC 直接返回 200 探活 JSON；
    # 正常 MCP JSON-RPC 再交给 FastMCP 的 ASGI app。
    inner = mcp.sse_app() if s2.mcp_transport == "sse" else mcp.streamable_http_app()

    async def entry(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return

        path = scope.get("path") or ""
        if path != s2.mcp_path:
            await inner(scope, receive, send)
            return

        method = (scope.get("method") or "GET").upper()
        if method == "GET":
            # MCP GET(SSE) 需要 Accept:text/event-stream；否则当作探活
            headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
            if "text/event-stream" not in headers.get("accept", ""):
                await JSONResponse({"status": "ok", "name": "qiwei-wecom"})(scope, receive, send)
                return

        if method == "POST":
            # 读取一次 body：空/非 JSON 时返回探活，避免 MCP 解析 -32700
            body = b""

            async def _recv() -> dict:  # type: ignore[no-untyped-def]
                nonlocal body
                msg = await receive()
                if msg.get("type") == "http.request":
                    body += msg.get("body") or b""
                return msg

            # 先把 body 读完
            more = True
            while more:
                m = await _recv()
                more = bool(m.get("more_body"))

            if not body.strip():
                await JSONResponse({"status": "ok", "name": "qiwei-wecom"})(scope, receive, send)
                return

            # 把 body 放回给 inner 再读一次
            sent = False

            async def _replay() -> dict:  # type: ignore[no-untyped-def]
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            await inner(scope, _replay, send)
            return

        await inner(scope, receive, send)

    app = Starlette(routes=[Route(s2.mcp_path, endpoint=entry, methods=["GET", "POST", "DELETE"])])
    uvicorn.run(app, host=s2.mcp_host, port=s2.mcp_port)


if __name__ == "__main__":
    main()
