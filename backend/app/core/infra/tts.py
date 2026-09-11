"""TTS 客户端：edge-tts 云端神经音色合成（免费无 key）。

设计：
- 流式合成：Communicate.stream() 逐块产出 MP3 音频，API 层 StreamingResponse
  透传，首字节延迟低（无需等全量合成）。
- 失败降级：网络/接口异常向上抛（API 层转 503），本层不静默——前端收到 503
  后回落浏览器 speechSynthesis（两级降级的前一级）。
- edge-tts 是微软 Edge 接口的逆向封装（无官方 SLA），浏览器兜底必须保留。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import edge_tts

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def synthesize(text: str) -> AsyncIterator[bytes]:
    """流式合成语音，逐块产出 MP3 音频 bytes。

    空白文本直接 return（不发起网络调用）；调用失败向上抛异常（含 logger.warning）。
    """
    text = (text or "").strip()
    if not text:
        return
    try:
        communicate = edge_tts.Communicate(text, voice=settings.TTS_VOICE)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
    except Exception as e:
        logger.warning("[TTS] edge-tts 合成失败（前端将回落浏览器播报）: %s", e)
        raise
