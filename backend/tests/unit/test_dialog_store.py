"""DialogStore（服务端会话热态记忆）单元测试。

fakeredis 模拟 Redis（dev 依赖，不连真实服务）；覆盖契约：
seed 仅首写防伪造覆写 / load 读时续期 / append_round 窗口裁剪 /
Redis 不可用全降级（None/False，永不抛）/ 脏数据丢弃。
"""

import json

from fakeredis import aioredis as fake_aioredis

from app.config import get_settings
from app.core.dialog.store import DialogStore, history_key
from app.core.infra.redis import RedisClient

settings = get_settings()


def _make_store() -> tuple[DialogStore, RedisClient]:
    """构造 fakeredis 驱动的 DialogStore（decode_responses 与生产连接池一致）。"""
    rc = RedisClient()
    rc._client = fake_aioredis.FakeRedis(decode_responses=True)
    return DialogStore(redis_client=rc), rc


async def test_seed_and_load_roundtrip():
    store, _ = _make_store()
    history = [
        {"role": "user", "content": "AssistMind 有折扣吗"},
        {"role": "assistant", "content": "目前有 9 折优惠"},
    ]
    assert await store.seed("tester", "conv-1", history) is True
    loaded = await store.load("tester", "conv-1")
    assert loaded == history


async def test_seed_sets_ttl():
    store, rc = _make_store()
    await store.seed("tester", "conv-1", [{"role": "user", "content": "hi"}])
    ttl = await rc._client.ttl(history_key("tester", "conv-1"))
    assert 0 < ttl <= settings.MEMORY_TTL_SECONDS


async def test_seed_skips_when_key_exists():
    """伪造覆写防护：key 已存在时 seed 必须为 no-op。"""
    store, _ = _make_store()
    first = [{"role": "user", "content": "真实历史"}]
    assert await store.seed("tester", "conv-1", first) is True
    forged = [{"role": "user", "content": "伪造历史"}]
    assert await store.seed("tester", "conv-1", forged) is False
    assert await store.load("tester", "conv-1") == first


async def test_load_renews_ttl():
    """滑动 TTL：读时续期到 MEMORY_TTL_SECONDS。"""
    store, rc = _make_store()
    await store.seed("tester", "conv-1", [{"role": "user", "content": "hi"}])
    key = history_key("tester", "conv-1")
    await rc._client.expire(key, 10)
    await store.load("tester", "conv-1")
    assert await rc._client.ttl(key) == settings.MEMORY_TTL_SECONDS


async def test_append_round_accumulates_and_trims_to_window():
    store, _ = _make_store()
    for i in range(7):
        await store.append_round("tester", "conv-1", f"问题{i}", f"答案{i}")
    loaded = await store.load("tester", "conv-1")
    assert len(loaded) == settings.MEMORY_WINDOW
    # LTRIM 保留最近窗口：最早两轮（问题0/答案0、问题1/答案1）被挤出
    contents = [m["content"] for m in loaded]
    assert "问题0" not in contents and "答案0" not in contents
    assert contents[0] == "问题2" and contents[-1] == "答案6"


async def test_append_round_renews_ttl():
    store, rc = _make_store()
    await store.append_round("tester", "conv-1", "q", "a")
    ttl = await rc._client.ttl(history_key("tester", "conv-1"))
    assert 0 < ttl <= settings.MEMORY_TTL_SECONDS


async def test_seed_after_append_is_noop():
    """append 之后再 seed（客户端迟到回传旧历史）不得覆盖已累积的服务端记录。"""
    store, _ = _make_store()
    await store.append_round("tester", "conv-1", "问题1", "答案1")
    assert await store.seed("tester", "conv-1", [{"role": "user", "content": "旧历史"}]) is False
    loaded = await store.load("tester", "conv-1")
    assert [m["content"] for m in loaded] == ["问题1", "答案1"]


async def test_load_drops_dirty_entries():
    """脏数据（非 JSON / 字段非法）丢弃，不进推理上下文。"""
    store, rc = _make_store()
    key = history_key("tester", "conv-1")
    await rc._client.rpush(
        key,
        "not-json",
        json.dumps({"role": "system", "content": "非法角色"}),
        json.dumps({"role": "user"}),
        json.dumps({"role": "user", "content": "正常消息"}, ensure_ascii=False),
    )
    loaded = await store.load("tester", "conv-1")
    assert loaded == [{"role": "user", "content": "正常消息"}]


async def test_no_redis_degrades_silently():
    """Redis 未连接：load→None / seed→False / append→False，绝不抛异常。"""
    store = DialogStore(redis_client=RedisClient())  # 未 connect，is_connected=False
    assert await store.load("tester", "conv-1") is None
    assert await store.seed("tester", "conv-1", [{"role": "user", "content": "hi"}]) is False
    assert await store.append_round("tester", "conv-1", "q", "a") is False


async def test_empty_conversation_id_guards():
    store, _ = _make_store()
    assert await store.load("tester", "") is None
    assert await store.seed("tester", "", [{"role": "user", "content": "hi"}]) is False
    assert await store.append_round("tester", "", "q", "a") is False


