"""Refund window and approval-limit boundaries."""

from pathlib import Path

from support_ops.config import make_settings
from support_ops.models import GateCode, Order, RefundReason
from support_ops.policy import evaluate_refund_policy


def _order(**overrides: object) -> Order:
    data: dict[str, object] = {
        "id": "ORD-X",
        "customer_id": "C",
        "sku": "SKU",
        "item_name": "Item",
        "total_cents": 20000,
        "refunded_cents": 0,
        "status": "delivered",
        "shipped_at": "2026-03-01",
        "delivered_at": "2026-04-05",
    }
    data.update(overrides)
    return Order.model_validate(data)


def test_change_of_mind_window_includes_day_30(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "unused.db")
    inside = _order(delivered_at="2026-03-11")
    outside = _order(delivered_at="2026-03-10")
    assert (
        evaluate_refund_policy(
            order=inside,
            customer_id="C",
            amount_cents=1000,
            reason=RefundReason.CHANGE_OF_MIND,
            settings=settings,
        )
        is GateCode.APPROVED_UNDER_LIMIT
    )
    assert (
        evaluate_refund_policy(
            order=outside,
            customer_id="C",
            amount_cents=1000,
            reason=RefundReason.CHANGE_OF_MIND,
            settings=settings,
        )
        is GateCode.OUTSIDE_WINDOW
    )


def test_auto_approve_limit_is_inclusive(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "unused.db")
    order = _order()
    assert (
        evaluate_refund_policy(
            order=order,
            customer_id="C",
            amount_cents=7500,
            reason=RefundReason.CHANGE_OF_MIND,
            settings=settings,
        )
        is GateCode.APPROVED_UNDER_LIMIT
    )
    assert (
        evaluate_refund_policy(
            order=order,
            customer_id="C",
            amount_cents=7501,
            reason=RefundReason.CHANGE_OF_MIND,
            settings=settings,
        )
        is GateCode.PENDING_APPROVAL
    )


def test_courtesy_and_lost_package_day_boundaries(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "unused.db")
    courtesy = {
        "status": "in_transit",
        "delivered_at": None,
    }
    assert (
        evaluate_refund_policy(
            order=_order(shipped_at="2026-04-03", **courtesy),
            customer_id="C",
            amount_cents=1500,
            reason=RefundReason.SHIPPING_COURTESY,
            settings=settings,
        )
        is GateCode.APPROVED_UNDER_LIMIT
    )
    assert (
        evaluate_refund_policy(
            order=_order(shipped_at="2026-04-04", **courtesy),
            customer_id="C",
            amount_cents=1500,
            reason=RefundReason.SHIPPING_COURTESY,
            settings=settings,
        )
        is GateCode.NOT_ELIGIBLE
    )
    assert (
        evaluate_refund_policy(
            order=_order(shipped_at="2026-03-27", **courtesy),
            customer_id="C",
            amount_cents=1000,
            reason=RefundReason.LOST_PACKAGE,
            settings=settings,
        )
        is GateCode.APPROVED_UNDER_LIMIT
    )
    assert (
        evaluate_refund_policy(
            order=_order(shipped_at="2026-03-28", **courtesy),
            customer_id="C",
            amount_cents=1000,
            reason=RefundReason.LOST_PACKAGE,
            settings=settings,
        )
        is GateCode.NOT_ELIGIBLE
    )
