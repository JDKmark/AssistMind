"""电商业务数据源抽象接口。

统一 mock（预置演示数据）与 real（未来对接 ERP 系统）两种实现的查询契约。
返回形状为 Agent 工具契约，两种实现必须保持一致：
- query_order → dict（order_sn / status / items / pay_amount / logistics_no / created_at）
- query_logistics → [{ts, content}]
- query_product → dict（id / name / spec / price / stock / services）
- apply_refund → {refund_id, status, message}

失败降级语义：query_order / query_product 未命中返回 None 不抛异常；
apply_refund 对非法状态（待付款/未知订单）返回失败结果 dict，不抛异常。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MallDataSource(ABC):
    """电商业务数据源接口：订单 / 物流 / 商品 / 售后统一查询。"""

    @property
    @abstractmethod
    def source_mode(self) -> str:
        """数据源模式标识：mock / real（供门面切换与前端展示）。"""

    @abstractmethod
    async def query_order(
        self, order_sn: str, *, requester_username: str, requester_role: str
    ) -> dict | None:
        """查询订单信息。未知 order_sn 返回 None。

        返回字段：
        - order_sn: 订单号
        - status: 订单状态中文描述（待付款/待发货/已发货/已完成）
        - items: 商品明细 [{product_id, name, spec, price, quantity}]
        - pay_amount: 实付金额（元）
        - logistics_no: 物流单号（未发货为 None）
        - created_at: 下单时间
        """

    @abstractmethod
    async def list_orders(
        self,
        *,
        owner_username: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查询订单列表（管理端）。返回 {"orders": [...], "total": int}。

        - 列表项字段：order_sn / owner_username / status / pay_amount / logistics_no / created_at
        - total 为过滤后（分页前）总数；orders 按 created_at 倒序、limit/offset 分页
        - 失败降级语义：real 实现 PostgreSQL 失败时返回
          {"orders": [], "total": 0, "degraded": ["postgres"]} 并 logger.warning，不抛异常
        """

    @abstractmethod
    async def my_orders(
        self,
        *,
        requester_username: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查询当前用户的订单列表（用户端）。返回 {"orders": [...], "total": int}。

        - 归属由服务端强制：仅返回 owner_username == requester_username 的订单
        - 列表项字段：order_sn / status / pay_amount / logistics_no / created_at /
          items（[{product_id, name, spec, price, quantity}]，完整商品明细）
        - total 为过滤后（分页前）总数；orders 按 created_at 倒序、limit/offset 分页
        - 失败降级语义：real 实现 PostgreSQL 失败时返回
          {"orders": [], "total": 0, "degraded": ["postgres"]} 并 logger.warning，不抛异常
        """

    @abstractmethod
    async def query_logistics(
        self, order_sn: str, *, requester_username: str, requester_role: str
    ) -> list[dict]:
        """查询物流轨迹 [{ts, content}]，按时间正序。未发货/未知订单返回空列表。"""

    @abstractmethod
    async def query_product(self, product_id: str) -> dict | None:
        """查询商品信息。未知 product_id 返回 None。

        返回字段：
        - id: 商品编码
        - name: 商品名称
        - spec: 规格
        - price: 价格（元）
        - stock: 库存
        - services: 服务标识列表（中文，如 无忧退货/快速退款/免费包邮）
        """

    @abstractmethod
    async def apply_refund(
        self, order_sn: str, reason: str, *, requester_username: str, requester_role: str
    ) -> dict:
        """创建售后（退款）单。

        状态机：
        - 待发货 / 已发货 / 已完成 → 可申请，返回 {refund_id, status, message}
        - 待付款 → 拒绝，返回 {refund_id: None, status: "failed", message: 提示原因}
        - 未知订单 → 拒绝，返回 {refund_id: None, status: "failed", message: 提示原因}
        - 同一订单重复申请 → 返回已存在的售后单（幂等，不重复创建）
        """

    @abstractmethod
    async def list_refunds(
        self,
        *,
        status: str | None = None,
        owner_username: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查询退款列表（管理端）。"""

    @abstractmethod
    async def update_refund_status(self, refund_id: str, new_status: str) -> dict:
        """执行处理中到已通过或已拒绝的退款状态流转。"""
