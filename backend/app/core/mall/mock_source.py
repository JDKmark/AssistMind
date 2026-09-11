"""模拟电商业务数据源：预置演示数据（固定清单，Agent 客服演示用）。

数据与固定演示清单完全一致：
- 商品 7 个（P001-P007；P006/P007 为跨品类同型号商品，共用型号别名 S1 Pro）
- 订单 6 个（20260801001-20260801006；001/002/005 归属 user1，003/004/006 归属 user2；
  005 仅含 P006、006 同时含 P006+P007——供「仅持有其中一款 / 同时持有两款」消歧演示）
- 物流轨迹仅 20260801001（已揽收 → 运输中）

售后单（apply_refund）进程内内存记录；未知单号/商品返回 None 不抛异常。
后续接入真实 ERP 时替换为 real 实现（见 data_source.py 门面注释）。
"""

from __future__ import annotations

from typing import Any

from app.core.mall.base import MallDataSource
from app.core.mall.presentation import (
    admin_order_row,
    mask_free_text,
    order_view,
    product_view,
    refund_view,
)

# 演示用户 user_id（phase13：与用户端演示身份一致，授权以 user_id 为权威）。
# real 模式由 users 表 UUID 权威；mock 用稳定演示 id 保持同语义。
_DEMO_USER_IDS = {
    "admin": "uid-admin",
    "agent": "uid-agent",
    "user": "uid-user",
    "user1": "uid-user1",
    "user2": "uid-user2",
}


def _order_owned_by(
    order: dict,
    requester_user_id: str,
    requester_username: str,
    requester_role: str,
) -> bool:
    """普通用户归属校验：user_id 命中即放行，否则按 owner_username 兜底。

    mock 行的 owner_user_id 是演示静态值（uid-user1 等），与真实登录用户的
    users 表 UUID 不同源，仅按 user_id 匹配会令演示用户名下"无订单"——
    username 兜底与 real 数据源旧行兼容语义一致。agent/admin 不受归属限制。
    """
    if requester_role in {"agent", "admin"}:
        return True
    if order.get("owner_user_id") and order["owner_user_id"] == requester_user_id:
        return True
    return order.get("owner_username") == requester_username


def _owner_matches(
    owner_user_id: str | None,
    owner_username: str | None,
    target_user_id: str | None,
    target_username: str | None,
) -> bool:
    """管理端归属过滤：user_id 命中即匹配，否则按 username 兜底（演示 uid 不同源）。"""
    if owner_user_id and owner_user_id == target_user_id:
        return True
    return owner_username == target_username

# ---- 商品（固定清单） ----
# {id, name, spec, price, stock, services(服务标识中文)}
PRODUCTS: dict[str, dict[str, Any]] = {
    "P001": {
        "id": "P001",
        "name": "华为 Mate 70 Pro",
        "spec": "256G 曜石黑",
        "price": 6999,
        "stock": 200,
        "services": ["无忧退货", "免费包邮"],
    },
    "P002": {
        "id": "P002",
        "name": "小米电视 65 英寸",
        "spec": "65英寸",
        "price": 3499,
        "stock": 100,
        "services": [],
    },
    "P003": {
        "id": "P003",
        "name": "戴森 V12 吸尘器",
        "spec": "V12",
        "price": 4990,
        "stock": 80,
        "services": ["无忧退货", "快速退款", "免费包邮"],
    },
    "P004": {
        "id": "P004",
        "name": "Apple AirPods Pro",
        "spec": "Pro",
        "price": 1899,
        "stock": 300,
        "services": [],
    },
    "P005": {
        "id": "P005",
        "name": "联想拯救者笔记本",
        "spec": "拯救者系列",
        "price": 8999,
        "stock": 50,
        "services": [],
    },
    # P006/P007：跨品类同型号商品（共用型号别名 S1 Pro），产品消歧演示数据源。
    # symptom_keywords 不在此维护（外置 app/data/product_models.json，热加载）
    "P006": {
        "id": "P006",
        "name": "贝亲 S1 Pro 电动吸奶器",
        "spec": "双边电动 静音款",
        "price": 1299,
        "stock": 150,
        "services": [],
    },
    "P007": {
        "id": "P007",
        "name": "追觅 S1 Pro 扫地机器人",
        "spec": "自集尘 拖扫一体",
        "price": 2999,
        "stock": 120,
        "services": [],
    },
}

