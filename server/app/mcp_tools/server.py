from __future__ import annotations

import logging
import sys

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
    uvicorn.run(app, host=s.mcp_host or "0.0.0.0", port=s.mcp_port or 8000)


if __name__ == "__main__":
    main()