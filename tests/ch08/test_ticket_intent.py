import pytest


@pytest.mark.parametrize('question', ['帮我建个工单', '请创建售后工单，键盘坏了', '我要建一个工单', '可以帮我开个工单吗'])
def test_explicit_ticket_requests(question):
    from mewhelp.ch08.ticket_intent import ticket_request_patch
    assert ticket_request_patch(question, 'm1', None)['explicit_request']


@pytest.mark.parametrize('question', ['不要帮我建工单', '如果键盘坏了就建工单', '客服说“帮我建个工单”是什么意思', '键盘坏了，太生气了', '你们可以建工单吗', '我不想建工单', '查一下物流'])
def test_negative_hypothetical_quote_or_ordinary_complaint(question):
    from mewhelp.ch08.ticket_intent import ticket_request_patch
    assert not ticket_request_patch(question, 'm1', None)['explicit_request']


def test_missing_problem_then_real_user_supply_and_withdrawal():
    from mewhelp.ch08.ticket_intent import ticket_request_patch, validate_ticket_draft
    evidence = ticket_request_patch('帮我建个工单', 'm1', None)
    args = {'description': '键盘坏了', 'ticket_type': '售后'}
    assert validate_ticket_draft(args, evidence)
    evidence = ticket_request_patch('键盘坏了', 'm2', evidence)
    assert not validate_ticket_draft(args, evidence)
    assert validate_ticket_draft({**args, 'description': '键盘冒烟了'}, evidence)
    assert validate_ticket_draft({'description': '帮我建个工单', 'ticket_type': '售后'}, evidence)
    assert validate_ticket_draft({**args, 'ticket_type': '投诉'}, evidence)
    cancelled = ticket_request_patch('算了，不建了', 'm3', evidence)
    assert not cancelled['explicit_request']
    assert validate_ticket_draft(args, cancelled)


def test_unrelated_new_message_clears_pending_evidence():
    from mewhelp.ch08.ticket_intent import ticket_request_patch
    previous = ticket_request_patch('帮我建个工单', 'm1', None)
    assert not ticket_request_patch('查订单1001的物流', 'm2', previous)['explicit_request']
