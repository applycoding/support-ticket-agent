"""Deterministic action planner used by the offline mock provider.

Live providers receive the same rules in the plan prompt and return the same JSON shape.
The refund tool runs the policy gate again before any money moves.
"""

import re

from support_ops.config import Settings
from support_ops.models import (
    EscalationReason,
    GateCode,
    Intent,
    Order,
    OrderStatus,
    RefundReason,
    TicketStatus,
)
from support_ops.policy import customer_visible_note, evaluate_refund_policy
from support_ops.util import age_days, assert_never

_LOST_RE = re.compile(r"\blost\b", re.IGNORECASE)

_QUEUE_FOR = {
    EscalationReason.ACCOUNT_SECURITY: "security",
    EscalationReason.BILLING_DISPUTE: "billing",
    EscalationReason.REFUND_APPROVAL: "refunds",
    EscalationReason.CUSTOMER_MISMATCH: "fraud_review",
    EscalationReason.MISSING_ORDER: "support",
    EscalationReason.CANCELLATION_AFTER_SHIPMENT: "support",
    EscalationReason.SHIPPING_INVESTIGATION: "shipping",
}

_OWNER_NOTES = {
    EscalationReason.MISSING_ORDER: (
        "I could not find an order for this customer, so I escalated the ticket."
    ),
    EscalationReason.CUSTOMER_MISMATCH: (
        "The order does not belong to the sender, so I escalated it for review "
        "and did not refund it."
    ),
}


def select_grounding_actions(context: dict) -> list[dict]:
    """Ask for the three read tools. The agent executes them before planning writes."""
    ticket = context["ticket"]
    classification = context["classification"]
    return [
        {"tool": "lookup_customer", "email": ticket["from_email"]},
        {"tool": "lookup_order", "order_id": classification.get("order_id")},
        {"tool": "search_policy", "query": context["query"]},
    ]


def plan_actions(context: dict, settings: Settings) -> list[dict]:
    """Choose refund, escalate, and status tools from grounded facts."""
    ticket = context["ticket"]
    classification = context["classification"]
    customer = _customer(context)
    order = _order(context)
    intent = Intent(classification["intent"])
    match intent:
        case Intent.ACCOUNT_ACCESS:
            return _escalation(
                EscalationReason.ACCOUNT_SECURITY,
                "Account security issues go to a specialist. I did not change any orders.",
            )
        case Intent.BILLING_DISPUTE:
            return _escalation(
                EscalationReason.BILLING_DISPUTE,
                "Billing disputes need a specialist review. I did not issue an automatic refund.",
            )
        case Intent.ORDER_STATUS:
            return _plan_order_status(customer, order)
        case Intent.GENERAL_INQUIRY:
            return [
                _status(
                    TicketStatus.RESOLVED,
                    "I answered from the policy knowledge base and did not change the order.",
                )
            ]
        case Intent.CANCELLATION:
            return _plan_cancellation(customer, order, settings)
        case Intent.SHIPPING_DELAY:
            return _plan_shipping(ticket, customer, order, settings)
        case Intent.REFUND_REQUEST:
            return _plan_refund(customer, order, RefundReason.CHANGE_OF_MIND, settings)
        case Intent.PRODUCT_DEFECT:
            return _plan_refund(customer, order, RefundReason.DEFECT, settings)
        case _ as unreachable:
            assert_never(unreachable)


def _plan_order_status(customer: dict | None, order: Order | None) -> list[dict]:
    problem = _owner_problem(customer, order)
    if problem is EscalationReason.CUSTOMER_MISMATCH:
        return _escalation(problem, _OWNER_NOTES[problem])
    return [_status(TicketStatus.RESOLVED, _order_status_note(order))]


def _plan_cancellation(
    customer: dict | None,
    order: Order | None,
    settings: Settings,
) -> list[dict]:
    problem = _owner_problem(customer, order)
    if problem is not None:
        return _escalation(problem, _OWNER_NOTES[problem])
    assert order is not None
    assert customer is not None
    if order.status is not OrderStatus.NOT_SHIPPED:
        return _escalation(
            EscalationReason.CANCELLATION_AFTER_SHIPMENT,
            "The order has already shipped, so I cannot cancel it automatically.",
        )
    remaining = order.total_cents - order.refunded_cents
    return _plan_money(order, str(customer["id"]), remaining, RefundReason.CANCELLATION, settings)


def _plan_shipping(
    ticket: dict,
    customer: dict | None,
    order: Order | None,
    settings: Settings,
) -> list[dict]:
    problem = _owner_problem(customer, order)
    if problem is not None:
        return _escalation(problem, _OWNER_NOTES[problem])
    assert order is not None
    match order.status:
        case OrderStatus.DELIVERED:
            return [
                _status(
                    TicketStatus.RESOLVED,
                    "The order is already delivered, so I did not issue a shipping refund.",
                )
            ]
        case OrderStatus.REFUNDED:
            return [
                _status(TicketStatus.RESOLVED, customer_visible_note(GateCode.ALREADY_REFUNDED))
            ]
        case OrderStatus.NOT_SHIPPED:
            return [
                _status(
                    TicketStatus.RESOLVED,
                    "The order has not shipped yet, so this is not a delay refund.",
                )
            ]
        case OrderStatus.IN_TRANSIT:
            assert customer is not None
            return _plan_in_transit(ticket, str(customer["id"]), order, settings)
        case _ as unreachable:
            assert_never(unreachable)


