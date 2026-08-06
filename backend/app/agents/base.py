"""BaseReActAgent：LangGraph StateGraph 驱动的 ReAct Agent + Loop Breaker。

ReAct 循环：Thought → Action → Observation → ... → Final Answer
Loop Breaker：迭代达 MAX_ITERATIONS 中断，返回降级答案。
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, StateGraph, add_messages

from app.config import get_settings
from app.core.infra.llm_factory import LLMUnavailableError, call_llm

logger = logging.getLogger(__name__)
settings = get_settings()


class AgentState(TypedDict, total=False):
    """Agent 状态。node 函数返回 dict 更新此状态。"""

    messages: Annotated[list, add_messages]
    iterations: int
    final_answer: str | None
    tool_calls: list[dict]
    pending_action: dict | None
    degraded: bool


class BaseReActAgent:
    """ReAct Agent 基类。

    子类需实现：
    - get_tools() -> list：返回可用工具列表
    - parse_action(llm_output) -> dict：解析 LLM 输出为 {action, input}
    - format_tools_prompt() -> str：格式化工具描述给 LLM
    - execute_tool(name, input_data) -> Any：执行工具调用

    基类提供上述方法的默认实现，子类按需覆盖。
    """

    def __init__(self, system_prompt: str = "你是一个智能助手。") -> None:
        self.system_prompt = system_prompt
        self.max_iterations = settings.MAX_ITERATIONS

    def get_tools(self) -> list[dict]:
        """子类覆盖：返回工具列表。"""
        return []

    def format_tools_prompt(self) -> str:
        """格式化工具描述给 LLM。"""
        tools = self.get_tools()
        if not tools:
            return "（无可用工具）"
        lines = []
        for t in tools:
            schema = t.get("input_schema", {})
            lines.append(f"- {t['name']}: {t.get('description', '')} 参数: {schema}")
        return "\n".join(lines)

    def parse_action(self, llm_output: str) -> dict:
        """解析 LLM 输出。

        Returns:
            {"type": "final", "answer": str} 或
            {"type": "action", "name": str, "input": dict}
        """
        text = llm_output.strip()
        if "Final Answer:" in text:
            answer = text.split("Final Answer:", 1)[1].strip()
            return {"type": "final", "answer": answer}
        if "Action:" in text:
            name: str | None = None
            input_str = ""
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("Action:"):
                    name = line[len("Action:"):].strip()
                elif line.startswith("Action Input:"):
                    input_str = line[len("Action Input:"):].strip()
            if name:
                try:
                    input_data = json.loads(input_str) if input_str else {}
                except json.JSONDecodeError:
                    logger.warning("[Agent] Action Input JSON 解析失败: %s", input_str)
                    input_data = {"raw": input_str}
                return {"type": "action", "name": name, "input": input_data}
        # 默认：将整段输出视为最终答案
        return {"type": "final", "answer": text}

    async def execute_tool(self, name: str, input_data: dict) -> Any:
        """执行工具调用。子类覆盖以提供具体执行逻辑。

        基类默认实现：在 get_tools() 中查找工具名，返回未实现占位结果。
        """
        tools = self.get_tools()
        for t in tools:
            if t.get("name") == name:
                return {"result": f"工具 {name} 未提供执行逻辑"}
        return {"error": f"未知工具: {name}"}

    def _format_messages(self, messages: list) -> str:
        """将消息列表格式化为字符串供 LLM 阅读。"""
        lines = []
        for m in messages:
            role = type(m).__name__.replace("Message", "")
            content = m.content if hasattr(m, "content") else str(m)
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _extract_query(self, messages: list) -> str:
        """从消息列表中提取用户当前问题（最后一条 HumanMessage）。

        单轮对话时仅有一条 HumanMessage；多轮对话（history 注入）时
        当前问题总是追加在最后，取最后一条而非第一条，保证检索与
        LLM 提示中的「用户问题」是当前轮次的输入。
        """
        query = ""
        for m in messages:
            if isinstance(m, HumanMessage):
                query = m.content if hasattr(m, "content") else str(m)
        return query

    async def think(self, state: AgentState) -> dict:
        """思考节点：调用 LLM 决定下一步动作。

        返回 dict 更新 state（不返回 None，遵循 LangGraph 约束）。
        """
        iterations = state.get("iterations", 0) + 1
        messages = state.get("messages", [])
        query = self._extract_query(messages)
        prompt = (
            f"你可以使用以下工具：\n{self.format_tools_prompt()}\n\n"
            f"已有对话：\n{self._format_messages(messages)}\n\n"
            f"用户问题：{query}\n\n"
            "请输出：\n"
            "- 如果需要调用工具：Action: <工具名>\nAction Input: <JSON参数>\n"
            "- 如果已有答案：Final Answer: <答案>\n"
        )
        llm_output = await call_llm(prompt, system=self.system_prompt)
        parsed = self.parse_action(llm_output)
        new_messages = [AIMessage(content=llm_output)]
        if parsed["type"] == "final":
            return {
                "iterations": iterations,
                "final_answer": parsed["answer"],
                "pending_action": None,
                "messages": new_messages,
            }
        return {
            "iterations": iterations,
            "pending_action": {"name": parsed["name"], "input": parsed["input"]},
            "messages": new_messages,
        }

    async def act(self, state: AgentState) -> dict:
        """行动节点：执行 think 决定的工具调用。

        返回 dict 更新 state。工具异常被捕获，作为 Observation 继续循环。
        """
        pending = state.get("pending_action")
        tool_calls = list(state.get("tool_calls", []))
        if pending is None:
            return {}
        name = pending["name"]
        input_data = pending.get("input", {}) or {}
        try:
            result = await self.execute_tool(name, input_data)
            observation = f"Observation: {result}"
        except Exception as e:
            logger.warning("[Agent] 工具 %s 执行失败: %s", name, e)
            observation = f"Observation: 工具 {name} 执行失败: {e}"
            result = {"error": str(e)}
        tool_calls.append({"name": name, "input": input_data, "result": result})
        return {
            "messages": [
                ToolMessage(
                    content=observation,
                    tool_call_id=f"{name}_{len(tool_calls)}",
                )
            ],
            "tool_calls": tool_calls,
            "pending_action": None,
        }

    def should_continue(self, state: AgentState) -> str:
        """条件边：判断是否继续循环还是结束。

        返回 "continue" | "end"
        - final_answer 不为 None → "end"
        - iterations >= max_iterations → "end"（Loop Breaker）
        - 否则 → "continue"
        """
        if state.get("final_answer") is not None:
            return "end"
        if state.get("iterations", 0) >= self.max_iterations:
            return "end"
        return "continue"

    def build_graph(self):
        """构建 LangGraph StateGraph。"""
        graph = StateGraph(AgentState)
        graph.add_node("think", self.think)
        graph.add_node("act", self.act)

        graph.set_entry_point("think")
        graph.add_conditional_edges(
            "think",
            self.should_continue,
            {"continue": "act", "end": END},
        )
        graph.add_edge("act", "think")  # act 后回到 think

        return graph.compile()

    async def run(
        self, query: str, history: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """运行 Agent。

        Args:
            query: 用户当前输入
            history: 多轮对话历史（可选），按时间正序，每项
                {"role": "user" | "assistant", "content": str}；
                缺省 None 时为单轮对话（行为与原来完全一致）。

        Returns:
            {answer, tool_calls, iterations, degraded}
        """
        messages: list = []
        for h in history or []:
            content = h.get("content", "")
            if h.get("role") == "assistant":
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))
        messages.append(HumanMessage(content=query))

        initial_state: AgentState = {
            "messages": messages,
            "iterations": 0,
            "final_answer": None,
            "tool_calls": [],
            "pending_action": None,
            "degraded": False,
        }

        # recursion_limit 留足余量，确保 Loop Breaker 先于 LangGraph 递归限制触发
        recursion_limit = max(self.max_iterations * 4, 25)

        try:
            graph = self.build_graph()
            final_state = await graph.ainvoke(
                initial_state,
                config={"recursion_limit": recursion_limit},
            )
            answer = final_state.get("final_answer")
            if answer is None:
                # Loop Breaker 触发或未给出答案
                answer = "抱歉，我暂时无法处理您的请求，请稍后重试或转人工客服。"
                return {
                    "answer": answer,
                    "tool_calls": final_state.get("tool_calls", []),
                    "iterations": final_state.get("iterations", 0),
                    "degraded": True,
                }
            return {
                "answer": answer,
                "tool_calls": final_state.get("tool_calls", []),
                "iterations": final_state.get("iterations", 0),
                "degraded": final_state.get("degraded", False),
            }
        except LLMUnavailableError:
            logger.warning("[Agent] LLM 不可用，降级返回")
            return {
                "answer": "抱歉，服务暂时繁忙，请稍后重试或转人工客服。",
                "tool_calls": [],
                "iterations": 0,
                "degraded": True,
            }
        except Exception as e:
            logger.warning("[Agent] 运行异常: %s", e)
            return {
                "answer": f"处理过程中出现错误，请重试。错误：{e}",
                "tool_calls": [],
                "iterations": 0,
                "degraded": True,
            }
