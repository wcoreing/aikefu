"""微信客服回调后的拉消息、百炼、回包。"""

from __future__ import annotations

import logging

from app.bailian.client import (
    BailianAppClient,
    BailianError,
    stable_kf_user_session_key,
)
from app.config import get_settings
from app.services.context_store import ContextStore, build_store
from app.wecom.api import WecomAPIError, WecomKFClient

logger = logging.getLogger(__name__)

_ORIGIN_CUSTOMER = 3


async def handle_kf_callback(
    *,
    open_kfid: str,
    sync_token: str,
    store: ContextStore | None = None,
    kf: WecomKFClient | None = None,
    bailian: BailianAppClient | None = None,
) -> None:
    """
    在 BackgroundTasks 中执行：sync_msg 分页 → 客户文本 → 百炼 → send_msg。
    """
    s = get_settings()
    store = store or build_store(s)
    kf = kf or WecomKFClient(s)
    bailian = bailian or BailianAppClient(s)

    cursor = await store.get_kf_cursor(open_kfid)
    try:
        messages, next_cursor = await kf.sync_msg_all_pages(
            open_kfid=open_kfid,
            token=sync_token,
            initial_cursor=cursor,
        )
    except WecomAPIError:
        logger.exception("sync_msg 失败 open_kfid=%s", open_kfid)
        return

    if next_cursor:
        await store.set_kf_cursor(open_kfid, next_cursor)

    for msg in messages:
        mid = msg.get("msgid")
        origin = msg.get("origin")
        if origin != _ORIGIN_CUSTOMER:
            continue
        if msg.get("msgtype") != "text":
            continue
        text_obj = msg.get("text") or {}
        content = text_obj.get("content")
        if not content or not isinstance(content, str):
            continue
        external_userid = msg.get("external_userid")
        okfid = msg.get("open_kfid") or open_kfid
        if not external_userid or not mid:
            continue
        if not await store.try_claim_msg(str(mid)):
            continue

        prev_session = await store.get_bailian_session(okfid, str(external_userid))
        if s.bailian_invoke_mode == "workflow":
            session_for_bailian = prev_session or stable_kf_user_session_key(
                okfid, str(external_userid)
            )
            user_for_bailian = str(external_userid)
        else:
            session_for_bailian = prev_session
            user_for_bailian = None

        try:
            reply, new_session = await bailian.chat(
                prompt=content,
                session_id=session_for_bailian,
                user_id=user_for_bailian,
            )
        except BailianError as e:
            logger.exception("百炼调用失败 msgid=%s: %s", mid, e)
            try:
                await kf.send_text(
                    open_kfid=okfid,
                    external_userid=str(external_userid),
                    content="抱歉，智能服务暂时不可用，请稍后再试。",
                )
            except WecomAPIError:
                logger.exception("发送降级文案失败")
            continue

        if new_session:
            await store.set_bailian_session(
                okfid, str(external_userid), str(new_session)
            )

        try:
            await kf.send_text(
                open_kfid=okfid,
                external_userid=str(external_userid),
                content=reply[:2048],
            )
        except WecomAPIError:
            logger.exception("send_msg 失败 msgid=%s", mid)
