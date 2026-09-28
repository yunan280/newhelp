"""create_ticket —— 真写 tickets 表,并把会话置「已转人工」。

这是五个工具里唯一的**写**操作,所以它是全章唯一一个 retryable=False 的工具。
"""

import datetime as dt
from collections.abc import Callable
from typing import Literal

from langchain_core.tools import BaseTool, tool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mewhelp.db.models import ConvStatus, TicketType
from mewhelp.db.repository import insert_ticket, next_ticket_no, set_conversation_status

from .business import build_business_tools
from .knowledge import build_knowledge_tools
from .registry import ToolRegistry, ToolSpec

# 工单号撞主键后的递增重试上限。
#
# **这不是**执行管线那个重试:那个重试是"同一个动作再放一遍",会重复建单;
# 这个重试是"换一个号再试",不会。两者不要混为一谈 —— 前者对写操作有害,
# 后者是写操作唯一的补救手段。
_MAX_TICKET_NO_ATTEMPTS = 5

_TICKET_TEMPLATE = (
    "已为用户创建工单,工单号 {ticket_no},类型「{ticket_type}」。"
    "已同步转交人工客服,请告知用户凭此工单号跟进。"
)


def build_ticket_tools(
    session_factory: Callable[[], Session], conversation_id: int
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
    ) -> str:
        """为用户创建人工工单。description 是问题描述,ticket_type 只能是售后/投诉/咨询。

        用户明确要求转人工、或描述的是需要人工处理的投诉与售后问题时使用。
        """
        last_error: Exception | None = None
        for _ in range(_MAX_TICKET_NO_ATTEMPTS):
            with session_factory() as session:
                try:
                    ticket_no = next_ticket_no(session, day=dt.date.today())
                    insert_ticket(
                        session,
                        conversation_id=conversation_id,
                        description=description,
                        ticket_type=TicketType(ticket_type),
                        ticket_no=ticket_no,
                    )
                    set_conversation_status(
                        session, conversation_id=conversation_id, status=ConvStatus.human
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
                    last_error = exc
                    continue
            return _TICKET_TEMPLATE.format(ticket_no=ticket_no, ticket_type=ticket_type)

        # 五次都撞上说明不是并发抖动。如实告诉模型失败,由它转告用户,
        # 而不是抛一个 500 出去 —— 那会让整轮对话断在这里。
        return (
            f"创建工单失败(连续 {_MAX_TICKET_NO_ATTEMPTS} 次工单号冲突):{last_error}。"
            "请告知用户稍后重试,或建议其直接联系人工客服。"
        )

    return [create_ticket]


def build_registry(
    session_factory: Callable[[], Session], conversation_id: int
) -> ToolRegistry:
    """汇总五个工具。

    收 **session 工厂**而不是 Session 实例:工具经 @tool 的 ainvoke 跑在线程池里
    (实测跑在 asyncio_0 线程),而 SQLAlchemy 的 Session 非线程安全。
    多个 tool_calls 经 asyncio.gather 并发时,共用一个 Session 会踩线程安全问题 ——
    每个工具调用自己开一个,正是 spec §11「每调用独立 session」的意思。
    """
    specs: dict[str, ToolSpec] = {
        t.name: ToolSpec(tool=t) for t in build_business_tools()
    }
    specs.update({t.name: ToolSpec(tool=t) for t in build_knowledge_tools(session_factory)})
    for t in build_ticket_tools(session_factory, conversation_id):
        # 唯一的写操作:不重试,避免重复建单。
        specs[t.name] = ToolSpec(tool=t, retryable=False)
    return ToolRegistry(specs)
