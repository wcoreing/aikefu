import pytest

from app.bailian.client import BailianError, extract_reply_text, stable_kf_user_session_key


def test_stable_kf_user_session_key_deterministic() -> None:
    a = stable_kf_user_session_key("wk1", "wm1")
    b = stable_kf_user_session_key("wk1", "wm1")
    c = stable_kf_user_session_key("wk2", "wm1")
    assert a == b
    assert a != c
    assert a.startswith("kf-")


def test_extract_reply_text_top_level() -> None:
    assert extract_reply_text({"text": "  hello  "}) == "hello"


def test_extract_reply_text_nested() -> None:
    assert extract_reply_text({"result": {"reply": "ok"}}) == "ok"


def test_extract_reply_text_missing() -> None:
    with pytest.raises(BailianError):
        extract_reply_text({})
