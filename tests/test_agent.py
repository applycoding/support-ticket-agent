"""End-to-end agent decisions on labeled tickets."""

import pytest

from support_ops.agent import SupportAgent
from support_ops.errors import RefundError
from support_ops.models import Intent, Urgency
from tests.helpers import load_ticket


def test_under_limit_refund_is_approved(agent: SupportAgent) -> None:
    result = agent.handle(load_ticket("TCK-1001"))
    assert result.intent == Intent.REFUND_REQUEST
    assert result.urgency == Urgency.MEDIUM
    assert result.refund_status == "approved"
    assert result.refund_amount_cents == 4200
    assert result.refund_id == "REF-0001"
    assert result.ticket_status == "resolved"
    assert result.escalated is False
    assert "issue_refund" in result.tools
    assert "escalate_to_human" not in result.tools
    assert "Hello Maya," in result.reply
    assert "We approved a refund of $42.00" in result.reply
    assert any(title in result.reply for title in result.policy_titles)
    order = agent.store.get_order("ORD-1001")
    assert order is not None
    assert order.refunded_cents == 4200


def test_over_limit_refund_stays_parked(agent: SupportAgent) -> None:
    result = agent.handle(load_ticket("TCK-1002"))
    assert result.refund_status == "pending_approval"
    assert result.refund_amount_cents == 24000
    assert result.ticket_status == "awaiting_approval"
    assert result.escalated is True
    assert "No money has moved yet." in result.reply
    order = agent.store.get_order("ORD-1002")
    assert order is not None
    assert order.refunded_cents == 0
    approval = agent.store.get_approval(result.refund_id or "")
    assert approval is not None
    assert approval.status == "pending"


def test_account_access_escalates_without_a_refund(agent: SupportAgent) -> None:
    result = agent.handle(load_ticket("TCK-1005"))
    assert result.intent == Intent.ACCOUNT_ACCESS
    assert result.urgency == Urgency.CRITICAL
    assert result.refund_status is None
    assert result.escalated is True
    assert result.ticket_status == "escalated"
    assert "issue_refund" not in result.tools
    assert agent.store.list_refunds_for_ticket("TCK-1005") == []


def test_outside_window_does_not_call_the_refund_tool(agent: SupportAgent) -> None:
    result = agent.handle(load_ticket("TCK-1004"))
    assert result.refund_status is None
    assert "issue_refund" not in result.tools
    order = agent.store.get_order("ORD-1004")
    assert order is not None
    assert order.refunded_cents == 0


def test_mismatched_sender_does_not_refund(agent: SupportAgent) -> None:
    result = agent.handle(load_ticket("TCK-1015"))
    assert result.escalated is True
    assert result.refund_id is None
    order = agent.store.get_order("ORD-1001")
    assert order is not None
    assert order.refunded_cents == 0


def test_refund_ids_are_sequential(agent: SupportAgent) -> None:
    first = agent.handle(load_ticket("TCK-1001"))
    second = agent.handle(load_ticket("TCK-1002"))
    assert first.refund_id == "REF-0001"
    assert second.refund_id == "REF-0002"


def test_audit_sequence_is_contiguous(agent: SupportAgent) -> None:
    result = agent.handle(load_ticket("TCK-1001"))
    events = agent.store.list_audit("TCK-1001")
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert events[0].event_type == "ticket_received"
    assert events[-1].event_type == "completed"
    assert result.audit_event_types == [event.event_type for event in events]
    required = {
        "ticket_received",
        "classification",
        "tool_call",
        "tool_result",
        "reply_drafted",
        "completed",
    }
    assert required <= set(result.audit_event_types)
    assert result.audit_event_types.count("tool_call") == result.audit_event_types.count(
        "tool_result"
    )


def test_human_approval_moves_money_once(agent: SupportAgent) -> None:
    result = agent.handle(load_ticket("TCK-1002"))
    assert result.refund_id is not None
    decided = agent.decide_refund(result.refund_id, "approve", "A. Bom")
    assert decided.status == "approved"
    assert decided.gate_code == "human_approved"
    order = agent.store.get_order("ORD-1002")
    assert order is not None
    assert order.refunded_cents == 24000
    stored = agent.store.get_ticket("TCK-1002")
    assert stored is not None
    assert stored.status == "resolved"
    events = agent.store.list_audit("TCK-1002")
    assert any(event.event_type == "approval_decision" for event in events)
    with pytest.raises(RefundError):
        agent.decide_refund(result.refund_id, "approve", "A. Bom")
