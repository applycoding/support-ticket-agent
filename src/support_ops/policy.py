"""Refund policy shared by the tool gate and the offline planner."""

from support_ops.config import Settings
from support_ops.models import GateCode, Order, OrderStatus, RefundReason
from support_ops.util import age_days, assert_never


def evaluate_refund_policy(
    *,
    order: Order | None,
    customer_id: str | None,
    amount_cents: int,
    reason: RefundReason,
    settings: Settings,
) -> GateCode:
    """Decide whether a refund is approved, parked, or refused.

    Amounts above the auto-approve limit are parked. The balance does not change
    until a person approves that row.
    """
    if order is None:
        return GateCode.ORDER_NOT_FOUND
    if customer_id is None or order.customer_id != customer_id:
        return GateCode.CUSTOMER_MISMATCH
    if amount_cents <= 0:
        return GateCode.INVALID_AMOUNT
    remaining = order.total_cents - order.refunded_cents
    if remaining <= 0:
        return GateCode.ALREADY_REFUNDED
    if amount_cents > remaining:
        return GateCode.EXCEEDS_ORDER_TOTAL
    reason_gate = _reason_gate(order, reason, amount_cents, settings)
    if reason_gate is not None:
        return reason_gate
    if amount_cents > settings.auto_approve_limit_cents:
        return GateCode.PENDING_APPROVAL
    return GateCode.APPROVED_UNDER_LIMIT


def customer_visible_note(gate: GateCode) -> str:
    """Short note stored on the ticket for a gate result."""
    match gate:
        case GateCode.APPROVED_UNDER_LIMIT:
            return "I approved the refund under the automatic limit."
        case GateCode.PENDING_APPROVAL:
            return "I parked the refund for a teammate because it is above the automatic limit."
        case GateCode.ORDER_NOT_FOUND:
            return "I could not find that order, so no refund was issued."
        case GateCode.CUSTOMER_MISMATCH:
            return "The order does not belong to the sender, so I did not refund it."
        case GateCode.ALREADY_REFUNDED:
            return "This order has already been refunded, so no additional refund was issued."
        case GateCode.EXCEEDS_ORDER_TOTAL:
            return "The requested amount is above the remaining balance, so no refund was issued."
        case GateCode.OUTSIDE_WINDOW:
            return "The order is outside the refund window, so no refund was issued."
        case GateCode.NOT_ELIGIBLE:
            return "The order is not eligible for that refund, so no money was moved."
        case GateCode.INVALID_AMOUNT:
            return "The refund amount is not valid, so no money was moved."
        case GateCode.DUPLICATE_REQUEST:
            return "A refund for this order is already waiting for approval."
        case GateCode.HUMAN_APPROVED:
            return "A teammate approved the refund."
        case GateCode.HUMAN_DENIED:
            return "A teammate denied the refund."
        case _ as unreachable:
            assert_never(unreachable)


def _reason_gate(
    order: Order,
    reason: RefundReason,
    amount_cents: int,
    settings: Settings,
) -> GateCode | None:
    match reason:
        case RefundReason.CHANGE_OF_MIND:
            return _delivered_window(order, settings.refund_window_days, settings)
        case RefundReason.DEFECT:
            return _delivered_window(order, settings.defect_window_days, settings)
        case RefundReason.CANCELLATION:
            if order.status is not OrderStatus.NOT_SHIPPED:
                return GateCode.NOT_ELIGIBLE
            return None
        case RefundReason.SHIPPING_COURTESY:
            return _shipping_courtesy_gate(order, amount_cents, settings)
        case RefundReason.LOST_PACKAGE:
            return _lost_package_gate(order, settings)
        case _ as unreachable:
            assert_never(unreachable)


def _delivered_window(order: Order, window_days: int, settings: Settings) -> GateCode | None:
    if order.status is not OrderStatus.DELIVERED:
        return GateCode.NOT_ELIGIBLE
    age = age_days(order.delivered_at, settings.today)
    if age is None or age > window_days:
        return GateCode.OUTSIDE_WINDOW
    return None


def _shipping_courtesy_gate(order: Order, amount_cents: int, settings: Settings) -> GateCode | None:
    if order.status is not OrderStatus.IN_TRANSIT:
        return GateCode.NOT_ELIGIBLE
    age = age_days(order.shipped_at, settings.today)
    too_soon = age is None or age < settings.shipping_courtesy_after_days
    too_late = age is not None and age >= settings.shipping_lost_after_days
    if too_soon or too_late or amount_cents > settings.courtesy_cents:
        return GateCode.NOT_ELIGIBLE
    return None


def _lost_package_gate(order: Order, settings: Settings) -> GateCode | None:
    if order.status is not OrderStatus.IN_TRANSIT:
        return GateCode.NOT_ELIGIBLE
    age = age_days(order.shipped_at, settings.today)
    if age is None or age < settings.shipping_lost_after_days:
        return GateCode.NOT_ELIGIBLE
    return None
