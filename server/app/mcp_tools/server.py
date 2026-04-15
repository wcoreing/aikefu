from __future__ import annotations

import logging
import sys

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

# ------------------------------
# 工具定义（完全不变）
# ------------------------------
@mcp.tool()
async def notify_sales(
    external_userid: str,
    open_kfid: str,
    summary: str = "",
) -> dict:
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
    app = mcp.http_app(path=s.mcp_path)
    uvicorn.run(
        app,
        host=s.mcp_host or "0.0.0.0",
        port=s.mcp_port or 8000,
    )


if __name__ == "__main__":
    main()