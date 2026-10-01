import pytest
from pydantic import ValidationError


def test_unknown_action_is_rejected_before_offer():
    try:
        from mewhelp.ch05.schemas import AgentDecision
    except ImportError:
        pytest.fail("action contract missing")
    with pytest.raises(ValidationError):
        AgentDecision(reply_mode="answer", suggested_actions=["refund_money"])
