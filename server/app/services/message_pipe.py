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
    #打印所有参数
    logger.info("open_kfid: %s", open_kfid)
    logger.info("sync_token: %s", sync_token)
    logger.info("store: %s", store)
    logger.info("kf: %s", kf)
    logger.info("bailian: %s", bailian)
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
    logger.info("messages: %s", messages)

    # Redis 数据丢失/首次对话场景兜底：当某个客户没有 session_id 时，只处理该客户最新一条客户文本，
    # 避免 cursor/claim 丢失导致历史消息全部重放。
    latest_mid_by_user: dict[tuple[str, str], str] = {}
    latest_ts_by_user: dict[tuple[str, str], int] = {}
    for msg in messages:
        if msg.get("origin") != _ORIGIN_CUSTOMER:
            continue
        if msg.get("msgtype") != "text":
            continue
        text_obj = msg.get("text") or {}
        content = text_obj.get("content")
        if not content or not isinstance(content, str):
            continue
        external_userid = msg.get("external_userid")
        mid = msg.get("msgid")
        okfid0 = msg.get("open_kfid") or open_kfid
        if not external_userid or not mid:
            continue
        key = (str(okfid0), str(external_userid))
        try:
            ts = int(msg.get("send_time") or 0)
        except Exception:  # noqa: BLE001
            ts = 0
        if key not in latest_mid_by_user or ts >= latest_ts_by_user.get(key, -1):
            latest_mid_by_user[key] = str(mid)
            latest_ts_by_user[key] = ts

    for msg in messages:
        mid = msg.get("msgid")
        origin = msg.get("origin")
        if origin != _ORIGIN_CUSTOMER:
            continue
        if msg.get("msgtype") != "text":
            continue
        text_obj = msg.get("text") or {}
        #打印text_obj
        logger.info("text_obj: %s", text_obj)
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
        if not prev_session:
            key = (str(okfid), str(external_userid))
            latest_mid = latest_mid_by_user.get(key)
            if latest_mid and str(mid) != latest_mid:
                logger.info(
                    "skip replay msg(no session): msgid=%s latest_msgid=%s open_kfid=%s external_userid=%s",
                    mid,
                    latest_mid,
                    okfid,
                    external_userid,
                )
                continue
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
                open_kfid=str(okfid),
                summary="",
            )
        except BailianError as e:
            logger.exception("百炼调用失败 msgid=%s: %s", mid, e)
            try:
                await kf.send_text(
                    open_kfid=okfid,
                    external_userid=str(external_userid),
                    content="抱歉，智能服务暂时不可用，请稍后再试。",
                )
            except WecomAPIError as we:
                logger.exception(
                    "发送降级文案失败 api=%s msgid=%s open_kfid=%s external_userid=%s errcode=%s errmsg=%s",
                    we.api,
                    mid,
                    okfid,
                    external_userid,
                    we.errcode,
                    str(we),
                )
            continue

        if new_session:
            await store.set_bailian_session(
                okfid, str(external_userid), str(new_session)
            )

        # 发送前先判断会话状态，避免人工接待/已结束等状态下触发 95018
        # service_state: 0 未处理 / 1 智能助手接待 -> 允许 API 回复
        #               2 待接入池排队 / 3 人工接待 / 4 已结束/未开始 -> 跳过发送
        try:
            st = await kf.service_state_get(
                open_kfid=str(okfid),
                external_userid=str(external_userid),
            )
            ss = st.get("service_state")
            if ss not in (0, 1):
                logger.warning(
                    "skip send_msg by service_state api=%s msgid=%s open_kfid=%s external_userid=%s service_state=%s servicer_userid=%s",
                    "/cgi-bin/kf/service_state/get",
                    mid,
                    okfid,
                    external_userid,
                    ss,
                    st.get("servicer_userid"),
                )
                continue
        except WecomAPIError as se:
            # 获取状态失败时仍尝试发送，但把接口与错误原因打全，便于后续排障
            logger.error(
                "service_state_get failed api=%s msgid=%s open_kfid=%s external_userid=%s errcode=%s errmsg=%s",
                se.api,
                mid,
                okfid,
                external_userid,
                se.errcode,
                str(se),
            )

        try:
            await kf.send_text(
                open_kfid=okfid,
                external_userid=str(external_userid),
                content=reply[:2048],
            )
        except WecomAPIError as we:
            if we.errcode == 95018:
                try:
                    st = await kf.service_state_get(
                        open_kfid=str(okfid),
                        external_userid=str(external_userid),
                    )
                    logger.error(
                        "session invalid(api=%s): msgid=%s open_kfid=%s external_userid=%s",
                        we.api,
                        mid,
                        okfid,
                        external_userid,
                    )
                    logger.error(
                        "service_state_get resp api=%s msgid=%s open_kfid=%s external_userid=%s service_state=%s servicer_userid=%s",
                        "/cgi-bin/kf/service_state/get",
                        mid,
                        okfid,
                        external_userid,
                        st.get("service_state"),
                        st.get("servicer_userid"),
                    )
                except WecomAPIError as se:
                    logger.error(
                        "send_msg session invalid but state_get failed api=%s msgid=%s open_kfid=%s external_userid=%s errcode=%s errmsg=%s",
                        se.api,
                        mid,
                        okfid,
                        external_userid,
                        se.errcode,
                        str(se),
                    )
            logger.exception(
                "send_msg 失败 api=%s msgid=%s open_kfid=%s external_userid=%s errcode=%s errmsg=%s",
                we.api,
                mid,
                okfid,
                external_userid,
                we.errcode,
                str(we),
            )
