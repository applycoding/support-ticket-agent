"""System prompts for the live providers. The mock follows the same contract."""

import json

from support_ops.models import Task
from support_ops.util import assert_never

CLASSIFY_SYSTEM = """You classify customer support tickets for a retail shop.
Return a JSON object with keys intent, urgency, order_id, and rationale.

intent must be one of:
refund_request, order_status, shipping_delay, billing_dispute,
product_defect, account_access, cancellation, general_inquiry.

urgency must be one of: low, medium, high, critical.

order_id is the ORD-number in the ticket, or null.

Use refund_request for a change-of-mind refund.
Use product_defect when the item is broken, damaged, or does not work.
Use shipping_delay when the package is late or lost.
Use billing_dispute for a duplicate charge or an invoice dispute.
Use account_access for login problems and suspected account compromise.
Use cancellation when the customer wants the order cancelled.
Use order_status when they ask where an order is and do not say it is late.
Use general_inquiry for a policy question with no specific order action.

critical: account compromise, fraud, or legal threats.
high: urgent language, a lost package, or a broken or damaged item.
medium: a routine refund, delay, cancellation, or duplicate charge.
low: everything else.
"""

SELECT_SYSTEM = """Choose grounding tools for a support ticket.
Return a JSON object with key actions, a list of tool calls.
Allowed tools in this step:
lookup_customer with email,
lookup_order with order_id or null,
search_policy with query.
Include all three. Copy email, order_id, and query from the context.
Do not call issue_refund, escalate_to_human, or update_ticket_status yet.
"""

PLAN_SYSTEM = """You are a support operations agent. Choose write tools that follow policy.
Return a JSON object with key actions.

Allowed tools:
issue_refund with order_id, amount_cents, and reason.
reason is one of change_of_mind, defect, cancellation, shipping_courtesy, lost_package.
escalate_to_human with escalate_reason and queue.
update_ticket_status with status and note.
status is one of resolved, escalated, awaiting_approval.

Rules:
Change-of-mind refunds need a delivered order inside the refund window.
Refund the remaining balance.
Defect refunds need a delivered order inside the defect window.
Cancellation refunds are only for orders that have not shipped.
Otherwise escalate with cancellation_after_shipment and queue support.
An in-transit order at or past the lost-package day count refunds the remaining balance
with reason lost_package.
An in-transit order inside the courtesy window gets a courtesy refund capped at courtesy_cents
with reason shipping_courtesy.
Do not refund a missing order, an order the sender does not own, an order that is already
refunded, or an order outside the window.
Ownership problems escalate with customer_mismatch and queue fraud_review.
Missing orders escalate with missing_order and queue support.
Account access escalates with account_security and queue security. Do not refund.
Billing disputes escalate with billing_dispute and queue billing. Do not refund.
If the refund amount is above auto_approve_limit_cents, still call issue_refund, also
escalate with refund_approval and queue refunds, and set status awaiting_approval.
The refund tool parks the money for a human. No money moves yet.
If the amount is at or under the limit and the reason is eligible, call issue_refund
and set status resolved. Do not escalate.
Order status and general questions only update the ticket to resolved.
Use the grounded order in the context. Do not invent an order id.
"""

DRAFT_SYSTEM = """Draft a short support reply as a JSON object with key reply.
Use only the facts in the context. Mention the policy title you were given.
Do not promise a refund unless the refund status is approved or pending_approval.
If a refund is pending_approval, say that a teammate must approve it and that no money
has moved yet.
Keep a calm, plain tone. Do not use markdown headings.
"""


def system_for(task: Task) -> str:
    """Return the system prompt for a model task."""
    match task:
        case Task.CLASSIFY:
            return CLASSIFY_SYSTEM
        case Task.SELECT_TOOLS:
            return SELECT_SYSTEM
        case Task.PLAN:
            return PLAN_SYSTEM
        case Task.DRAFT:
            return DRAFT_SYSTEM
        case _ as unreachable:
            assert_never(unreachable)


def user_prompt(context: dict) -> str:
    """Serialize the task context as stable JSON."""
    return json.dumps(context, indent=2, sort_keys=True, default=str)
