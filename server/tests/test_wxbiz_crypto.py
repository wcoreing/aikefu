"""加解密自洽测试（加密回包再解密）。"""

import xml.etree.ElementTree as ET

import pytest

from app.wecom.wxbiz_crypto import WXBizMsgCrypt, WXBizMsgCryptError


def test_encrypt_reply_roundtrip() -> None:
    token = "testtoken"
    encoding_aes_key = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
    corp_id = "wx5823bf96d3bd56c7"
    c = WXBizMsgCrypt(token, encoding_aes_key, corp_id)
    inner = (
        "<xml><ToUserName><![CDATA[to]]></ToUserName>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[hello]]></Content></xml>"
    )
    ts, nonce = "1409659813", "1372623149"
    outer = c.encrypt_reply(inner, ts, nonce)
    root = ET.fromstring(outer)
    enc = root.find("Encrypt").text
    msig = root.find("MsgSignature").text
    ts2 = root.find("TimeStamp").text
    nonce2 = root.find("Nonce").text
    assert enc and msig
    minimal = f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt></xml>"
    plain = c.decrypt_msg(msig, ts2, nonce2, minimal)
    assert "hello" in plain
    assert "text" in plain


def test_bad_signature() -> None:
    c = WXBizMsgCrypt("t", "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C", "wx5823bf96d3bd56c7")
    with pytest.raises(WXBizMsgCryptError):
        c.verify_url("bad", "1", "2", "xxx")