# ---- 订单（固定清单） ----
# {order_sn, status, items, pay_amount, logistics_no, created_at}
ORDERS: dict[str, dict[str, Any]] = {
    "20260801001": {
        "order_sn": "20260801001",
        "owner_username": "user1",
        "owner_user_id": _DEMO_USER_IDS["user1"],
        "status": "已发货",
        "items": [
            {
                "product_id": "P001",
                "name": "华为 Mate 70 Pro",
                "spec": "256G 曜石黑",
                "price": 6999,
                "quantity": 1,
            }
        ],
        "pay_amount": 6999,
        "logistics_no": "SF1234567890",
        "created_at": "2026-08-01 09:30:00",
    },
    "20260801002": {
        "order_sn": "20260801002",
        "owner_username": "user1",
        "owner_user_id": _DEMO_USER_IDS["user1"],
        "status": "待发货",
        "items": [
            {
                "product_id": "P002",
                "name": "小米电视 65 英寸",
                "spec": "65英寸",
                "price": 3499,
                "quantity": 1,
            },
            {
                "product_id": "P004",
                "name": "Apple AirPods Pro",
                "spec": "Pro",
                "price": 1899,
                "quantity": 1,
            },
        ],
        "pay_amount": 5398,
        "logistics_no": None,
        "created_at": "2026-08-01 10:05:00",
    },
    "20260801003": {
        "order_sn": "20260801003",
        "owner_username": "user2",
        "owner_user_id": _DEMO_USER_IDS["user2"],
        "status": "已完成",
        "items": [
            {
                "product_id": "P003",
                "name": "戴森 V12 吸尘器",
                "spec": "V12",
                "price": 4990,
                "quantity": 1,
            }
        ],
        "pay_amount": 4990,
        "logistics_no": None,
        "created_at": "2026-08-01 11:20:00",
    },
    "20260801004": {
        "order_sn": "20260801004",
        "owner_username": "user2",
        "owner_user_id": _DEMO_USER_IDS["user2"],
        "status": "待付款",
        "items": [
            {
                "product_id": "P005",
                "name": "联想拯救者笔记本",
                "spec": "拯救者系列",
                "price": 8999,
                "quantity": 1,
            }
        ],
        "pay_amount": 8999,
        "logistics_no": None,
        "created_at": "2026-08-01 12:00:00",
    },
    # phase15 消歧演示订单：005 user1 仅持 P006（订单交集唯一）；
    # 006 user2 同时持 P006+P007（交集歧义 → 反问/信号/LLM 兜底分流）
    "20260801005": {
        "order_sn": "20260801005",
        "owner_username": "user1",
        "owner_user_id": _DEMO_USER_IDS["user1"],
        "status": "已完成",
        "items": [
            {
                "product_id": "P006",
                "name": "贝亲 S1 Pro 电动吸奶器",
                "spec": "双边电动 静音款",
                "price": 1299,
                "quantity": 1,
            }
        ],
        "pay_amount": 1299,
        "logistics_no": None,
        "created_at": "2026-08-02 09:00:00",
    },
    "20260801006": {
        "order_sn": "20260801006",
        "owner_username": "user2",
        "owner_user_id": _DEMO_USER_IDS["user2"],
        "status": "已完成",
        "items": [
            {
                "product_id": "P006",
                "name": "贝亲 S1 Pro 电动吸奶器",
                "spec": "双边电动 静音款",
                "price": 1299,
                "quantity": 1,
            },
            {
                "product_id": "P007",
                "name": "追觅 S1 Pro 扫地机器人",
                "spec": "自集尘 拖扫一体",
                "price": 2999,
                "quantity": 1,
            },
        ],
        "pay_amount": 4298,
        "logistics_no": None,
        "created_at": "2026-08-02 10:00:00",
    },
}

