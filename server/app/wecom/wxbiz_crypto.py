"""企业微信消息加解密（与官方 WXBizMsgCrypt 行为一致，UTF-8）。"""

from __future__ import annotations

import base64
import hashlib
import secrets
import struct
import xml.etree.ElementTree as ET
from typing import Tuple

from Crypto.Cipher import AES


class WXBizMsgCryptError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


def _sha1_hex(*parts: str) -> str:
    s = "".join(sorted(parts)).encode("utf-8")
    return hashlib.sha1(s).hexdigest()


def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    n = block_size - (len(data) % block_size)
    return data + bytes([n]) * n


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    if n < 1 or n > 32:
        raise WXBizMsgCryptError(-40007, "invalid pkcs7 padding")
    if data[-n:] != bytes([n]) * n:
        raise WXBizMsgCryptError(-40007, "invalid pkcs7 padding")
    return data[:-n]


class WXBizMsgCrypt:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str) -> None:
        self.token = token
        self.receive_id = receive_id
        try:
            key = base64.b64decode(encoding_aes_key + "=")
        except Exception as e:  # noqa: BLE001
            raise WXBizMsgCryptError(-40004, f"invalid encoding aes key: {e}") from e
        if len(key) != 32:
            raise WXBizMsgCryptError(-40004, "AESKey length must be 32")
        self._aes_key = key

    def verify_url(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echo_str: str,
    ) -> str:
        """URL 验证：校验签名并解密 echostr，返回明文字符串（需原样写入 HTTP 响应体）。"""
        if _sha1_hex(self.token, timestamp, nonce, echo_str) != msg_signature:
            raise WXBizMsgCryptError(-40001, "signature verification failed")
        return self._decrypt_echo(echo_str)

    def decrypt_msg(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        post_data: str,
    ) -> str:
        """解密 POST 包体中的 <Encrypt>，返回明文 XML 字符串。"""
        root = ET.fromstring(post_data)
        enc_node = root.find("Encrypt")
        if enc_node is None or not enc_node.text:
            raise WXBizMsgCryptError(-40002, "Encrypt node missing")
        encrypt = enc_node.text
        if _sha1_hex(self.token, timestamp, nonce, encrypt) != msg_signature:
            raise WXBizMsgCryptError(-40001, "signature verification failed")
        return self._decrypt_to_xml(encrypt)

    def encrypt_reply(self, reply_xml: str, timestamp: str, nonce: str) -> str:
        """构造被动加密的 XML 响应包。"""
        encrypt_b64 = self._encrypt_raw(reply_xml.encode("utf-8"))
        sig = _sha1_hex(self.token, timestamp, nonce, encrypt_b64)
        return (
            f"<xml>"
            f"<Encrypt><![CDATA[{encrypt_b64}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{sig}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            f"</xml>"
        )

    def _decrypt_echo(self, b64_cipher: str) -> str:
        plain, rid = self._decrypt_parts(b64_cipher)
        if rid != self.receive_id:
            raise WXBizMsgCryptError(-40005, "receiveid mismatch")
        return plain.decode("utf-8")

    def _decrypt_to_xml(self, b64_cipher: str) -> str:
        plain, rid = self._decrypt_parts(b64_cipher)
        if rid != self.receive_id:
            raise WXBizMsgCryptError(-40005, "receiveid mismatch")
        return plain.decode("utf-8")

    def _decrypt_parts(self, b64_cipher: str) -> Tuple[bytes, str]:
        try:
            raw = base64.b64decode(b64_cipher)
        except Exception as e:  # noqa: BLE001
            raise WXBizMsgCryptError(-40010, f"base64 decode failed: {e}") from e
        iv = self._aes_key[:16]
        cipher = AES.new(self._aes_key, AES.MODE_CBC, iv)
        try:
            plain = _pkcs7_unpad(cipher.decrypt(raw))
        except WXBizMsgCryptError:
            raise
        except Exception as e:  # noqa: BLE001
            raise WXBizMsgCryptError(-40007, f"aes decrypt failed: {e}") from e
        if len(plain) < 20:
            raise WXBizMsgCryptError(-40008, "invalid plain buffer")
        content = plain[16:]
        xml_len = struct.unpack(">I", content[:4])[0]
        if xml_len < 0 or xml_len > len(content) - 4:
            raise WXBizMsgCryptError(-40008, "invalid xml length")
        xml_bytes = content[4 : 4 + xml_len]
        rid_bytes = content[4 + xml_len :]
        return xml_bytes, rid_bytes.decode("utf-8")

    def _encrypt_raw(self, plain: bytes) -> str:
        msg_len = len(plain)
        pkg = (
            secrets.token_bytes(16)
            + struct.pack(">I", msg_len)
            + plain
            + self.receive_id.encode("utf-8")
        )
        padded = _pkcs7_pad(pkg, 32)
        iv = self._aes_key[:16]
        aes = AES.new(self._aes_key, AES.MODE_CBC, iv)
        return base64.b64encode(aes.encrypt(padded)).decode("ascii")
