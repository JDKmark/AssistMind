"""多供应商 LLM 配置解析（Settings.llm_profile）契约测试。

约定：每个供应商一组 <前缀>_API_KEY/_BASE_URL/_MODEL（前缀 = 供应商名大写），
LLM_PROVIDER 一行切换；专属组未配置时回落 DEEPSEEK_* 主配置（warning 一次），
两者皆空且非 deepseek 供应商时显式 RuntimeError（不做空 key 静默请求）。
"""

from __future__ import annotations

import logging

import pytest

from app.config import Settings


def _settings(**overrides) -> Settings:
    """构造与本地 .env 隔离的 Settings（本测试相关字段全部显式覆盖）。"""
    base: dict[str, object] = {
        "LLM_PROVIDER": "sensenova",
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_BASE_URL": "",
        "DEEPSEEK_MODEL": "",
        "SENSENOVA_API_KEY": "",
        "SENSENOVA_BASE_URL": "",
        "SENSENOVA_MODEL": "",
        "LLM_EXTRA_BODY": "",
    }
    base.update(overrides)
    return Settings(**base)


def test_llm_profile_dedicated_group_wins():
    """供应商配置了专属组时，三元组完全来自专属组，不碰 DEEPSEEK_*。"""
    s = _settings(
        SENSENOVA_API_KEY="sk-gateway",
        SENSENOVA_BASE_URL="https://gateway.example/v1",
        SENSENOVA_MODEL="deepseek-v4-flash",
        DEEPSEEK_API_KEY="sk-other",
        DEEPSEEK_BASE_URL="https://other.example",
        DEEPSEEK_MODEL="other-model",
    )
    assert s.llm_profile() == ("sk-gateway", "https://gateway.example/v1", "deepseek-v4-flash")


def test_llm_profile_falls_back_to_deepseek_when_group_missing(caplog):
    """专属组缺 key 时回落 DEEPSEEK_* 主配置，且 warning 只发一次。"""
    Settings._llm_fallback_warned.clear()
    s = _settings(
        DEEPSEEK_API_KEY="sk-legacy",
        DEEPSEEK_BASE_URL="https://legacy.example",
        DEEPSEEK_MODEL="legacy-model",
    )
    with caplog.at_level(logging.WARNING, logger="app.config"):
        assert s.llm_profile() == ("sk-legacy", "https://legacy.example", "legacy-model")
        assert s.llm_profile() == ("sk-legacy", "https://legacy.example", "legacy-model")
    warnings = [r for r in caplog.records if "回落 DEEPSEEK_" in r.message]
    assert len(warnings) == 1  # 同一供应商只告警一次，不随调用次数刷屏


def test_llm_profile_deepseek_uses_main_config():
    """provider=deepseek 直接使用 DEEPSEEK_* 主配置（即其专属组）。"""
    s = _settings(
        LLM_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="sk-official",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_MODEL="deepseek-chat",
    )
    assert s.llm_profile() == ("sk-official", "https://api.deepseek.com", "deepseek-chat")


def test_llm_profile_partial_group_fills_from_legacy():
    """专属组只配了 key、缺 base_url/model 时，缺项从 DEEPSEEK_* 补齐。"""
    s = _settings(
        SENSENOVA_API_KEY="sk-gateway",
        DEEPSEEK_BASE_URL="https://legacy.example",
        DEEPSEEK_MODEL="legacy-model",
    )
    assert s.llm_profile() == ("sk-gateway", "https://legacy.example", "legacy-model")


def test_llm_profile_missing_key_raises_for_non_deepseek():
    """非 deepseek 供应商且专属组与回落配置皆空 → 显式 RuntimeError。"""
    s = _settings()
    with pytest.raises(RuntimeError, match="SENSENOVA_API_KEY"):
        s.llm_profile()


def test_llm_profile_deepseek_empty_key_keeps_legacy_behavior():
    """deepseek 空 key 不在解析层报错（启动期 validate_security 负责该校验）。"""
    s = _settings(LLM_PROVIDER="deepseek")
    assert s.llm_profile() == ("", "", "")


