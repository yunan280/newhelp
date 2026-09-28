"""种子数据 —— 幂等,且验收③的漏召回是**设计出来的**(spec §8)。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.db.models import Faq
from mewhelp.db.repository import find_faq
from mewhelp.db.seed import FAQ_SEED, seed


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_seed_is_idempotent(session):
    """连跑两次行数不变 —— 不然每次起服务都灌一遍,FAQ 表会一直涨。"""
    seed(session)
    session.commit()
    first = session.query(Faq).count()

    seed(session)
    session.commit()

    assert session.query(Faq).count() == first


def test_seed_has_twelve_rows(session):
    seed(session)
    session.commit()
    assert session.query(Faq).count() == 12


def test_seed_covers_six_categories(session):
    """分类覆盖:退换货 / 物流 / 支付 / 发票 / 商品 / 售后。"""
    seed(session)
    session.commit()
    categories = {row.category for row in session.query(Faq).all()}
    assert categories == {"退换货", "物流", "支付", "发票", "商品", "售后"}


def test_the_shipping_fee_row_never_says_the_word_the_user_will_use(session):
    """验收③的**全部机关**在这一条上。

    FAQ 里**有**一条讲运送费用的知识,但措辞是「运费怎么计算」,全文不含「邮费」。
    用户问「邮费是多少」时模型抽出的关键词是「邮费」→ LIKE 0 行命中 → 漏召回。

    若哪天有人"顺手"把「邮费」写进这条,验收③ 就不再是漏召回,而是命中 ——
    那条验收会静默失效,ch03 也失去了可信的对照基线。这条测试就是拦它的。
    """
    seed(session)
    session.commit()

    row = session.query(Faq).filter(Faq.question.like("%运费%")).one()
    assert "邮费" not in row.question
    assert "邮费" not in row.answer

    # 而且全表都不该出现「邮费」
    for r in session.query(Faq).all():
        assert "邮费" not in r.question
        assert "邮费" not in r.answer


def test_the_return_policy_row_does_contain_the_word_for_acceptance_two(session):
    """验收②走**同一段代码**:「退货政策是什么」→ 关键词「退货」→ 命中。

    与上一条成对:同一个工具,一问就中、一问就漏 —— 这才说明漏的是检索能力,
    不是工具坏了。
    """
    seed(session)
    session.commit()

    assert session.query(Faq).filter(Faq.question.like("%退货%")).count() >= 1


def test_every_seed_row_is_non_empty():
    for question, answer, category in FAQ_SEED:
        assert question.strip() and answer.strip() and category.strip()


def test_seed_rows_have_unique_questions():
    questions = [q for q, _, _ in FAQ_SEED]
    assert len(questions) == len(set(questions))


def test_the_acceptance_pair_holds_on_the_real_seed(session):
    """把 T5 的 `find_faq` 与这一份种子接起来 —— 验收②③ 的**组合**才是要交付的行为。

    上面两条各自只钉一半:一条只钉「数据里没有『邮费』」,T5 那条只钉机制(用的是
    合成 fixture)。数据与检索各自正确、组合起来却答错,是可能的 —— 比如日后有人
    给 `find_faq` 加同义词表,把「邮费」改写成「运费」,上面两条都还是绿的。
    这条是那个改动的拦路测试。
    """
    seed(session)
    session.commit()

    # 验收③:换个词问同一件事,查不到 —— 刻意的漏召回,ch03 的对照基线
    assert [r.question for r in find_faq(session, keyword="邮费")] == []
    # 验收②:同一段代码、同一个工具,该中的就中
    assert [r.question for r in find_faq(session, keyword="退货")] == ["退货政策是什么"]