def _plan_in_transit(
    ticket: dict, customer_id: str, order: Order, settings: Settings
) -> list[dict]:
    remaining = order.total_cents - order.refunded_cents
    if remaining <= 0:
        return [_status(TicketStatus.RESOLVED, customer_visible_note(GateCode.ALREADY_REFUNDED))]
    days = age_days(order.shipped_at, settings.today)
    text = f"{ticket['subject']}\n{ticket['body']}"
    lost_claim = _LOST_RE.search(text) is not None
    if lost_claim and (days is None or days < settings.shipping_lost_after_days):
        return _escalation(
            EscalationReason.SHIPPING_INVESTIGATION,
            "This lost-package claim is too early to refund, so I escalated it.",
        )
    if days is not None and days >= settings.shipping_lost_after_days:
        return _plan_money(order, customer_id, remaining, RefundReason.LOST_PACKAGE, settings)
    if days is not None and days >= settings.shipping_courtesy_after_days:
        amount = min(settings.courtesy_cents, remaining)
        return _plan_money(order, customer_id, amount, RefundReason.SHIPPING_COURTESY, settings)
    return [
        _status(
            TicketStatus.RESOLVED,
            "The shipment is still inside the normal transit window, so I did not issue a refund.",
        )
    ]


def _plan_refund(
    customer: dict | None,
    order: Order | None,
    reason: RefundReason,
    settings: Settings,
) -> list[dict]:
    problem = _owner_problem(customer, order)
    if problem is not None:
        return _escalation(problem, _OWNER_NOTES[problem])
    assert order is not None
    assert customer is not None
    remaining = order.total_cents - order.refunded_cents
    return _plan_money(order, str(customer["id"]), remaining, reason, settings)


def _plan_money(
    order: Order,
    customer_id: str,
    amount_cents: int,
    reason: RefundReason,
    settings: Settings,
) -> list[dict]:
    gate = evaluate_refund_policy(
        order=order,
        customer_id=customer_id,
        amount_cents=amount_cents,
        reason=reason,
        settings=settings,
    )
    match gate:
        case GateCode.APPROVED_UNDER_LIMIT:
            return [
                _refund(order.id, amount_cents, reason),
                _status(TicketStatus.RESOLVED, customer_visible_note(gate)),
            ]
        case GateCode.PENDING_APPROVAL:
            note = customer_visible_note(gate)
            return [
                _refund(order.id, amount_cents, reason),
                _escalate(EscalationReason.REFUND_APPROVAL, note),
                _status(TicketStatus.AWAITING_APPROVAL, note),
            ]
        case GateCode.CUSTOMER_MISMATCH:
            return _escalation(
                EscalationReason.CUSTOMER_MISMATCH,
                _OWNER_NOTES[EscalationReason.CUSTOMER_MISMATCH],
            )
        case GateCode.ORDER_NOT_FOUND:
            return _escalation(
                EscalationReason.MISSING_ORDER,
                _OWNER_NOTES[EscalationReason.MISSING_ORDER],
            )
        case (
            GateCode.ALREADY_REFUNDED
            | GateCode.EXCEEDS_ORDER_TOTAL
            | GateCode.OUTSIDE_WINDOW
            | GateCode.NOT_ELIGIBLE
            | GateCode.INVALID_AMOUNT
            | GateCode.DUPLICATE_REQUEST
            | GateCode.HUMAN_APPROVED
            | GateCode.HUMAN_DENIED
        ):
            return [_status(TicketStatus.RESOLVED, customer_visible_note(gate))]
        case _ as unreachable:
            assert_never(unreachable)


def _owner_problem(customer: dict | None, order: Order | None) -> EscalationReason | None:
    if customer is None and order is not None:
        return EscalationReason.CUSTOMER_MISMATCH
    if customer is None or order is None:
        return EscalationReason.MISSING_ORDER
    if order.customer_id != customer["id"]:
        return EscalationReason.CUSTOMER_MISMATCH
    return None


def _order_status_note(order: Order | None) -> str:
    if order is None:
        return "I could not find an order for this request."
    parts = [f"Order {order.id} ({order.item_name}) is {order.status.value}."]
    if order.shipped_at:
        parts.append(f"It shipped on {order.shipped_at}.")
    if order.delivered_at:
        parts.append(f"It was delivered on {order.delivered_at}.")
    return " ".join(parts)


def _customer(context: dict) -> dict | None:
    raw = context.get("customer")
    if raw is None:
        return None
    return dict(raw)


def _order(context: dict) -> Order | None:
    raw = context.get("order")
    if raw is None:
        return None
    return Order.model_validate(raw)


def _escalation(reason: EscalationReason, note: str) -> list[dict]:
    return [
        _escalate(reason, note),
        _status(TicketStatus.ESCALATED, note),
    ]


def _escalate(reason: EscalationReason, note: str) -> dict:
    return {
        "tool": "escalate_to_human",
        "escalate_reason": reason.value,
        "queue": _QUEUE_FOR[reason],
        "note": note,
    }


def _refund(order_id: str, amount_cents: int, reason: RefundReason) -> dict:
    return {
        "tool": "issue_refund",
        "order_id": order_id,
        "amount_cents": amount_cents,
        "reason": reason.value,
    }


def _status(status: TicketStatus, note: str) -> dict:
    return {"tool": "update_ticket_status", "status": status.value, "note": note}
