"""内部 HTTP：触发百炼群发工作流（需 INTERNAL_API_TOKEN）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.bailian.client import BailianAppClient, BailianError
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


class GroupBroadcastBody(BaseModel):
    tag: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


@router.post("/workflows/group-broadcast")
async def group_broadcast_workflow(
    body: GroupBroadcastBody,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict:
    s = get_settings()
    if not (s.internal_api_token or "").strip():
        raise HTTPException(status_code=503, detail="INTERNAL_API_TOKEN 未配置")
    if (x_internal_token or "").strip() != s.internal_api_token.strip():
        raise HTTPException(status_code=401, detail="invalid X-Internal-Token")
    if not (s.bailian_group_app_id or "").strip():
        raise HTTPException(status_code=503, detail="BAILIAN_GROUP_APP_ID 未配置")
    try:
        text = await BailianAppClient(s).invoke_group_workflow(
            tag=body.tag.strip(),
            content=body.content,
        )
    except BailianError as e:
        logger.exception("群发工作流调用失败")
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"text": text}