async def test_empty_history_seed_is_noop():
    store, _ = _make_store()
    assert await store.seed("tester", "conv-1", None) is False
    assert await store.seed("tester", "conv-1", []) is False


async def test_isolation_by_username():
    """双键隔离：同 conversation_id 不同 username 互不可见。"""
    store, _ = _make_store()
    await store.seed("alice", "conv-1", [{"role": "user", "content": "alice 的消息"}])
    assert await store.load("bob", "conv-1") is None
    assert await store.load("alice", "conv-1") == [
        {"role": "user", "content": "alice 的消息"}
    ]


# ---------- 摘要压缩（超窗消息 LLM 压缩，load 头部注入）----------


async def test_summary_triggered_on_overflow_and_injected_on_load(monkeypatch):
    """消息超过 窗口+阈值：后台摘要写入，load 头部注入摘要消息（不占窗口槽位）。"""
    import asyncio

    store, _ = _make_store()
    prompts: list[str] = []

    async def _fake_llm(prompt, system=None, fast=False):
        prompts.append(prompt)
        assert fast is True
        return "用户咨询了早期问题，客服已解答。"

    monkeypatch.setattr("app.core.dialog.store.call_llm", _fake_llm)
    for i in range(8):  # 16 条 > 10 窗口，overflow=6 ≥ 阈值 6 → 触发
        await store.append_round("tester", "conv-s1", f"问题{i}", f"答案{i}")
    await asyncio.gather(*store._bg_tasks)

    loaded = await store.load("tester", "conv-s1")
    assert loaded[0] == {
        "role": "assistant",
        "content": "（此前对话摘要：用户咨询了早期问题，客服已解答。）",
    }
    # 摘要在 trim 之后拼接，不占窗口槽位
    assert len(loaded) == settings.MEMORY_WINDOW + 1
    # 摘要 prompt 含被挤出的最老消息
    assert "问题0" in prompts[0]


async def test_summary_not_triggered_below_threshold(monkeypatch):
    """overflow < 阈值：不触发摘要、零 LLM 调用。"""
    import asyncio

    store, _ = _make_store()

    async def _fail(prompt, system=None, fast=False):
        raise AssertionError("低于阈值不应调用 LLM")

    monkeypatch.setattr("app.core.dialog.store.call_llm", _fail)
    for i in range(7):  # 14 条，overflow=4 < 6
        await store.append_round("tester", "conv-s2", f"q{i}", f"a{i}")
    await asyncio.gather(*store._bg_tasks)
    loaded = await store.load("tester", "conv-s2")
    assert loaded is not None
    assert not loaded[0]["content"].startswith("（此前对话摘要")


async def test_summary_llm_failure_is_silent(monkeypatch):
    """LLM 不可用：append_round 仍成功、无摘要、load 正常（纯滑动窗口降级）。"""
    import asyncio

    from app.core.infra.llm_factory import LLMUnavailableError

    store, _ = _make_store()

    async def _broken(prompt, system=None, fast=False):
        raise LLMUnavailableError("down")

    monkeypatch.setattr("app.core.dialog.store.call_llm", _broken)
    for i in range(8):
        assert await store.append_round("tester", "conv-s3", f"q{i}", f"a{i}") is True
    await asyncio.gather(*store._bg_tasks)
    loaded = await store.load("tester", "conv-s3")
    assert len(loaded) == settings.MEMORY_WINDOW
    assert not loaded[0]["content"].startswith("（此前对话摘要")


async def test_summary_disabled(monkeypatch):
    """开关关闭：超窗零 LLM 调用、无摘要注入（纯滑动窗口）。"""
    import asyncio

    store, _ = _make_store()

    async def _fail(prompt, system=None, fast=False):
        raise AssertionError("关闭时不应调用 LLM")

    monkeypatch.setattr("app.core.dialog.store.call_llm", _fail)
    monkeypatch.setattr("app.core.dialog.store.settings.MEMORY_SUMMARY_ENABLED", False)
    for i in range(8):
        await store.append_round("tester", "conv-s4", f"q{i}", f"a{i}")
    await asyncio.gather(*store._bg_tasks)
    loaded = await store.load("tester", "conv-s4")
    assert not loaded[0]["content"].startswith("（此前对话摘要")


async def test_summary_merges_with_existing(monkeypatch):
    """再次触发时增量合并：prompt 要求在旧摘要基础上补充。"""
    import asyncio

    store, _ = _make_store()
    prompts: list[str] = []

    async def _fake_llm(prompt, system=None, fast=False):
        prompts.append(prompt)
        return "合并后的摘要。"

    monkeypatch.setattr("app.core.dialog.store.call_llm", _fake_llm)
    for i in range(8):  # 第一次触发
        await store.append_round("tester", "conv-s5", f"q{i}", f"a{i}")
    await asyncio.gather(*store._bg_tasks)
    for i in range(8, 12):  # +4 轮 → 18 条，overflow=8 ≥ 6 → 第二次触发
        await store.append_round("tester", "conv-s5", f"q{i}", f"a{i}")
    await asyncio.gather(*store._bg_tasks)

    assert len(prompts) == 2
    assert "已有摘要" in prompts[1]
    assert "合并后的摘要。" in prompts[1]
