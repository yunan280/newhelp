"""种子数据 —— 幂等,可重复跑。

为什么用 Python 而不是 .sql:它要能在两个方言上跑(SQLite 测试 / MySQL 演示),
而且测试要能**直接断言**「运费那条不含邮费」这件事。DDL 用 .sql 是因为
CLI 执行时的编码坑真实存在(见 sql/ch02-ddl.sql 的 SET NAMES);种子不走 CLI,
那个坑不适用,而可测性在这儿更重要。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Faq

# 12 条、6 个分类:退换货 2 / 物流 3 / 支付 2 / 发票 2 / 商品 2 / 售后 1。
#
# 「运费怎么计算」这一条的措辞是**设计的一部分**:它讲运送费用,但全文不含
# 「邮费」二字。验收③要展示的就是这个语义鸿沟 —— 见 tests/test_db_seed.py
# 里那条守门测试,别顺手把「邮费」写进去。
FAQ_SEED: list[tuple[str, str, str]] = [
    ("退货政策是什么", "签收后 7 天内,商品不影响二次销售可无理由退货。", "退换货"),
    ("换货怎么申请", "在订单详情页点「申请换货」,选好规格后寄回即可。", "退换货"),
    ("运费怎么计算", "单笔满 99 元包邮;不满 99 元按收货地收取 8 元起。", "物流"),
    ("多久能发货", "现货商品在付款后 48 小时内发出,预售商品以商品页标注为准。", "物流"),
    ("物流信息多久更新", "承运商通常每 4 小时同步一次,偏远地区可能更慢。", "物流"),
    ("支持哪些支付方式", "支持微信、支付宝、银联与货到付款。", "支付"),
    ("支付失败怎么办", "先确认余额与限额,仍失败可换一种方式,重复扣款会自动退回。", "支付"),
    ("发票怎么开", "下单时勾选「开具发票」并填抬头,随货寄出或开电子票。", "发票"),
    ("发票可以重开吗", "可以。在订单详情页提交重开申请,3 个工作日内处理。", "发票"),
    ("商品有质量问题怎么办", "拍照留证后在订单页申请售后,审核通过可退可换。", "商品"),
    ("商品尺码怎么选", "商品页有尺码对照表,拿不准可把身高体重发给客服。", "商品"),
    ("保修期是多久", "电子类商品保修 12 个月,人为损坏不在保修范围内。", "售后"),
]


def seed(session: Session) -> None:
    """幂等灌种子 —— 按 question 去重,已存在的不动。

    不按主键 upsert:faq 的主键是自增的,同一个问题在不同库里拿到的 id 不一样,
    按 question 去重才是跨库稳定的判据。
    """
    existing = set(session.scalars(select(Faq.question)).all())
    for question, answer, category in FAQ_SEED:
        if question in existing:
            continue
        session.add(Faq(question=question, answer=answer, category=category))


def main() -> None:
    """真机入口:`.venv/Scripts/python.exe -m mewhelp.db.seed`"""
    from .engine import SessionLocal

    with SessionLocal() as session:
        seed(session)
        session.commit()
    print("种子数据已就绪(faq 12 条)")


if __name__ == "__main__":
    main()
