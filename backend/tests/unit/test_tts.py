"""TTS（edge-tts 语音合成）单元测试。

覆盖：
- synthesize 客户端：正常只产出 audio 块 / 空白文本零调用 / 失败 warning + 上抛
- /api/v1/tts/speak API：200 流式 audio/mpeg / 空文本与超长 422 /
  edge-tts 首块失败 503 / 未登录 401

mock 策略：mock edge_tts.Communicate（客户端层）与 app.api.tts.synthesize
（API 层），不连真实网络。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.infra import tts as tts_infra
from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)

TEST_TOKEN = create_access_token({"uid": "tester-id", "sub": "tester", "role": "admin"})
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


# ---------- synthesize 客户端 ----------


def _make_communicate(chunks: list[dict]):
    """构造 mock edge_tts.Communicate：stream() 逐块产出指定 chunks。"""

    class FakeCommunicate:
        def __init__(self, text, voice):
            self.text = text
            self.voice = voice

        async def stream(self):
            for c in chunks:
                yield c

    return FakeCommunicate


async def test_synthesize_yields_only_audio_chunks():
    """混合块（audio + WordBoundary 元数据）只产出 audio 块。"""
    chunks = [
        {"type": "audio", "data": b"\xff\xfbfake-mp3-head"},
        {"type": "WordBoundary", "offset": 0, "text": "你"},
        {"type": "audio", "data": b"rest-bytes"},
    ]
    with patch.object(tts_infra, "edge_tts") as mock_etts:
        mock_etts.Communicate = _make_communicate(chunks)
        got = [c async for c in tts_infra.synthesize("你好")]
    assert got == [b"\xff\xfbfake-mp3-head", b"rest-bytes"]


async def test_synthesize_blank_text_no_call():
    """空白文本：不发起网络调用（Communicate 不构造），产出零块。"""
    with patch.object(tts_infra, "edge_tts") as mock_etts:
        mock_etts.Communicate = _make_communicate([{"type": "audio", "data": b"x"}])
        got = [c async for c in tts_infra.synthesize("   ")]
        assert got == []
        # 空白文本提前 return，Communicate 未被构造
        mock_etts.Communicate = MagicMock()
        got = [c async for c in tts_infra.synthesize("  \n ")]
        assert got == []
    mock_etts.Communicate.assert_not_called()


async def test_synthesize_failure_warns_and_raises(caplog):
    """合成失败：logger.warning + 异常上抛（不静默）。"""

    class BoomCommunicate:
        def __init__(self, text, voice):
            pass

        async def stream(self):
            raise ConnectionError("edge 接口不可达")
            yield  # pragma: no cover

    with (
        patch.object(tts_infra, "edge_tts") as mock_etts,
        pytest.raises(ConnectionError),
    ):
        mock_etts.Communicate = BoomCommunicate
        async for _ in tts_infra.synthesize("你好"):
            pass  # pragma: no cover
    assert any("TTS" in r.message for r in caplog.records)


# ---------- /api/v1/tts/speak API ----------


def _patch_synthesize(chunks: list[bytes] | None, error: Exception | None = None):
    """patch app.api.tts.synthesize 为产出 chunks（或首块即抛 error）的生成器。"""

    async def fake_gen(text):
        if error:
            raise error
        for c in chunks or []:
            yield c

    return patch("app.api.tts.synthesize", fake_gen)


def test_speak_returns_streaming_audio():
    """正常：200 + audio/mpeg + 音频字节按序透传。"""
    with _patch_synthesize([b"head-", b"tail"]):
        resp = client.post(
            "/api/v1/tts/speak", headers=AUTH_HEADERS, json={"text": "订单已发货"}
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/mpeg")
    assert resp.content == b"head-tail"


def test_speak_blank_text_422():
    """纯空白文本（绕过 Pydantic min_length）：422。"""
    with _patch_synthesize([b"x"]):
        resp = client.post("/api/v1/tts/speak", headers=AUTH_HEADERS, json={"text": "   "})
    assert resp.status_code == 422


def test_speak_empty_text_422():
    """空字符串：Pydantic 校验 422。"""
    resp = client.post("/api/v1/tts/speak", headers=AUTH_HEADERS, json={"text": ""})
    assert resp.status_code == 422


def test_speak_too_long_text_422():
    """超长文本（>500 字）：422。"""
    resp = client.post(
        "/api/v1/tts/speak", headers=AUTH_HEADERS, json={"text": "字" * 501}
    )
    assert resp.status_code == 422


def test_speak_edge_tts_down_503():
    """edge-tts 首块失败：503（前端据此回落浏览器播报）。"""
    with _patch_synthesize(None, error=ConnectionError("edge 不可达")):
        resp = client.post(
            "/api/v1/tts/speak", headers=AUTH_HEADERS, json={"text": "你好"}
        )
    assert resp.status_code == 503
    assert "暂不可用" in resp.json()["detail"]


def test_speak_requires_auth():
    """未登录：401。"""
    resp = client.post("/api/v1/tts/speak", json={"text": "你好"})
    assert resp.status_code == 401
