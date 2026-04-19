"""FastAPI 入口。"""

from __future__ import annotations

import base64
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routes.internal_workflows import router as internal_workflows_router
from app.routes.wecom_callback import router as wecom_router

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


def _mask(s: str, keep: int = 4) -> str:
    if not s:
        return ""
    if len(s) <= keep:
        return "*" * len(s)
    return f"{s[:keep]}****"


def _aes_key_len(enc_key: str) -> int | None:
    if not enc_key:
        return None
    try:
        return len(base64.b64decode(enc_key + "="))
    except Exception:  # noqa: BLE001
        return -1


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info(
        "config loaded: wecom_token=%s wecom_corp_id=%s wecom_encoding_aes_key_len=%s wecom_aes_decoded_len=%s bailian_app_id=%s bailian_mode=%s redis=%s",
        bool(settings.wecom_token),
        _mask(settings.wecom_corp_id),
        len(settings.wecom_encoding_aes_key) if settings.wecom_encoding_aes_key else 0,
        _aes_key_len(settings.wecom_encoding_aes_key),
        _mask(settings.bailian_app_id),
        settings.bailian_invoke_mode,
        bool(settings.redis_url),
    )
    if (settings.internal_api_token or "").strip():
        logger.info(
            "internal API enabled: group_workflow=%s",
            bool(settings.bailian_group_app_id),
        )
    yield


app = FastAPI(title="qiwei-server", version="0.1.0", lifespan=_lifespan)
app.include_router(wecom_router, prefix="/wecom")
if (settings.internal_api_token or "").strip():
    app.include_router(internal_workflows_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    s = get_settings()
    lvl = (s.log_level or "info").lower()
    if lvl not in ("critical", "error", "warning", "info", "debug", "trace"):
        lvl = "info"
    uvicorn.run(app, host=s.host, port=s.port, log_level=lvl)


if __name__ == "__main__":
    main()
