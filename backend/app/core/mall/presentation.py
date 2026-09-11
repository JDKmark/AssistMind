"""商城展示策略：按调用者角色统一最小化商品 / 订单 / 退款输出。

phase13 目标：库存精确值、物流单号、用户名与自由文本中的个人信息只按需披露，
Mock / Real 两种数据源与 HTTP / MCP 两类入口共用同一套掩码逻辑，
避免各处各自实现不一致。

角色语义：
- user：最小披露（无归属字段、物流单号掩码、仅 stock_status）
- agent：运营可处理（归属用户名掩码、物流单号掩码、自由文本 PII 掩码、仅 stock_status）
- admin：单查/商品可完整（精确 stock、完整物流单号/用户名）；批量列表用户名掩码
  （owner_user_id 保留为稳定关联，防批量泄露完整用户名）
- 掩码顺序：先按归属授权（数据源层）再脱敏（本层），见 mock/real 实现

本模块为纯函数，不依赖 API、MCP Context 或具体数据源。
"""

from __future__ import annotations

import re

STOCK_IN = "有货"
STOCK_OUT = "缺货"

# 中国大陆手机号（11 位，1 开头）
_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 邮箱：保留本地部分首字符 + 域名，掩码本地其余部分
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@")


def stock_status(stock: int | None) -> str:
    """库存状态：>0 有货，否则缺货（不暴露精确数量）。"""
    return STOCK_IN if (stock or 0) > 0 else STOCK_OUT


def mask_identifier(value: str | None, keep: int = 4) -> str | None:
    """标识掩码：保留末 keep 位，其余替换为 *；空值原样返回。"""
    if not value:
        return value
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def mask_username(username: str | None) -> str | None:
    """用户名掩码：保留首字符 + ***；单字符全部掩码。"""
    if not username:
        return username
    if len(username) <= 1:
        return "*" * len(username)
    return username[0] + "***"


def mask_free_text(text: str | None) -> str | None:
    """自由文本 PII 掩码：手机号（保留前 3 + 后 4）、邮箱（保留首字符 + 域名）。"""
    if not text:
        return text
    text = _MOBILE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)
    text = _EMAIL_RE.sub(lambda m: m.group(1) + "***@", text)
    return text


def product_view(product: dict | None, role: str) -> dict | None:
    """商品视图：user/agent 只返回 stock_status；admin 额外保留精确 stock。"""
    if product is None:
        return None
    view = dict(product)
    view.pop("stock", None)
    view["stock_status"] = stock_status(product.get("stock"))
    if role == "admin":
        view["stock"] = product.get("stock")
    return view


def order_view(order: dict | None, role: str) -> dict | None:
    """订单单查视图：user 无归属且物流掩码；agent 归属掩码且物流掩码；admin 完整。"""
    if order is None:
        return None
    view = dict(order)
    if role == "admin":
        return view
    if view.get("logistics_no"):
        view["logistics_no"] = mask_identifier(view["logistics_no"])
    if "owner_username" in view:
        if role == "agent" and view.get("owner_username"):
            view["owner_username"] = mask_username(view["owner_username"])
        else:
            view.pop("owner_username", None)
    view.pop("owner_user_id", None)
    return view


def admin_order_row(order: dict) -> dict:
    """管理端订单行（仅 require_admin 端点消费）：owner_user_id 稳定关联 + 用户名掩码展示值。

    phase13 spec：admin 订单/退款列表使用 owner_user_id 作为稳定关联，username 仅返回
    掩码展示值（防批量泄露完整用户名）；admin 单查（order_view role=admin）仍保持完整。
    """
    view = dict(order)
    if view.get("owner_username"):
        view["owner_username"] = mask_username(view["owner_username"])
    return view


def refund_view(refund: dict | None, role: str) -> dict | None:
    """退款视图：reason 自由文本 PII 掩码；用户名掩码。

    admin 保留 owner_user_id 作为稳定关联字段；非 admin 不输出内部 owner_user_id。
    """
    if refund is None:
        return None
    view = dict(refund)
    if view.get("reason"):
        view["reason"] = mask_free_text(view["reason"])
    if view.get("owner_username"):
        view["owner_username"] = mask_username(view["owner_username"])
    if role != "admin":
        view.pop("owner_user_id", None)
    return view
