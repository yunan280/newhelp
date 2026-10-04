from importlib import import_module

import pytest

from mewhelp.ch07.budget import ContextBudget
from mewhelp.ch07.config import BudgetProfile
from mewhelp.ch07.types import HistoryContext, SummarySegment
from mewhelp.db.models import Conversation, ConversationSummary, Message, MsgRole


def provenance():
    try:
        return import_module('mewhelp.ch07.provenance')
    except ModuleNotFoundError:
        pytest.fail('Ch07 summary references require real conversation-local source ranges')


def test_summary_order_source_is_verified_against_original_user_rows(session_factory):
    module = provenance()
    with session_factory.begin() as session:
        conv = Conversation(session_id='ref', user_id='alice', summary_upto_msg_id=14,
                            layer1_from_msg_id=14)
        session.add(conv)
        session.flush()
        cid = conv.id
        session.add_all([Message(id=10, conversation_id=cid, role=MsgRole.user,
                                content='订单1001查物流'),
                         Message(id=14, conversation_id=cid, role=MsgRole.assistant,
                                 content='尚未查到')])
        session.add(ConversationSummary(id=1, conversation_id=cid, seq=1, from_msg_id=10,
            upto_msg_id=14, content='订单1001查物流，仍未解决。'))
    budget = ContextBudget(0, 0, 10000, 10000, 7000, 3000, {}, BudgetProfile().fingerprint)
    history = HistoryContext(cid, 14, 14, '订单1001查物流，仍未解决。',
        (SummarySegment(1, 1, 10, 14, '订单1001查物流，仍未解决。'),), (), (), (), {}, budget)
    sources = module.reference_sources(history, user_id='alice', session_factory=session_factory)
    assert module.verify_reference(order_id='1001', source_id='summary-1', sources=sources)
    assert not module.verify_reference(order_id='1002', source_id='summary-1', sources=sources)
    assert module.reference_sources(history, user_id='bob', session_factory=session_factory) == []


def test_earliest_is_chosen_by_provenance_and_ambiguous_it_stays_unselected():
    module = provenance()
    sources = [{'order_id': '1002', 'message_id': 'mysql-24', 'from_msg_id': 20},
               {'order_id': '1001', 'message_id': 'summary-1', 'from_msg_id': 10}]
    assert module.chronological_reference('最开始那个订单后来怎么说', sources) == '1001'
    assert module.chronological_reference('不是第一单，是后一单的物流', sources) == '1002'
    assert module.chronological_reference('它后来怎样了', sources) is None


def test_recent_reference_uses_last_mention_of_repeated_order():
    sources = [{'order_id':'1001','from_msg_id':10}, {'order_id':'1002','from_msg_id':20},
               {'order_id':'1001','from_msg_id':30}]
    assert provenance().chronological_reference('刚才那单的物流呢', sources) == '1001'
    assert provenance().chronological_reference('最开始那单的物流呢', sources) == '1001'


def test_summary_source_proves_last_exact_identifier_occurrence(session_factory):
    with session_factory.begin() as session:
        conv = Conversation(session_id='repeated', user_id='alice')
        session.add(conv); session.flush(); cid = conv.id
        session.add_all([Message(id=i,conversation_id=cid,role=MsgRole.user,content=text)
            for i,text in [(10,'订单10012先查'),(20,'订单1001查'),(30,'订单1002查'),(40,'订单1001再查')]])
        session.add(Message(id=44,conversation_id=cid,role=MsgRole.assistant,content='已答'))
        session.add(ConversationSummary(id=1,conversation_id=cid,seq=1,from_msg_id=10,
            upto_msg_id=44,content='订单10012、订单1001、订单1002分别查询物流。'))
    budget = ContextBudget(0,0,10000,10000,7000,3000,{},BudgetProfile().fingerprint)
    history = HistoryContext(cid,44,44,'', (SummarySegment(1,1,10,44,
        '订单10012、订单1001、订单1002分别查询物流。'),),(),(),(),{},budget)
    sources = provenance().reference_sources(history,user_id='alice',session_factory=session_factory)
    chosen = next(s for s in sources if s['order_id']=='1001')
    assert chosen['from_msg_id'] == 20
    assert chosen['last_msg_id'] == 40
    assert provenance().chronological_reference('刚才那单', sources) == '1001'
