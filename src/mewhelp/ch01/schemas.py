"""结构化输出模型。

用枚举而不是裸字符串:把模型的选择限制在闭集里,准确率显著高于自由文本,
下游做统计和路由也不用处理同义词。字段的 description 会被送进 function
calling 的 schema,等于给模型的第二份说明。
"""

from enum import Enum

from pydantic import BaseModel, Field


class AfterSalesIntent(str, Enum):
    """用户的核心诉求类型。"""

    refund = "退款"
    return_goods = "退货"
    exchange = "换货"
    repair = "维修"
    reship = "补发"
    compensation = "补偿"
    consultation = "咨询"
    complaint = "投诉"
    other = "其他"


class ExpectedSolution(str, Enum):
    """用户希望怎么解决。"""

    full_refund = "全额退款"
    partial_refund = "部分退款"
    exchange = "换货"
    repair = "维修"
    reship = "补发"
    coupon = "优惠券补偿"
    explanation = "仅需解释"
    unspecified = "未提及"


class AfterSalesTicket(BaseModel):
    """从一段售后描述里抽取出的工单要素。"""

    order_id: str | None = Field(
        default=None,
        description="订单号。描述里没有出现就留空,不要推测或编造。",
    )
    intent: AfterSalesIntent = Field(
        description="用户的核心诉求类型,从枚举里选最贴近的一个。",
    )
    # 已知缺口(留作第 2 章的 schema 问题):有**显式诉求**却既不是要信息、也不在
    # 退款/换货/维修/补发/补偿这五类动作里时(如「我要投诉」),当前 8 个值没有归属;
    # 评估集保持现状,不为它新增取值。
    # 为什么不写进模型看得见的那两条说明(Prompt 的 expected_solution 一段 / 本字段的
    # description):实测写进去之后模型会把这类描述判成「未提及」—— #18/#19 从
    # 「仅需解释」翻转过去(schema 侧单独加这句话,#18 连续 5 次全翻)。那正是本章反复
    # 修掉的缺陷形状:「未提及」的定义是"通篇看不出诉求",拿它当"清单外兜底"的下场,
    # 就是下游把"用户明确要求处理"读成"用户没说"。要新的取值就动 schema,那是第 2 章的事。
    expected_solution: ExpectedSolution = Field(
        description=(
            "用户希望怎么解决。"
            "用户要的是**信息** —— 在问时间、问规则、问进度、问东西到哪了,"
            "或要我们解释/确认/查一下,而不是要我们做退款/换货/维修/补发/补偿这类动作时,"
            "填「仅需解释」;"
            "通篇看不出任何诉求(纯粹情绪、纯粹陈述,或信息太少判断不了),才填「未提及」。"
        ),
    )
    reason: str | None = Field(
        default=None,
        description="一句话概括原因,不超过 20 字。看不出原因就留空。",
    )
