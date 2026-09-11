"""语音播报路由：edge-tts 流式合成（登录用户可用）。

前端 Chat 页「语音播报」开关的云端音色后端：POST /api/v1/tts/speak
返回 audio/mpeg 流。edge-tts 失败返回 503，前端回落浏览器 speechSynthesis。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.infra.tts import synthesize

logger = logging.getLogger(__name__)
router = APIRouter()

# 单条播报文本上限：客服回答播报足够，防长文本刷免费合成接口
_TTS_TEXT_MAX_LEN = 500


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_TTS_TEXT_MAX_LEN, description="待合成文本")


@router.post("/speak")
async def speak(
    req: TTSRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> StreamingResponse:
    """流式合成语音（MP3）。edge-tts 不可用时 503，前端降级浏览器播报。

    首块预取：连接/首块失败在响应开始前转 503（状态码还能改）；
    中途断流只能截断音频（此时状态码已发出），记 warning 不静默。
    """
    text = req.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="文本不能为空",
        )

    gen = synthesize(text)
    try:
        first_chunk = await gen.__anext__()
    except StopAsyncIteration:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="文本不能为空",
        ) from None
    except Exception:
        logger.warning(
            "[TTS] /speak 首块合成失败 user=%s text_len=%d", user.get("username"), len(text)
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="语音合成服务暂不可用",
        ) from None

    async def stream() -> AsyncIterator[bytes]:
        yield first_chunk
        async for chunk in gen:
            yield chunk
        # 中途断流由 synthesize 内 logger.warning 兜底（状态码已发出，仅截断）

    return StreamingResponse(stream(), media_type="audio/mpeg")
