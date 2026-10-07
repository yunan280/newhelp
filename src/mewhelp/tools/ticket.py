"""create_ticket —— 真写 tickets 表，建单与转人工相互独立。

这是五个工具里唯一的**写**操作,所以它是全章唯一一个 retryable=False 的工具。
"""

import datetime as dt
from collections.abc import Callable
from typing import Literal

from langchain_core.tools import BaseTool, tool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mewhelp.db.models import TicketType
from mewhelp.db.repository import find_ticket_by_request_id, insert_ticket, next_ticket_no

from .business import build_business_tools
from .contracts import ToolCallContext
from .knowledge import build_knowledge_tools
from .registry import ToolRegistry, ToolSpec

# 工单号撞主键后的递增重试上限。
#
# **这不是**执行管线那个重试:那个重试是"同一个动作再放一遍",会重复建单;
# 这个重试是"换一个号再试",不会。两者不要混为一谈 —— 前者对写操作有害,
# 后者是写操作唯一的补救手段。
_MAX_TICKET_NO_ATTEMPTS = 5

_TICKET_TEMPLATE = (
    "已为用户创建工单,工单号 {ticket_no},类型「{ticket_type}」。请告知用户凭此工单号跟进。"
)


def build_ticket_tools(
    session_factory: Callable[[], Session], conversation_id: int, *, request_id: str | None = None
) -> list[BaseTool]:
    """返回建单工具。

    `conversation_id` 由**闭包**注入,不在函数签名里 —— 因此它不可能出现在
    args_schema 里,更不可能被模型幻觉出来。自增主键本就不该让模型编。
    (spec §9.2:`InjectedToolArg` 在本机 1.6.5 上实测不生效,故改用闭包。)
    """

    @tool
    def create_ticket(
        description: str,
        ticket_type: Literal["售后", "投诉", "咨询"],
    ) -> str | dict:
        """为用户创建人工工单。description 是问题描述,ticket_type 只能是售后/投诉/咨询。

        仅用于用户明确确认创建工单；不会转人工或修改会话状态。
        """
        last_error: Exception | None = None

        def existing_receipt(session):
            existing = (
                find_ticket_by_request_id(session, request_id=request_id) if request_id else None
            )
            if existing is None:
                return None
            if (
                existing.conversation_id != conversation_id
                or existing.description != description
                or existing.ticket_type.value != ticket_type
            ):
                raise ValueError("ticket request_id conflicts with existing parameters")
            return _TICKET_TEMPLATE.format(ticket_no=existing.ticket_no, ticket_type=ticket_type)

        for _ in range(_MAX_TICKET_NO_ATTEMPTS):
            with session_factory() as session:
                receipt = existing_receipt(session)
                if receipt:
                    return receipt
                try:
                    ticket_no = next_ticket_no(session, day=dt.date.today())  # noqa: DTZ011 — 工单编号沿用本地日期
                    insert_ticket(
                        session,
                        conversation_id=conversation_id,
                        description=description,
                        ticket_type=TicketType(ticket_type),
                        ticket_no=ticket_no,
                        request_id=request_id,
                    )
                    session.commit()
                except IntegrityError as exc:
                    # 并发下两个请求算出了同一个号。换一个再试 —— 这不是盲目重放。
                    #
                    # 显式 rollback **不是**"没写脏"的必要条件:这一轮的 session 随
                    # with 退出就被 close 了,close 本身就会回滚(实测:去掉这句,
                    # 工单数与会话状态都不变)。它买的是"失败事务立刻结束" ——
                    # 代码不该依赖 close() 的隐式行为,哪天有人把 Session 提到循环外
                    # 复用,少这一句就会撞上那个已经失败的事务。
                    session.rollback()
                    receipt = existing_receipt(session)
                    if receipt:
                        return receipt
                    detail = str(exc.orig)
                    if not ('UNIQUE constraint failed: tickets.ticket_no' in detail or
                            getattr(exc.orig, 'args', (None,))[0] == 1062):
                        raise
                    last_error = exc
                    continue
            return _TICKET_TEMPLATE.format(ticket_no=ticket_no, ticket_type=ticket_type)

        # 五次都撞上说明不是并发抖动。如实告诉模型失败,由它转告用户,
        # 而不是抛一个 500 出去 —— 那会让整轮对话断在这里。
        return {'outcome':'error', 'message': (
            f"创建工单失败(连续 {_MAX_TICKET_NO_ATTEMPTS} 次工单号冲突):{last_error}。"
            "请告知用户稍后重试,或建议其直接联系人工客服。"
        )}

    return [create_ticket]


def build_registry(
    session_factory: Callable[[], Session], conversation_id: int, **knowledge_dependencies
) -> ToolRegistry:
    """隔离调用的兼容注册表；生产通过共享 Ch08 runtime 热发现 MCP。

    收 **session 工厂**而不是 Session 实例:工具经 @tool 的 ainvoke 跑在线程池里
    (实测跑在 asyncio_0 线程),而 SQLAlchemy 的 Session 非线程安全。
    多个 tool_calls 经 asyncio.gather 并发时,共用一个 Session 会踩线程安全问题 ——
    每个工具调用自己开一个,正是 spec §11「每调用独立 session」的意思。
    """
    specs: dict[str, ToolSpec] = {t.name: ToolSpec(tool=t) for t in build_business_tools()}
    # BGE-M3 冷加载在 Docker 同时启动时曾耗时 58.6 秒。线程里的超时调用
    # 不会被 wait_for 取消，因此 FAQ 只执行一次并留出冷启动余量。
    specs.update(
        {
            t.name: ToolSpec(tool=t, retryable=False, timeout_seconds=120.0)
            for t in build_knowledge_tools(session_factory, **knowledge_dependencies)
        }
    )
    for t in build_ticket_tools(session_factory, conversation_id):
        # 唯一的写操作:不重试,避免重复建单。
        specs[t.name] = ToolSpec(tool=t, retryable=False, permission='write')
    return ToolRegistry(specs)


def build_ticket_spec(session_factory) -> ToolSpec:
    """启动登记 Schema，执行时才绑定可信会话与确认幂等键。"""
    template = build_ticket_tools(session_factory, 0)[0]
    def factory(context: ToolCallContext):
        if context.conversation_id is None or context.authorization is None:
            raise ValueError('建单缺少可信确认上下文')
        return build_ticket_tools(session_factory, context.conversation_id,
                                  request_id=context.authorization.confirmation_id)[0]
    return ToolSpec(template, retryable=False, permission='write', tool_factory=factory)
