from app.services.group_broadcast import apply_broadcast_template


def test_apply_broadcast_template() -> None:
    s = apply_broadcast_template(
        "Hi {name} uid={user_id} nick={nickname}",
        user_id="wm1",
        name="张三",
        nickname="小张",
    )
    assert "wm1" in s
    assert "张三" in s
    assert "小张" in s
