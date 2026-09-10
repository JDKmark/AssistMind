"""ToolAgent：通过 MCP Client 调用工具的 ReAct Agent。

设计要点（spec.md Task 6）：
- Retrieval Before Agency：非纯工单操作的问题先调 search_knowledge 检索，再决策
- 参数缺失触发澄清：LLM 输出 "CLARIFY: <追问>" 时不调工具，直接返回追问话术
- 电商业务工具：query_order / query_logistics / query_product / apply_refund
  走 MCP Server → mall 数据源（固定演示数据），先确认订单再受理退款
- 多轮对话：run(query, history=...) 注入上文，支持「物流到哪了」等依赖上下文的场景
- MCP 不可用降级：连接失败时返回降级话术，不抛异常中断
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.base import AgentState, BaseReActAgent
from app.core.dialog import extract_query
from app.core.dialog.state import extract_slots, missing_slots
from app.core.mall.entity_extractor import extract_with_llm, fill_tool_args
from app.core.mcp.client import MCPClient, get_mcp_client

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = """你是 AssistMind 电商智能客服，帮用户处理商品咨询、订单查询、物流查询与售后申请。

可用工具：
- search_knowledge(query, role)：检索知识库，回答商品、优惠、售后政策等问题
- query_order(order_sn)：查询订单信息（状态/商品明细/实付金额/物流单号）
- query_logistics(order_sn)：查询订单物流轨迹
- query_product(product_id)：查询商品信息（价格/库存/服务标识）
- apply_refund(order_sn, reason)：申请订单退款/退货（创建售后单，需要订单号与退款原因）
- create_ticket(title, description, priority)：创建售后/问题工单（title/description 必填）
- transfer_human(reason)：转人工客服
- get_ticket_status(ticket_id)：查询工单/售后处理状态

工作原则：
1. Retrieval Before Agency：对于非纯工单操作的问题，系统会先调用 search_knowledge 检索知识库，再决定是否调用其他工具。纯工单操作（创建/查询工单、转人工）可跳过检索。
2. 订单/物流/售后是实时业务数据，必须调用对应工具查询（query_order / query_logistics / apply_refund），以工具返回结果为准；即使检索片段中出现了订单或物流信息，也不得代替工具调用，工具返回结果前不要编造订单号、物流信息或售后处理结果。
3. 调用 query_order / query_logistics / apply_refund 前必须拿到订单号（order_sn）；用户未提供订单号时，输出 "CLARIFY: <追问内容>" 向用户索要，不要编造订单号调用工具。
4. 退货/退款流程：先 query_order 确认订单状态，再 apply_refund(order_sn, reason) 申请退款；退款原因（reason）用户未说明时先询问。
5. 工具查不到相关信息（如订单不存在、物流无记录）时，如实告知用户查不到，并建议转人工客服处理。
6. 参数缺失澄清：如果用户要创建工单但缺少必填参数（title/description），不要直接调用 create_ticket，而是输出 "CLARIFY: <追问内容>" 询问用户。
7. 商品价格/库存/服务承诺等知识性问题，基于 search_knowledge 检索结果回答，不要编造。
8. 多轮对话：用户已在上文提供过的关键信息（如订单号）不要重复索要，优先从「已有对话」中提取；apply_refund 只需订单号与退款原因，用户已给出时直接调用工具，不要按知识库表单流程索要会员账号、商品明细、凭证等额外信息。
9. 实体预填：系统可能已自动识别用户问题中的订单号/商品 ID 并补填到工具参数（query_order / query_logistics / apply_refund 的 order_sn、query_product 的 product_id）；调用工具时以补填后的参数为准，不要重复索要或编造。

执行示例（订单/物流/退款必须照此调用工具）：
用户：查一下订单 20240801001
助手：Action: query_order
Action Input: {"order_sn": "20240801001"}
（收到工具返回后）Final Answer: 您的订单 20240801001 已发货……

用户：我要退货
助手：CLARIFY: 好的，请问您的订单号是多少？我需要先查询订单信息。

用户：订单号是 20240801001，原因不想要了
助手：Action: apply_refund
Action Input: {"order_sn": "20240801001", "reason": "不想要了"}
（收到工具返回后）Final Answer: 您的退货申请已提交，售后单号 AF20240801001……

用户：物流到哪了？
助手：Action: query_logistics
Action Input: {"order_sn": "20240801001"}
（收到工具返回后）Final Answer: 您的订单 20240801001 物流轨迹：已揽收 → 运输中，预计明天送达……

用户：订单 999999 查一下
助手：Action: query_order
Action Input: {"order_sn": "999999"}
（收到工具返回「订单不存在」后）Final Answer: 未查询到该订单，建议您转人工客服进一步核实。

