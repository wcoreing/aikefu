"""企微回调：GET 验证 URL，POST 解密后触发微信客服拉消息。"""

from __future__ import annotations

import logging
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.services.message_pipe import handle_kf_callback
from app.wecom.wxbiz_crypto import WXBizMsgCrypt, WXBizMsgCryptError
from app.wecom.xml_parse import parse_plain_xml

logger = logging.getLogger(__name__)

router = APIRouter(tags=["wecom"])


def _crypt() -> WXBizMsgCrypt:
    s = get_settings()
    return WXBizMsgCrypt(
        s.wecom_token,
        s.wecom_encoding_aes_key,
        s.wecom_corp_id,
    )


@router.get("/callback")
async def wecom_verify(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(..., alias="timestamp"),
    nonce: str = Query(..., alias="nonce"),
    echostr: str = Query(..., alias="echostr"),
) -> PlainTextResponse:
    try:
        echo = _crypt().verify_url(msg_signature, timestamp, nonce, echostr)
    except WXBizMsgCryptError as e:
        logger.warning("URL 验证失败: %s", e)
        return PlainTextResponse("verify fail", status_code=403)
    return PlainTextResponse(content=echo)


@router.post("/callback")
async def wecom_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(..., alias="timestamp"),
    nonce: str = Query(..., alias="nonce"),
) -> Response:
    body = (await request.body()).decode("utf-8")
    try:
        plain_xml = _crypt().decrypt_msg(msg_signature, timestamp, nonce, body)
    except WXBizMsgCryptError as e:
        logger.warning("消息解密失败: %s", e)
        return PlainTextResponse("decrypt fail", status_code=403)

    ev = parse_plain_xml(plain_xml)
    if ev:
        background_tasks.add_task(
            handle_kf_callback,
            open_kfid=ev.open_kfid,
            sync_token=ev.token,
        )
    else:
        logger.debug("非 kf_msg_or_event，已忽略: %s", plain_xml[:200])

    s = get_settings()
    if s.wecom_plain_success_response:
        return PlainTextResponse("success")
    try:
        xml = _crypt().encrypt_reply("success", timestamp, nonce)
        return Response(content=xml, media_type="application/xml; charset=utf-8")
    except WXBizMsgCryptError:
        return PlainTextResponse("success")
