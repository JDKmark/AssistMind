"""RealMallDataSource 归属过滤单元测试（SQL 编译级，不连真实 PG）。

背景回归：deps 降级（旧 token + PG 故障放行）返回 user_id=None 时，
`owner_user_id == None` 会被 SQLAlchemy 渲染成 `owner_user_id IS NULL`，
命中全部未回填 owner_user_id 的存量订单（跨用户越权读取）。
"""

from __future__ import annotations

from app.core.mall.real_source import _owner_filter


def _compile(cond) -> str:
    return str(cond.compile(compile_kwargs={"literal_binds": True}))


def test_owner_filter_none_user_id_not_bare_is_null():
    """user_id=None：首分支渲染为空串等值匹配，而非裸 IS NULL 命中全部旧行。"""
    sql = _compile(_owner_filter(None, "alice"))
    assert "owner_user_id = ''" in sql
    # username 兼容分支保留（迁移窗口语义），且 IS NULL 必须与 username 条件绑定出现
    assert "owner_user_id IS NULL AND mall_orders.owner_username = 'alice'" in sql


def test_owner_filter_empty_user_id_keeps_username_fallback():
    """user_id 缺失但 username 存在：仍按 username 兼容分支匹配（迁移窗口语义）。"""
    sql = _compile(_owner_filter("", "bob"))
    assert "owner_username = 'bob'" in sql


def test_owner_filter_both_empty_is_false():
    """身份全空（无法自证归属）：恒假条件，不返回任何行。"""
    sql = _compile(_owner_filter(None, None))
    assert sql == "false"
