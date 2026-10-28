"""run_eval.py 判分完整性（P0-1/P0-2）单元测试。

背景：RAGAS 抽检（2026-09-13）发现报告只写摘要、回答被截断到 60 字，且
5/35 行 faithfulness 缺失而无人知晓。本测试守住三件事：
1. --dump-json 落全量明细（完整回答 / 检索片段 / 缺失记 null / 失败原因）
2. 判分缺失行逐条重试，重试无效则在报告里列出行号与原因
3. 重试返回行数不匹配时拒绝合并，绝不把 A 行的分数写到 B 行

全部用例离线运行：aevaluate 与数据集构造被替换为假实现，不调用 LLM。
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
import types
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_eval  # noqa: E402

NAN = float("nan")
METRIC_NAMES = ["faithfulness", "context_precision", "context_recall", "answer_relevancy"]


class _FakeMetric:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeResult:
    def __init__(self, scores: list[dict]) -> None:
        self.scores = scores


def _metrics() -> list[_FakeMetric]:
    return [_FakeMetric(n) for n in METRIC_NAMES[:3]]


def _nan_row() -> dict:
    return {"faithfulness": NAN, "context_precision": NAN, "context_recall": NAN}


def _rows(n: int) -> list[dict]:
    return [{"question": f"q{i}", "adversarial": False} for i in range(n)]


def _install_fake_evaluate(monkeypatch, handler) -> None:
    """替换 ragas 的 aevaluate 与 EvaluationDataset（不联网、不导入 ragas）。"""

    async def fake_aevaluate(dataset=None, **kwargs):
        return handler(dataset)

    monkeypatch.setattr(run_eval, "aevaluate", fake_aevaluate)
    monkeypatch.setattr(
        run_eval,
        "EvaluationDataset",
        lambda samples: types.SimpleNamespace(samples=list(samples)),
    )


# ---------------------------------------------------------------- 参数解析


def test_parse_args_defaults():
    dataset, dump_path, retries, max_tokens = run_eval.parse_args([])
    assert dataset.endswith("eval_mall_qa.json")
    assert dump_path is None
    assert retries == 2
    assert max_tokens == 8192


def test_parse_args_flags_and_positional():
    dataset, dump_path, retries, max_tokens = run_eval.parse_args(
        ["my.json", "--dump-json", "out.json", "--judge-retries", "3", "--judge-max-tokens", "12288"]
    )
    assert (dataset, dump_path, retries, max_tokens) == ("my.json", "out.json", 3, 12288)


def test_parse_args_equals_form_and_zero():
    _, dump_path, retries, _ = run_eval.parse_args(["--dump-json=o.json", "--judge-retries=0"])
    assert dump_path == "o.json"
    assert retries == 0


def test_parse_args_negative_retries_clamped(caplog):
    _, _, retries, _ = run_eval.parse_args(["--judge-retries", "-5"])
    assert retries == 0


def test_parse_args_tiny_max_tokens_bumped():
    _, _, _, max_tokens = run_eval.parse_args(["--judge-max-tokens", "100"])
    assert max_tokens == 8192


# ---------------------------------------------------------------- judge 调用旁路记录


def test_capture_records_sync_call(monkeypatch):
    class _Msg:
        content = '{"statements": ["a"]}' + "x" * 100

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Usage:
        prompt_tokens = 1234
        completion_tokens = 99

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()

    class _Inner:
        def create(self, **kwargs):
            return _Resp()

    sink: list[dict] = []
    comp = run_eval._CaptureCompletions(_Inner(), sink, "sync")
    resp = comp.create(model="m", max_tokens=8192, messages=[{"content": "问题" * 100}])

    assert resp is not None and len(sink) == 1
    entry = sink[0]
    assert entry["finish_reason"] == "stop"
    assert entry["label"] == "sync"
    assert entry["max_tokens"] == 8192
    assert entry["prompt_tokens"] == 1234
    assert len(entry["output_tail"]) <= 80


def test_capture_records_async_call(monkeypatch):
    class _Msg:
        content = '{"truncat'

    class _Choice:
        finish_reason = "length"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    class _Inner:
        def create(self, **kwargs):
            async def _call():
                return _Resp()

            return _call()

    sink: list[dict] = []
    comp = run_eval._CaptureCompletions(_Inner(), sink, "async")
    resp = asyncio.run(comp.create(model="m", messages=[]))
    assert resp is not None
    assert sink[0]["finish_reason"] == "length"
    assert sink[0]["output_tail"].endswith('{"truncat')


def test_capture_records_error_and_reraises():
    class _Inner:
        def create(self, **kwargs):
            raise RuntimeError("boom")

    sink: list[dict] = []
    comp = run_eval._CaptureCompletions(_Inner(), sink, "sync")
    try:
        comp.create(model="m", messages=[])
        raised = False
    except RuntimeError:
        raised = True
    assert raised and "RuntimeError: boom" in sink[0]["error"]


def test_judge_calls_summary_counts(monkeypatch):
    monkeypatch.setattr(run_eval, "_JUDGE_CALL_SINK", [
        {"finish_reason": "stop"},
        {"finish_reason": "length"},
        {"error": "IncompleteOutputException: ..."},
    ])
    summary = run_eval._judge_calls_summary()
    assert "共 3 次调用" in summary and "length" in summary and "IncompleteOutputException" in summary


def test_capture_client_passthrough():
    class _InnerCompletions:
        pass

    class _InnerChat:
        def __init__(self):
            self.completions = _InnerCompletions()

    class _Inner:
        attr = 42

        def __init__(self):
            self.chat = _InnerChat()

        def hello(self):
            return "world"

    client = run_eval._CaptureClient(_Inner(), [], "sync")
    assert client.attr == 42
    assert client.hello() == "world"
    assert isinstance(client.chat, run_eval._CaptureChat)


# ---------------------------------------------------------------- 失败行收集


def test_collect_row_errors_lists_only_missing_rows():
    rows = [
        {"question": "正常行", "adversarial": False},
        {"question": "判分失败行", "adversarial": True},
        {"question": "只缺相关性", "adversarial": False},
    ]
    scores = [
        {"faithfulness": 1.0, "context_precision": 1.0, "context_recall": 1.0, "answer_relevancy": 0.9},
        {"faithfulness": NAN, "context_precision": NAN, "context_recall": NAN, "answer_relevancy": NAN},
        {"faithfulness": 0.5, "context_precision": 0.5, "context_recall": 0.5, "answer_relevancy": NAN},
    ]
    errors = run_eval._collect_row_errors(rows, scores, METRIC_NAMES, {2: "ValueError: response 为空"}, 2)

    assert [e["index"] for e in errors] == [2, 3]
    assert "faithfulness" in errors[0]["missing_metrics"]
    assert "重试 2 轮" in errors[0]["reason"]
    assert "response 为空" in errors[1]["reason"]


# ---------------------------------------------------------------- 缺失行重试


def test_retry_missing_scores_fills_gap(monkeypatch):
    calls: list[list] = []

    def handler(dataset):
        calls.append(list(dataset.samples))
        return _FakeResult(
            [{"faithfulness": 0.7, "context_precision": 0.6, "context_recall": 0.5}
             for _ in dataset.samples]
        )

    _install_fake_evaluate(monkeypatch, handler)
    result = _FakeResult([{"faithfulness": 1.0, "context_precision": 0.9, "context_recall": 0.8}, _nan_row()])

    diag = asyncio.run(
        run_eval._retry_missing_scores(_rows(2), ["s0", "s1"], _metrics(), result, None, None, 2)
    )

    assert result.scores[1]["faithfulness"] == 0.7
    assert result.scores[0]["faithfulness"] == 1.0  # 已有分数不被覆盖
    assert calls == [["s1"]]  # 只重试缺失行
    assert diag["retry_rounds"] == 1 and diag["retried_rows"] == 1


def test_retry_gives_up_after_max_rounds(monkeypatch):
    def handler(dataset):
        return _FakeResult([_nan_row() for _ in dataset.samples])

    _install_fake_evaluate(monkeypatch, handler)
    result = _FakeResult([_nan_row()])

    diag = asyncio.run(
        run_eval._retry_missing_scores(_rows(1), ["s0"], _metrics(), result, None, None, 2)
    )

    assert diag["retry_rounds"] == 2 and diag["retried_rows"] == 2
    assert all(math.isnan(v) for v in result.scores[0].values())


def test_retry_rejects_row_count_mismatch(monkeypatch):
    """行数不一致必须拒绝合并：否则 zip 会把别的行的分数写到当前行。"""

    def handler(dataset):
        return _FakeResult(
            [
                {"faithfulness": 0.9, "context_precision": 0.9, "context_recall": 0.9},
                {"faithfulness": 0.1, "context_precision": 0.1, "context_recall": 0.1},
            ]
        )

    _install_fake_evaluate(monkeypatch, handler)
    result = _FakeResult([_nan_row()])

    diag = asyncio.run(
        run_eval._retry_missing_scores(_rows(1), ["s0"], _metrics(), result, None, None, 1)
    )

    assert all(math.isnan(v) for v in result.scores[0].values())
    assert diag["retry_rounds"] == 1


# ---------------------------------------------------------------- 全量落盘


def _dump_rows() -> list[dict]:
    return [
        {
            "question": "华为 Mate 60 Pro 多少钱？",
            "adversarial": False,
            "ground_truth": "6999",
            "hit_docs": ["knowledge/mall/business/演示商品与订单数据.md"],
            "contexts_detail": [
                {"source": "knowledge/mall/business/演示商品与订单数据.md", "chars": 120, "score": 0.83}
            ],
            "contexts": ["华为 Mate 60 Pro 售价 ¥6999（256G 雅丹黑）"],
            "answer": "华为 Mate 60 Pro 的售价是 ¥6999。[1]",
            "degraded": [],
        },
        {
            "question": "对抗样本问题",
            "adversarial": True,
            "ground_truth": "应拒答",
            "hit_docs": ["knowledge/mall/business/服务承诺与话术.md"],
            "contexts_detail": [],
            "contexts": [],
            "answer": "",
            "degraded": ["rag_failed"],
        },
    ]


def test_dump_json_writes_full_records(tmp_path):
    out = tmp_path / "dump.json"
    scores = [
        {"faithfulness": 1.0, "context_precision": 0.917, "context_recall": 0.86, "answer_relevancy": 0.904},
        {"faithfulness": NAN, "context_precision": NAN, "context_recall": NAN, "answer_relevancy": NAN},
    ]
    diag = {"retry_rounds": 1, "row_errors": [{"index": 2, "missing_metrics": ["faithfulness"]}]}

    run_eval.dump_json(str(out), "eval_mall_qa.json", _dump_rows(), scores, diag)
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["sample_count"] == 2
    assert data["judge_retry_rounds"] == 1
    assert data["row_errors"][0]["index"] == 2
    # 回答不被截断（报告里原来是 60 字 + 省略号）
    assert data["records"][0]["answer"] == _dump_rows()[0]["answer"]
    # 检索片段明细（判分时到底喂了什么）与全文都保留
    assert data["records"][0]["contexts_detail"][0]["score"] == 0.83
    assert data["records"][0]["contexts_full"][0].startswith("华为")
    # 缺失分数写 null，而不是 NaN（JSON 里 NaN 非法）
    assert data["records"][1]["scores"]["faithfulness"] is None
    assert data["records"][1]["degraded"] == ["rag_failed"]


# ---------------------------------------------------------------- 报告输出


def test_print_report_lists_failed_rows(capsys):
    scores = [
        {"faithfulness": 1.0, "context_precision": 0.917, "context_recall": 0.86, "answer_relevancy": 0.904},
        {"faithfulness": NAN, "context_precision": NAN, "context_recall": NAN, "answer_relevancy": NAN},
    ]
    diag = {
        "retry_rounds": 1,
        "row_errors": [
            {
                "index": 2,
                "question": "对抗样本问题",
                "adversarial": True,
                "missing_metrics": ["faithfulness", "answer_relevancy"],
                "reason": "judge 调用失败",
            }
        ],
    }

    all_nan = run_eval.print_report(_dump_rows(), scores, diag)
    text = capsys.readouterr().out

    assert all_nan == 1
    assert "判分失败行（1 条" in text
    assert "judge 调用失败" in text
    assert "有效 1/2 行" in text


def test_print_report_announces_no_failures(capsys):
    run_eval.print_report(
        _dump_rows()[:1],
        [{"faithfulness": 1.0, "context_precision": 0.9, "context_recall": 0.8, "answer_relevancy": 0.9}],
        {"retry_rounds": 0, "row_errors": []},
    )
    assert "无失败行" in capsys.readouterr().out


# ---------------------------------------------------------------- 判分分批（限速保护）


def test_split_batches_even():
    assert run_eval._split_batches(12, 6) == [(0, 6), (6, 12)]


def test_split_batches_remainder():
    assert run_eval._split_batches(42, 8) == [(0, 8), (8, 16), (16, 24), (24, 32), (32, 40), (40, 42)]


def test_split_batches_small_total_and_bad_size():
    assert run_eval._split_batches(3, 8) == [(0, 3)]
    assert run_eval._split_batches(4, 0) == [(0, 1), (1, 2), (2, 3), (3, 4)]  # size<=0 归一为 1


def test_score_bag_holds_scores():
    bag = run_eval._ScoreBag([{"faithfulness": 1.0}])
    assert bag.scores[0]["faithfulness"] == 1.0


# ---------------------------------------------------------------- 全局限速器


def test_rate_limiter_spaces_calls():
    limiter = run_eval._GlobalRateLimiter(rpm=600)  # 0.1s 间隔
    t0 = time.monotonic()
    for _ in range(4):
        limiter.acquire()
    elapsed = time.monotonic() - t0
    assert limiter.calls == 4
    assert elapsed >= 0.29  # 3 个间隔 ≈ 0.3s（首调不等待）


def test_rate_limiter_install_patches_modules_and_limits(monkeypatch):
    from app.core.rag import engine as rag_engine

    calls: list[str] = []

    async def fake_inner(prompt, **kwargs):
        calls.append(prompt)
        return "ok"

    from app.core.infra import llm_factory as _llm

    monkeypatch.setattr(_llm, "call_llm", fake_inner)
    monkeypatch.setattr(rag_engine, "call_llm", fake_inner)

    limiter_holder: list = []
    run_eval.install_llm_rate_limit(rpm=600)
    limiter_holder.append(run_eval._LLM_LIMITER)

    result = asyncio.run(rag_engine.call_llm("问题"))
    assert result == "ok" and calls == ["问题"]  # 包装后仍能正常返回
    assert rag_engine.call_llm is not fake_inner  # 已被替换为限速版本
    assert limiter_holder[0] is not None and limiter_holder[0].calls == 1


def test_limited_client_wraps_async_create():
    class _Msg:
        content = "x"

    class _Choice:
        finish_reason = "stop"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    class _InnerCompletions:
        def create(self, **kwargs):
            async def _call():
                return _Resp()

            return _call()

    class _InnerChat:
        def __init__(self):
            self.completions = _InnerCompletions()

    class _Inner:
        def __init__(self):
            self.chat = _InnerChat()

    limiter = run_eval._GlobalRateLimiter(rpm=6000)  # 0.01s，测试跑得快
    client = run_eval._LimitedClient(_Inner(), limiter)
    resp = asyncio.run(client.chat.completions.create(model="m", messages=[]))
    assert resp is not None and limiter.calls == 1
