from app.wecom.xml_parse import parse_plain_xml


def test_parse_kf_msg_or_event() -> None:
    xml = """<xml>
<ToUserName><![CDATA[ww123]]></ToUserName>
<CreateTime>1348831860</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[kf_msg_or_event]]></Event>
<Token><![CDATA[ENCtoken123]]></Token>
<OpenKfId><![CDATA[wkxxxx]]></OpenKfId>
</xml>"""
    ev = parse_plain_xml(xml)
    assert ev is not None
    assert ev.token == "ENCtoken123"
    assert ev.open_kfid == "wkxxxx"