输出格式：
- 调用工具：Action: <工具名>\\nAction Input: <JSON参数>
- 给出答案：Final Answer: <答案>
- 参数缺失：CLARIFY: <追问内容>
"""

# 工单操作动作词
_TICKET_ACTION_VERBS = ("创建", "提交", "建", "查", "查询", "转", "转接")

# 意图粗判关键词（按优先级：退款 > 物流 > 订单 > 工单；无法判断则跳过）
_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("refund", ("退", "退款", "退货")),
    ("logistics", ("物流", "快递", "到哪")),
    ("order", ("查订单", "订单")),
    ("ticket", ("工单", "建单")),
]


class ToolAgent(BaseReActAgent):
    """通过 MCP Client 调用工具的 ReAct Agent。

    工具列表：search_knowledge / query_order / query_logistics / query_product /
    apply_refund / create_ticket / transfer_human / get_ticket_status（8 个）。
    工具调用通过注入的 MCPClient 完成，便于测试时替换为 mock。
    """

    def __init__(
        self,
        system_prompt: str | None = None,
        mcp_client: MCPClient | None = None,
        entity_fill: bool = True,
    ) -> None:
        super().__init__(system_prompt or DEFAULT_SYSTEM_PROMPT)
        self.mcp_client = mcp_client or get_mcp_client()
        # 实体识别开关：True 时抽取实体 + 参数补填（默认）；False 时关闭
        # （评估脚本 run_eval_agent 用它做 before/after 对比，验证功能有效性）
        self.entity_fill = entity_fill
        # 本轮抽取的实体（订单号/商品 ID），run() 开头赋值，execute_tool 补填用
        self._entities: dict[str, str] = {}

    def get_tools(self) -> list[dict]:
        """返回 MCP Server 暴露的 8 个工具及其参数 schema（含电商业务工具）。"""
        return [
            {
                "name": "search_knowledge",
                "description": "搜索知识库，返回相关文档片段",
                "input_schema": {"query": "str", "role": "str"},
            },
            {
                "name": "query_order",
                "description": "查询电商订单信息（状态/商品明细/实付金额/物流单号）",
                "input_schema": {"order_sn": "str"},
            },
            {
                "name": "query_logistics",
                "description": "查询订单物流轨迹",
                "input_schema": {"order_sn": "str"},
            },
            {
                "name": "query_product",
                "description": "查询商品信息（价格/库存/服务标识）",
                "input_schema": {"product_id": "str"},
            },
            {
                "name": "apply_refund",
                "description": "申请订单退款（创建售后单），需要订单号与退款原因",
                "input_schema": {"order_sn": "str", "reason": "str"},
            },
            {
                "name": "create_ticket",
                "description": "创建客服工单（title/description 必填）",
                "input_schema": {
                    "title": "str",
                    "description": "str",
                    "priority": "str",
                },
            },
            {
                "name": "transfer_human",
                "description": "转人工客服",
                "input_schema": {"reason": "str"},
            },
            {
                "name": "get_ticket_status",
                "description": "查询工单状态",
                "input_schema": {"ticket_id": "str"},
            },
        ]

    def parse_action(self, llm_output: str) -> dict:
        """解析 LLM 输出，增加 CLARIFY 标记处理。

        "CLARIFY: <追问内容>" -> 直接作为最终答案返回（不调用工具）。
        其他情况委托给基类 parse_action。
        """
        text = llm_output.strip()
        if text.startswith("CLARIFY:"):
            clarify = text[len("CLARIFY:"):].strip()
            logger.info("[ToolAgent] 参数缺失，触发澄清: %s", clarify)
            return {"type": "final", "answer": clarify}
        return super().parse_action(llm_output)

    def _is_pure_ticket_op(self, query: str) -> bool:
        """判断是否为纯工单操作（可跳过检索）。

        包含"转人工"，或包含"工单"且有动作词（创建/查询等）。
        """
        if "转人工" in query or "转接人工" in query:
            return True
        if "工单" in query:
            return any(verb in query for verb in _TICKET_ACTION_VERBS)
        return False

    def _has_retrieved(self, state: AgentState) -> bool:
        """检查本轮是否已调用 search_knowledge。"""
        return any(
            tc.get("name") == "search_knowledge"
            for tc in state.get("tool_calls", [])
        )

    def _has_entity_note(self, state: AgentState) -> bool:
        """检查本轮是否已注入实体识别提示（避免重复注入）。"""
        prefix = "（系统实体识别"
        return any(
            isinstance(m, AIMessage)
            and str(getattr(m, "content", "")).startswith(prefix)
            for m in state.get("messages", [])
        )

    async def think(self, state: AgentState) -> dict:
        """思考节点：强制 Retrieval Before Agency + 实体提示注入。

        1. 若本轮尚未检索且 query 非纯工单操作，优先调用 search_knowledge，
           不调用 LLM（节省 token），直接返回检索动作。
        2. 已抽取到实体（订单号/商品 ID）且未注入过时，向 messages 注入提示
           （不消耗迭代，act 空转后回到 think 再决策），
           帮助 LLM 多轮对话正确选参（如「物流到哪了」依赖上文订单号）。
        否则委托基类 think 调用 LLM 决策。
        """
        query = extract_query(state.get("messages", []))

        if (
            query
            and not self._has_retrieved(state)
            and not self._is_pure_ticket_op(query)
        ):
            logger.info("[ToolAgent] Retrieval Before Agency: 先检索知识库")
            iterations = state.get("iterations", 0) + 1
            return {
                "iterations": iterations,
                "pending_action": {
                    "name": "search_knowledge",
                    "input": {"query": query},
                },
                "messages": [AIMessage(content="（系统：先检索知识库）")],
            }

        if self._entities and not self._has_entity_note(state):
            hints = []
            if self._entities.get("order_sn"):
                hints.append(f"订单号 {self._entities['order_sn']}")
            if self._entities.get("product_id"):
                hints.append(f"商品 ID {self._entities['product_id']}")
            if hints:
                note = f"（系统实体识别：已从用户问题提取 {'、'.join(hints)}，可直接用于工具参数"
                # 顺带注入缺失槽位提示（确定性规则）：意图可判且还缺槽位时追加
                missing = self._missing_slots(query, state)
                if missing:
                    note += f"，还缺{'、'.join(missing)}"
                note += "）"
                logger.info("[ToolAgent] 注入实体识别提示: %s", note)
                return {"messages": [AIMessage(content=note)]}

        return await super().think(state)

    @staticmethod
    def _guess_intent(query: str) -> str | None:
        """从 query 关键词粗判当前意图（退款 > 物流 > 订单 > 工单；无法判断返回 None）。"""
        for intent, keywords in _INTENT_KEYWORDS:
            if any(keyword in query for keyword in keywords):
                return intent
        return None

    @staticmethod
    def _history_from_state(messages: list) -> list[dict[str, str]]:
        """把 state messages 转成 [{role, content}] 历史（供槽位提取）。

        去掉当前 query（最后一条 HumanMessage，extract_query 语义），
        跳过工具消息（ToolMessage 不参与槽位提取）。
        """
        last_human_idx = -1
        for i, m in enumerate(messages):
            if isinstance(m, HumanMessage):
                last_human_idx = i
        history: list[dict[str, str]] = []
        for i, m in enumerate(messages):
            if i == last_human_idx:
                continue
            content = getattr(m, "content", "")
            if not isinstance(content, str):
                continue
            if isinstance(m, HumanMessage):
                history.append({"role": "user", "content": content})
            elif isinstance(m, AIMessage):
                history.append({"role": "assistant", "content": content})
        return history

    def _missing_slots(self, query: str, state: AgentState) -> list[str]:
        """计算当前意图还缺的槽位（确定性规则，供提示注入；无法判断意图返回空列表）。"""
        intent = self._guess_intent(query)
        if intent is None:
            return []
        slots = extract_slots(query, self._history_from_state(state.get("messages", [])))
        return missing_slots(intent, slots)

    async def execute_tool(self, name: str, input_data: dict) -> Any:
        """通过 MCP Client 调用工具，先做实体参数补填。

        LLM 决策调用业务工具但 input 缺关键参数（order_sn/product_id）时，
        用实体识别结果补填（减少编造订单号与 CLARIFY 次数）。

        MCPClient.call_tool 内部已处理失败降级（返回 {"error": ...}），
        此处不额外捕获异常。
        """
        input_data = input_data or {}
        filled_args, filled = fill_tool_args(name, input_data, self._entities)
        if filled:
            logger.info("[ToolAgent] 实体补填 %s 参数: %s -> %s", name, input_data, filled_args)
        return await self.mcp_client.call_tool(name, filled_args)

    async def run(
        self, query: str, history: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """运行 Agent，增加 MCP 可用性预检查。

        Args:
            query: 用户当前输入
            history: 多轮对话历史（可选，见 BaseReActAgent.run），
                用于「查物流到哪了」等依赖上文订单号的多轮场景。

        MCP 不可用时直接返回降级话术，不进入 ReAct 循环。
        """
        # 实体识别：当前问题 + 多轮历史 → 订单号/商品 ID（execute_tool 补填用）
        # 规则层确定性优先；ENTITY_LLM_FALLBACK 开启时规则未命中才走 LLM 兜底
        # entity_fill=False 时关闭（评估脚本 before/after 对比用）
        self._entities = (
            await extract_with_llm(query, history) if (query and self.entity_fill) else {}
        )

        if not self.mcp_client.is_connected:
            connected = await self.mcp_client.connect()
            if not connected:
                logger.warning("[ToolAgent] MCP 不可用，降级返回")
                return {
                    "answer": "工具服务暂时不可用，已为您记录问题，请稍后重试或转人工客服。",
                    "tool_calls": [],
                    "iterations": 0,
                    "degraded": True,
                }
        return await super().run(query, history=history)
