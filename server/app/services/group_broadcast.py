"""按企业标签群发微信客服文本（MCP / 内部任务复用）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.config import Settings, get_settings
from app.wecom.api import WecomAPIError, WecomKFClient
from app.wecom.contact_client import WecomContactAPIError, WecomContactClient

logger = logging.getLogger(__name__)


def apply_broadcast_template(
    content: str, *, user_id: str, name: str, nickname: str
) -> str:
    name = name or ""
    nick = (nickname or name).strip()
    return (
        content.replace("{user_id}", user_id)
        .replace("{name}", name)
        .replace("{nickname}", nick)
    )


async def send_group_msg_by_tag(
    *,
    tag: str,
    content: str,
    settings: Optional[Settings] = None,
    kf: Optional[WecomKFClient] = None,
    contact: Optional[WecomContactClient] = None,
) -> Dict[str, Any]:
    """
    解析标签 → 枚举带该标签的客户 → 模板变量替换 → kf/send_msg。
    返回 {"success","fail","total"}，与方案1 MCP 出参一致。
    """
    s = settings or get_settings()
    kf = kf or WecomKFClient(s)
    contact = contact or WecomContactClient(s)
    open_kfid = (s.wecom_default_open_kfid or "").strip()
    if not open_kfid:
        raise ValueError("WECOM_DEFAULT_OPEN_KFID 未配置，无法群发客服消息")

    tag_id = await contact.resolve_tag_id(tag)
    success = 0
    fail = 0
    total = 0
    async for ext, display_name in contact.iter_external_in_tag(tag_id):
        if total >= s.wecom_group_max_recipients:
            logger.warning(
                "群发已达上限 wecom_group_max_recipients=%s",
                s.wecom_group_max_recipients,
            )
            break
        total += 1
        text = apply_broadcast_template(
            content, user_id=ext, name=display_name, nickname=display_name
        )
        try:
            await kf.send_text(
                open_kfid=open_kfid,
                external_userid=ext,
                content=text[:2048],
            )
            success += 1
        except WecomAPIError as e:
            logger.warning("群发失败 external_userid=%s: %s", ext, e)
            fail += 1
        await asyncio.sleep(s.wecom_group_send_interval_sec)

    return {"success": success, "fail": fail, "total": total}