def test_build_chat_model_consumes_provider_profile(monkeypatch):
    """llm_factory 构造 ChatOpenAI 时消费 llm_profile()（供应商组生效）。"""
    from app.core.infra import llm_factory as lf

    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(lf, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(lf.settings, "LLM_PROVIDER", "sensenova")
    monkeypatch.setattr(lf.settings, "SENSENOVA_API_KEY", "sk-gateway")
    monkeypatch.setattr(lf.settings, "SENSENOVA_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(lf.settings, "SENSENOVA_MODEL", "deepseek-v4-flash")

    lf._build_chat_model("deepseek")
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["api_key"] == "sk-gateway"
    assert captured["base_url"] == "https://gateway.example/v1"


# ---------- 网关透传参数（LLM_EXTRA_BODY）----------


def test_llm_extra_body_parsed_as_dict():
    """合法 JSON 对象 → dict，供 _build_chat_model 透传给网关。"""
    s = _settings(LLM_EXTRA_BODY='{"thinking": {"type": "disabled"}}')
    assert s.llm_extra_body() == {"thinking": {"type": "disabled"}}


def test_llm_extra_body_empty_returns_empty_dict():
    """未配置（默认）时不传任何透传参数，行为与改动前一致。"""
    assert _settings(LLM_EXTRA_BODY="").llm_extra_body() == {}
    assert _settings(LLM_EXTRA_BODY="   ").llm_extra_body() == {}


def test_llm_extra_body_invalid_json_warns_once_and_degrades(caplog):
    """非法 JSON：不抛异常（可选调优项不能阻断启动），只 warning 一次。"""
    Settings._llm_fallback_warned.clear()
    s = _settings(LLM_EXTRA_BODY="{thinking: disabled}")
    with caplog.at_level(logging.WARNING, logger="app.config"):
        assert s.llm_extra_body() == {}
        assert s.llm_extra_body() == {}
    warnings = [r for r in caplog.records if "LLM_EXTRA_BODY" in r.message]
    assert len(warnings) == 1


def test_llm_extra_body_non_object_returns_empty():
    """JSON 合法但不是对象（数组/字符串）→ 忽略，避免污染请求体。"""
    assert _settings(LLM_EXTRA_BODY="[1, 2]").llm_extra_body() == {}
    assert _settings(LLM_EXTRA_BODY='"disabled"').llm_extra_body() == {}


def test_build_chat_model_passes_extra_body_when_configured(monkeypatch):
    """配置了 LLM_EXTRA_BODY 时透传给 ChatOpenAI（关闭推理模型的思考阶段）。"""
    from app.core.infra import llm_factory as lf

    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(lf, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(lf.settings, "LLM_PROVIDER", "sensenova")
    monkeypatch.setattr(lf.settings, "SENSENOVA_API_KEY", "sk-gateway")
    monkeypatch.setattr(lf.settings, "LLM_EXTRA_BODY", '{"thinking": {"type": "disabled"}}')

    lf._build_chat_model("sensenova")
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_build_chat_model_omits_extra_body_when_unset(monkeypatch):
    """未配置时不带 extra_body 键（不向网关塞空对象）。"""
    from app.core.infra import llm_factory as lf

    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(lf, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(lf.settings, "LLM_PROVIDER", "sensenova")
    monkeypatch.setattr(lf.settings, "SENSENOVA_API_KEY", "sk-gateway")
    monkeypatch.setattr(lf.settings, "LLM_EXTRA_BODY", "")

    lf._build_chat_model("sensenova")
    assert "extra_body" not in captured


# ---------- 429 语义区分：额度耗尽不重试 ----------


def _rate_limit_error(body: dict):
    """构造带 body 的 openai.RateLimitError（模拟网关 429 响应）。"""
    import httpx
    import openai

    req = httpx.Request("POST", "https://gateway.example/v1/chat/completions")
    resp = httpx.Response(429, request=req, json={"error": body})
    return openai.RateLimitError("Error code: 429", response=resp, body={"error": body})


def test_quota_exhausted_429_is_not_retried():
    """额度耗尽（insufficient_quota / rpm exhausted）重试无意义 → 不重试。"""
    from app.core.infra import llm_factory as lf

    for body in (
        {"message": "inference exceeds tpm/rpm limit", "code": "insufficient_quota"},
        {"message": "rpm exhausted", "code": "8"},
        {"message": "Insufficient Balance"},
    ):
        assert lf._is_quota_exhausted(_rate_limit_error(body)) is True
        assert lf._should_retry(_rate_limit_error(body)) is False


def test_transient_429_still_retried():
    """瞬时限流（每分钟窗口打满）等几秒能恢复 → 仍然重试。"""
    from app.core.infra import llm_factory as lf

    exc = _rate_limit_error({"message": "rate limit reached, please retry later"})
    assert lf._is_quota_exhausted(exc) is False
    assert lf._should_retry(exc) is True


def test_timeout_and_connection_errors_still_retried():
    """超时/连接错误保持原有重试语义（不受额度判定影响）。"""
    import openai

    from app.core.infra import llm_factory as lf

    assert lf._should_retry(openai.APITimeoutError(request=None)) is True
    assert lf._should_retry(TimeoutError()) is True
