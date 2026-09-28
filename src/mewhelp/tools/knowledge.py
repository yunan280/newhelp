"""query_faq —— 真实查 faq 表的关键词检索。

这是本章**刻意保持朴素**的检索(spec §8):`LIKE '%kw%'` 扫三列,不做分词、
不做同义词、不做向量。验收③要展示的正是它的语义鸿沟 —— 那是 ch03 的引子,
现在把它做"好"反而会让 ch03 失去对照基线。
"""

from collections.abc import Callable

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from mewhelp.db.repository import find_faq

# 一次回灌给模型最多几条。12 条全倒进去没有必要,而且会挤占上下文预算。
_MAX_ROWS = 3

# 漏召回时的措辞是**设计的一部分**:它必须明确禁止模型用自己的知识补一个答案,
# 否则验收③观察到的会变成"模型没调工具",而不是"查表查不出来" —— 那是两件事。
_NO_HIT = (
    "知识库中没有找到与「{keyword}」相关的条目。"
    "请如实告诉用户没有查到,不要凭你自己的知识回答政策类问题,"
    "并建议用户转人工或换个说法再问。"
)


def build_knowledge_tools(session_factory: Callable[[], Session]) -> list[BaseTool]:
    """返回查知识库的工具。

    收的是**工厂**而不是 Session:工具经 @tool 的 ainvoke 跑在线程池里,
    SQLAlchemy 的 Session 非线程安全,多个工具并发时共用一个 Session 会出事。
    每个调用自己开一个,用完就关。
    """

    @tool
    def query_faq(keyword: str) -> str:
        """按关键词查常见问题。keyword 是用户问题里的核心词,例如「退货」「发票」。"""
        with session_factory() as session:
            rows = find_faq(session, keyword=keyword, limit=_MAX_ROWS)
            # 在这里就把文本拼完,不要留到 with 外面再读 row.category ——
            # Session 关闭后实例是 detached 的,现在能读是因为属性已加载;
            # 哪天 find_faq 里加一次 commit(expire_on_commit 一到期),这里就会
            # 变成 DetachedInstanceError,而且是线上才炸。
            lines = [f"[{row.category}] {row.question}:{row.answer}" for row in rows]

        if not lines:
            return _NO_HIT.format(keyword=keyword)
        return "\n".join(lines)

    return [query_faq]
