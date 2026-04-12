"""解析企微明文 XML。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional


def _text(root: ET.Element, name: str) -> Optional[str]:
    n = root.find(name)
    return n.text if n is not None and n.text is not None else None


@dataclass
class KfMsgOrEvent:
    token: str
    open_kfid: str
    to_user_name: Optional[str] = None
    create_time: Optional[str] = None


def parse_plain_xml(xml_str: str) -> KfMsgOrEvent | None:
    root = ET.fromstring(xml_str)
    msg_type = _text(root, "MsgType")
    if msg_type != "event":
        return None
    event = _text(root, "Event")
    if event != "kf_msg_or_event":
        return None
    token = _text(root, "Token")
    open_kfid = _text(root, "OpenKfId")
    if not token or not open_kfid:
        return None
    return KfMsgOrEvent(
        token=token,
        open_kfid=open_kfid,
        to_user_name=_text(root, "ToUserName"),
        create_time=_text(root, "CreateTime"),
    )
