"""FastAPI 入口。"""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI

from app.config import get_settings
from app.routes.wecom_callback import router as wecom_router

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)

app = FastAPI(title="qiwei-server", version="0.1.0")
app.include_router(wecom_router, prefix="/wecom")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
