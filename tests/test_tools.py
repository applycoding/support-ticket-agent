"""Refund tool gate against the seeded orders."""

from support_ops.agent import SupportAgent
from support_ops.models import OrderStatus, RefundReason
from support_ops.util import age_days


def _refund(
    agent: SupportAgent,
    *,
    order_id: str,
    customer_id: str,
    amount_cents: int,
    reason: RefundReason,
) -> object:
    return agent.tools.issue_refund(
        ticket_id="T-tool",
        order_id=order_id,
        customer_id=customer_id,
        amount_cents=amount_cents,
        reason=reason,
    )


def test_seed_dates_match_the_demo_clock(agent: SupportAgent) -> None:
    today = agent.settings.today
    jordan = agent.store.get_order("ORD-1006")
    dana = agent.store.get_order("ORD-1012")
    sam = agent.store.get_order("ORD-1004")
    assert jordan is not None and dana is not None and sam is not None
    assert age_days(jordan.shipped_at, today) == 21
    assert age_days(dana.shipped_at, today) == 9
    assert age_days(sam.delivered_at, today) == 68
    customers = {customer.id for customer in agent.store.list_customers()}
    for order in agent.store.list_orders():
        assert order.customer_id in customers
        assert order.refunded_cents <= order.total_cents


def test_under_limit_approves_and_over_limit_parks(agent: SupportAgent) -> None:
    approved = _refund(
        agent,
        order_id="ORD-1002",
        customer_id="C-002",
        amount_cents=7500,
        reason=RefundReason.CHANGE_OF_MIND,
    )
    assert approved.status == "approved"
    assert agent.store.get_order("ORD-1002").refunded_cents == 7500


def test_one_cent_over_does_not_move_money(agent: SupportAgent) -> None:
    parked = _refund(
        agent,
        order_id="ORD-1002",
        customer_id="C-002",
        amount_cents=7501,
        reason=RefundReason.CHANGE_OF_MIND,
    )
    assert parked.status == "pending_approval"
    assert parked.gate_code == "pending_approval"
    assert agent.store.get_order("ORD-1002").refunded_cents == 0
    approval = agent.store.get_approval(parked.id)
    assert approval is not None
    assert approval.status == "pending"


def test_amount_above_remaining_balance_is_denied(agent: SupportAgent) -> None:
    denied = _refund(
        agent,
        order_id="ORD-1002",
        customer_id="C-002",
        amount_cents=24001,
        reason=RefundReason.CHANGE_OF_MIND,
    )
    assert denied.status == "denied"
    assert denied.gate_code == "exceeds_order_total"
    assert agent.store.get_order("ORD-1002").refunded_cents == 0


def test_window_defect_and_ownership_gates(agent: SupportAgent) -> None:
    outside = _refund(
        agent,
        order_id="ORD-1004",
        customer_id="C-004",
        amount_cents=1000,
        reason=RefundReason.CHANGE_OF_MIND,
    )
    assert outside.gate_code == "outside_window"
    defect = _refund(
        agent,
        order_id="ORD-1004",
        customer_id="C-004",
        amount_cents=1000,
        reason=RefundReason.DEFECT,
    )
    assert defect.status == "approved"
    mismatch = _refund(
        agent,
        order_id="ORD-1001",
        customer_id="C-002",
        amount_cents=1000,
        reason=RefundReason.CHANGE_OF_MIND,
    )
    assert mismatch.gate_code == "customer_mismatch"
    already = _refund(
        agent,
        order_id="ORD-1003",
        customer_id="C-003",
        amount_cents=100,
        reason=RefundReason.CHANGE_OF_MIND,
    )
    assert already.gate_code == "already_refunded"


def test_cancellation_shipping_and_duplicate_pending(agent: SupportAgent) -> None:
    cancelled = _refund(
        agent,
        order_id="ORD-1009",
        customer_id="C-009",
        amount_cents=1500,
        reason=RefundReason.CANCELLATION,
    )
    assert cancelled.status == "approved"
    shipped = _refund(
        agent,
        order_id="ORD-1013",
        customer_id="C-013",
        amount_cents=1000,
        reason=RefundReason.CANCELLATION,
    )
    assert shipped.gate_code == "not_eligible"
    courtesy = _refund(
        agent,
        order_id="ORD-1012",
        customer_id="C-012",
        amount_cents=1500,
        reason=RefundReason.SHIPPING_COURTESY,
    )
    assert courtesy.status == "approved"
    too_much_courtesy = _refund(
        agent,
        order_id="ORD-1007",
        customer_id="C-007",
        amount_cents=2000,
        reason=RefundReason.SHIPPING_COURTESY,
    )
    assert too_much_courtesy.gate_code == "not_eligible"
    first = _refund(
        agent,
        order_id="ORD-1006",
        customer_id="C-006",
        amount_cents=12000,
        reason=RefundReason.LOST_PACKAGE,
    )
    second = _refund(
        agent,
        order_id="ORD-1006",
        customer_id="C-006",
        amount_cents=12000,
        reason=RefundReason.LOST_PACKAGE,
    )
    assert first.status == "pending_approval"
    assert second.gate_code == "duplicate_request"
    order = agent.store.get_order("ORD-1006")
    assert order is not None
    assert order.refunded_cents == 0
    assert order.status is not OrderStatus.REFUNDED
