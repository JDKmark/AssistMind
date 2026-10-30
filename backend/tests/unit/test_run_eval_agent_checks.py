"""run_eval_agent.py 断言与报告结构单元测试。

守住两件事：

1. 转人工 / 建单任务的断言必须同时校验「工具调用」与「工单号回显」。
   只检查话术会漏掉两类真实失败：
   - 口头答应转人工但没调 transfer_human（说要做、没做）
   - 工具调用成功但答案没带单号（底层成功、用户拿不到凭证）
2. `build_report` 产物必须可 JSON 序列化（task 里的 check 是函数，不能进报告），
   通过率计算正确——它是跨版本对比的唯一依据。

全部用例离线运行，不启动 MCP Server、不调用 LLM。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_eval_agent as agent_eval  # noqa: E402

TICKET_ID = "TK-202610311030001234567"


def _turn(answer: str = "", tool_calls: list | None = None) -> dict:
    return {
        "user": "q",
        "answer": answer,
        "tool_calls": tool_calls or [],
        "iterations": 1,
        "degraded": False,
    }


def test_transfer_human_rejects_answer_without_tool_call():
    """话术说「已转人工」但没调工具：必须判失败。

    这是「说要做、没做」的典型形态，只看回答关键词会放过。
    """
    ok, reason = agent_eval.check_transfer_human({}, _turn("已为您转接人工客服，请稍候"))

    assert not ok
    assert "未调用 transfer_human" in reason


def test_transfer_human_rejects_missing_ticket_echo():
    """调了工具但答案没带工单号：用户拿不到凭证，仍判失败。"""
    calls = [{"name": "transfer_human", "input": {}, "result": {"ticket_id": TICKET_ID}}]

    ok, reason = agent_eval.check_transfer_human({}, _turn("已为您转接人工客服", calls))

    assert not ok
    assert "未回显工单号" in reason


def test_transfer_human_passes_with_call_and_echo():
    calls = [{"name": "transfer_human", "input": {}, "result": {"ticket_id": TICKET_ID}}]

    ok, reason = agent_eval.check_transfer_human({}, _turn(f"已转人工，工单号 {TICKET_ID}", calls))

    assert ok, reason
    assert TICKET_ID in reason


def test_create_ticket_rejects_missing_required_args():
    """create_ticket 缺 title/description：即便返回了单号也判失败。"""
    calls = [{"name": "create_ticket", "input": {"title": ""}, "result": {"ticket_id": TICKET_ID}}]

    ok, reason = agent_eval.check_create_ticket({}, _turn(f"已建单 {TICKET_ID}", calls))

    assert not ok
    assert "缺必填参数" in reason


def test_create_ticket_rejects_missing_ticket_id_in_result():
    """工具返回里没有单号：无法确认建单成功。"""
    calls = [
        {
            "name": "create_ticket",
            "input": {"title": "投诉", "description": "商品质量问题"},
            "result": {"error": "boom"},
        }
    ]

    ok, reason = agent_eval.check_create_ticket({}, _turn("已为您创建工单", calls))

    assert not ok
    assert "未返回工单号" in reason


def test_create_ticket_passes_with_args_and_echo():
    calls = [
        {
            "name": "create_ticket",
            "input": {"title": "投诉", "description": "商品质量问题"},
            "result": {"ticket_id": TICKET_ID},
        }
    ]

    ok, reason = agent_eval.check_create_ticket({}, _turn(f"已建单，工单号：{TICKET_ID}", calls))

    assert ok, reason


def test_ticket_id_extracted_from_string_result():
    """结果不是 dict（MCP 返回字符串）时用正则兜底取号。"""
    tc = {"name": "create_ticket", "input": {}, "result": f"工单创建成功，单号 {TICKET_ID}"}

    assert agent_eval._ticket_id_of(tc) == TICKET_ID
    assert agent_eval._ticket_id_of(None) == ""


def test_build_report_is_json_serializable_and_counts_rate():
    """报告可落盘：task 里的 check 函数必须被剔除，通过率按任务数计算。"""
    results = [
        {
            "task": {"name": "查订单", "check": lambda ctx, turn: (True, "ok")},
            "turns": [
                {
                    "user": "查订单",
                    "answer": "已发货",
                    "tool_calls": [{"name": "query_order", "input": {}, "result": {}}],
                    "iterations": 2,
                    "degraded": False,
                }
            ],
            "checks": [{"passed": True, "reason": "ok"}],
            "ok": True,
        },
        {
            "task": {"name": "退款", "check": lambda ctx, turn: (False, "no")},
            "turns": [{"user": "退款", "answer": "", "tool_calls": [], "iterations": 1}],
            "checks": [{"passed": False, "reason": "未调用 apply_refund"}],
            "ok": False,
        },
    ]

    report = agent_eval.build_report(results, "实体识别 ON")

    assert report["passed"] == 1
    assert report["total"] == 2
    assert report["rate"] == 0.5
    assert report["tasks"][0]["tools_called"] == [["query_order"]]
    assert report["tasks"][1]["failed_checks"] == [{"turn": 1, "reason": "未调用 apply_refund"}]
    # 必须可序列化：含函数会导致 write_report 抛 TypeError
    json.dumps(report, ensure_ascii=False)


def test_tasks_include_transfer_and_ticket_coverage():
    """任务清单必须覆盖转人工与建单——这两条是客服闭环的关键动作。"""
    names = " ".join(t["name"] for t in agent_eval.TASKS)

    assert "transfer_human" in names
    assert "create_ticket" in names
