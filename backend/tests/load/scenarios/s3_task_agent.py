"""S3 Agent 多轮工具链（task 意图：ReAct + MCP 工具调用）。

观察重点：单请求驻留时间（agent_ms）、ReAct 轮数（iterations）、tool_call 次数。
权重参考 20%。
"""

from __future__ import annotations

from lib.corpus import task_corpus
from locust import task

from scenarios.base import AssistMindUser

_corpus = task_corpus()

# 多轮追问：第二轮依赖上文订单号（ToolAgent 从 history 回溯补填参数）
_FOLLOW_UP = ["物流到哪了？", "大概什么时候能到？", "可以帮我申请退款吗？"]


class S3TaskAgentUser(AssistMindUser):
    abstract = False
    scenario_name = "s3_task_agent"
    weight = 2
    _turn = 0

    @task(3)
    def single_turn_tool(self) -> None:
        """单轮：直接带订单号的业务工具调用。"""
        self.ask(_corpus.next(), name="s3_task_agent")

    @task(1)
    def multi_turn_tool(self) -> None:
        """多轮：首轮查订单，次轮用 history 追问（测实体回溯补填）。"""
        first = _corpus.next()
        history = [
            {"role": "user", "content": first},
            {"role": "assistant", "content": "已为您查询到该订单的信息。"},
        ]
        follow = _FOLLOW_UP[self._turn % len(_FOLLOW_UP)]
        self._turn += 1
        self.ask(follow, name="s3_task_agent:multi_turn", history=history)
