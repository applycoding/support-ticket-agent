"""Reply text for the offline mock. Live models draft from the same facts."""

from support_ops.models import Intent, RefundStatus
from support_ops.util import assert_never, format_usd


def render_reply(context: dict) -> str:
    """Build a plain-text reply from tool results and the top policy passage."""
    customer = context.get("customer")
    name = "there"
    if customer and customer.get("name"):
        name = str(customer["name"]).split()[0]
    lines = [f"Hello {name},", "", _intent_line(context)]
    policies = context.get("policies") or []
    if policies:
        lines.append(_policy_line(policies[0]))
    note = str(context.get("decision_note") or "").strip()
    if note:
        lines.append(note)
    refund_line = _refund_line(context)
    if refund_line:
        lines.append(refund_line)
    if customer and customer.get("tier") == "premium":
        lines.append("Your account is on the premium tier.")
    lines.append("This note is from the support operations demo assistant.")
    return "\n".join(lines)


def _intent_line(context: dict) -> str:
    intent = Intent(context["classification"]["intent"])
    order = context.get("order")
    if order:
        item = (
            f" for order {order['id']} "
            f"({order['item_name']}, {format_usd(int(order['total_cents']))})"
        )
    else:
        item = ""
    match intent:
        case Intent.REFUND_REQUEST:
            return f"I reviewed your refund request{item}."
        case Intent.PRODUCT_DEFECT:
            return f"I reviewed the product issue{item}."
        case Intent.SHIPPING_DELAY:
            return f"I reviewed the shipment{item}."
        case Intent.ORDER_STATUS:
            return f"I looked up the latest status{item}."
        case Intent.CANCELLATION:
            return f"I reviewed the cancellation{item}."
        case Intent.BILLING_DISPUTE:
            return f"I reviewed the billing dispute{item}."
        case Intent.ACCOUNT_ACCESS:
            return "I reviewed the account access report."
        case Intent.GENERAL_INQUIRY:
            return "I reviewed your question against the support policy."
        case _ as unreachable:
            assert_never(unreachable)


def _policy_line(policy: dict) -> str:
    excerpt = " ".join(str(policy.get("text", "")).split())
    if len(excerpt) > 160:
        excerpt = excerpt[:157].rstrip() + "..."
    return f"Policy reference: {policy['title']}. {excerpt}"


def _refund_line(context: dict) -> str | None:
    refund = context.get("refund")
    if not refund:
        return None
    status = RefundStatus(refund["status"])
    amount = format_usd(int(refund["amount_cents"]))
    order_id = refund["order_id"]
    limit = format_usd(int(context["auto_approve_limit_cents"]))
    match status:
        case RefundStatus.APPROVED:
            return (
                f"We approved a refund of {amount} on order {order_id}. "
                f"The amount is within the {limit} automatic approval limit."
            )
        case RefundStatus.PENDING_APPROVAL:
            return (
                f"The refund of {amount} on order {order_id} is above the {limit} "
                "automatic limit, so a teammate has to approve it before any money moves. "
                "No money has moved yet."
            )
        case RefundStatus.DENIED:
            return f"I did not issue a refund on order {order_id}. Gate: {refund['gate_code']}."
        case _ as unreachable:
            assert_never(unreachable)