# ---- 物流轨迹（固定清单，仅 20260801001） ----
# {order_sn: [{ts, content}]}，按时间正序
LOGISTICS: dict[str, list[dict[str, str]]] = {
    "20260801001": [
        {"ts": "2026-08-01 16:00:00", "content": "已揽收"},
        {"ts": "2026-08-01 18:30:00", "content": "运输中（预计明天送达）"},
    ],
}

# 可申请售后的订单状态
_REFUNDABLE_STATUSES = ("待发货", "已发货", "已完成")


class MockMallDataSource(MallDataSource):
    """模拟电商数据源：固定演示数据 + 进程内售后单记录。"""

    def __init__(self) -> None:
        # 售后单记录（进程内内存；order_sn -> 售后单 dict，重复申请幂等复用）
        self._refunds: dict[str, dict[str, Any]] = {}

    @property
    def source_mode(self) -> str:
        return "mock"

    async def query_order(
        self,
        order_sn: str,
        *,
        requester_user_id: str,
        requester_username: str,
        requester_role: str,
    ) -> dict | None:
        """查询订单信息。未知或无权访问时返回 None。"""
        order = ORDERS.get(order_sn)
        if order is None or not _order_owned_by(
            order, requester_user_id, requester_username, requester_role
        ):
            return None
        return order_view(order, requester_role)

    async def list_orders(
        self,
        *,
        owner_username: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查询订单列表：过滤 → created_at 倒序 → 先 total 后切片分页。

        列表项含 owner_username（与 query_order 单查不返回 owner 的契约互补，
        管理端需要归属信息做演示隔离展示）。owner_username 过滤先解析为
        user_id 再匹配规范归属字段；未知用户名返回空列表。
        """
        target_user_id = (
            _DEMO_USER_IDS.get(owner_username) if owner_username is not None else None
        )
        if owner_username is not None and target_user_id is None:
            return {"orders": [], "total": 0}

        def _match(order: dict) -> bool:
            if owner_username is not None and not _owner_matches(
                order.get("owner_user_id"),
                order.get("owner_username"),
                target_user_id,
                owner_username,
            ):
                return False
            return status is None or order["status"] == status

        filtered = [order for order in ORDERS.values() if _match(order)]
        filtered.sort(key=lambda order: order["created_at"], reverse=True)
        return {
            "orders": [
                admin_order_row(
                    {
                        "order_sn": order["order_sn"],
                        "owner_username": order.get("owner_username"),
                        "owner_user_id": order.get("owner_user_id"),
                        "status": order["status"],
                        "pay_amount": order["pay_amount"],
                        "logistics_no": order["logistics_no"],
                        "created_at": order["created_at"],
                    }
                )
                for order in filtered[offset : offset + limit]
            ],
            "total": len(filtered),
        }

    async def my_orders(
        self,
        *,
        requester_user_id: str,
        requester_username: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查询当前用户订单列表：过滤 → created_at 倒序 → 先 total 后切片分页。

        行含完整 items（用户端需要商品明细展示；与 list_orders 管理端摘要
        不含 items 的契约互补）。归属以 user_id 为权威，旧行 username 兼容。
        """
        filtered = [
            order
            for order in ORDERS.values()
            if _order_owned_by(order, requester_user_id, requester_username, "user")
            and (status is None or order["status"] == status)
        ]
        filtered.sort(key=lambda order: order["created_at"], reverse=True)
        return {
            "orders": [
                order_view(
                    {
                        "order_sn": order["order_sn"],
                        "status": order["status"],
                        "pay_amount": order["pay_amount"],
                        "logistics_no": order["logistics_no"],
                        "created_at": order["created_at"],
                        "items": order["items"],
                    },
                    "user",
                )
                for order in filtered[offset : offset + limit]
            ],
            "total": len(filtered),
        }

    async def query_logistics(
        self,
        order_sn: str,
        *,
        requester_user_id: str,
        requester_username: str,
        requester_role: str,
    ) -> list[dict]:
        """查询物流轨迹。未发货、未知或无权访问时返回空列表。"""
        order = ORDERS.get(order_sn)
        if order is None or not _order_owned_by(
            order, requester_user_id, requester_username, requester_role
        ):
            return []
        return [
            {"ts": trace["ts"], "content": mask_free_text(trace["content"])}
            for trace in LOGISTICS.get(order_sn, [])
        ]

    async def query_product(self, product_id: str, *, requester_role: str) -> dict | None:
        """查询商品信息。未知 product_id 返回 None；展示按角色最小披露。"""
        return product_view(PRODUCTS.get(product_id), requester_role)

    async def apply_refund(
        self,
        order_sn: str,
        reason: str,
        *,
        requester_user_id: str,
        requester_username: str,
        requester_role: str,
    ) -> dict:
        """创建售后（退款）单。

        状态机：
        - 未知订单 → 拒绝（订单不存在）
        - 待付款 → 拒绝（未支付不可售后）
        - 待发货 / 已发货 / 已完成 → 创建售后单，refund_id = AF{order_sn}
        - 重复申请 → 返回已存在的售后单（幂等）
        """
        order = ORDERS.get(order_sn)
        if order is None or not _order_owned_by(
            order, requester_user_id, requester_username, requester_role
        ):
            return {
                "refund_id": None,
                "status": "failed",
                "message": "订单不存在或无权操作，无法申请退款",
            }
        if order["status"] == "待付款":
            return {
                "refund_id": None,
                "status": "failed",
                "message": f"订单 {order_sn} 待付款，请先完成支付后再申请退款",
            }

        existing = self._refunds.get(order_sn)
        if existing is not None:
            return {
                "refund_id": existing["refund_id"],
                "status": existing["status"],
                "message": f"订单 {order_sn} 已申请过售后（{existing['refund_id']}），请勿重复提交",
            }

        refund_id = f"AF{order_sn}"
        refund = {
            "refund_id": refund_id,
            "order_sn": order_sn,
            "owner_username": order.get("owner_username"),
            "reason": reason,
            "status": "处理中",
            "created_at": order["created_at"],
        }
        self._refunds[order_sn] = refund
        return {
            "refund_id": refund_id,
            "status": refund["status"],
            "message": f"售后申请已提交，售后单号 {refund_id}",
        }

    async def list_refunds(
        self,
        *,
        status: str | None = None,
        owner_username: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        target_user_id = (
            _DEMO_USER_IDS.get(owner_username) if owner_username is not None else None
        )
        if owner_username is not None and target_user_id is None:
            return {"refunds": [], "total": 0}
        refunds = [
            refund
            for refund in self._refunds.values()
            if (status is None or refund["status"] == status)
            and (
                owner_username is None
                or _owner_matches(
                    ORDERS.get(refund["order_sn"], {}).get("owner_user_id"),
                    refund.get("owner_username"),
                    target_user_id,
                    owner_username,
                )
            )
        ]
        refunds.sort(key=lambda item: item["created_at"], reverse=True)
        return {
            "refunds": [
                refund_view(
                    {
                        **refund,
                        "owner_user_id": ORDERS.get(refund["order_sn"], {}).get("owner_user_id"),
                    },
                    "admin",
                )
                for refund in refunds[offset : offset + limit]
            ],
            "total": len(refunds),
        }

    async def update_refund_status(self, refund_id: str, new_status: str) -> dict:
        refund = next(
            (item for item in self._refunds.values() if item["refund_id"] == refund_id), None
        )
        if refund is None:
            raise LookupError(f"退款单不存在: {refund_id}")
        if refund["status"] != "处理中" or new_status not in {"已通过", "已拒绝"}:
            raise ValueError(f"非法状态流转: {refund['status']} -> {new_status}")
        refund["status"] = new_status
        return refund_view(refund, "admin")
