"""Tool implementations. The refund tool enforces the policy gate."""

from support_ops.config import Settings
from support_ops.models import (
    Customer,
    EscalationRecord,
    GateCode,
    Order,
    RefundReason,
    RefundRecord,
    RefundStatus,
    StoredTicket,
)
from support_ops.policy import evaluate_refund_policy
from support_ops.retrieval import Passage, PolicyRetriever
from support_ops.store import Store


class Toolbox:
    """CRM lookups, policy search, refunds, escalation, and ticket updates."""

    def __init__(self, store: Store, settings: Settings, retriever: PolicyRetriever) -> None:
        self.store = store
        self.settings = settings
        self.retriever = retriever

    def lookup_customer(self, email: str) -> Customer | None:
        return self.store.get_customer_by_email(email)

    def lookup_order(self, order_id: str | None, fallback_email: str) -> Order | None:
        if order_id:
            return self.store.get_order(order_id)
        customer = self.store.get_customer_by_email(fallback_email)
        if customer is None:
            return None
        return self.store.latest_order_for_customer(customer.id)

    def search_policy(self, query: str) -> list[Passage]:
        return self.retriever.search(query)

    def update_ticket(self, ticket_id: str, status: str, note: str) -> StoredTicket:
        return self.store.update_ticket(ticket_id, status=status, note=note)

    def escalate(self, *, ticket_id: str, reason: str, queue: str) -> EscalationRecord:
        return self.store.create_escalation(ticket_id=ticket_id, reason=reason, queue=queue)

    def issue_refund(
        self,
        *,
        ticket_id: str,
        order_id: str,
        customer_id: str | None,
        amount_cents: int,
        reason: RefundReason,
    ) -> RefundRecord:
        """Apply the policy gate inside one transaction so the balance cannot drift."""
        with self.store.transaction():
            existing = self.store.pending_refund_for_order(order_id)
            if existing is not None:
                return self.store.insert_refund(
                    ticket_id=ticket_id,
                    order_id=order_id,
                    amount_cents=amount_cents,
                    reason=reason.value,
                    status=RefundStatus.DENIED.value,
                    gate_code=GateCode.DUPLICATE_REQUEST.value,
                    apply_balance=False,
                )
            order = self.store.get_order(order_id)
            gate = evaluate_refund_policy(
                order=order,
                customer_id=customer_id,
                amount_cents=amount_cents,
                reason=reason,
                settings=self.settings,
            )
            if gate is GateCode.APPROVED_UNDER_LIMIT:
                return self.store.insert_refund(
                    ticket_id=ticket_id,
                    order_id=order.id if order is not None else order_id,
                    amount_cents=amount_cents,
                    reason=reason.value,
                    status=RefundStatus.APPROVED.value,
                    gate_code=gate.value,
                    apply_balance=True,
                )
            if gate is GateCode.PENDING_APPROVAL:
                return self.store.insert_refund(
                    ticket_id=ticket_id,
                    order_id=order.id if order is not None else order_id,
                    amount_cents=amount_cents,
                    reason=reason.value,
                    status=RefundStatus.PENDING_APPROVAL.value,
                    gate_code=gate.value,
                    apply_balance=False,
                )
            return self.store.insert_refund(
                ticket_id=ticket_id,
                order_id=order.id if order is not None else order_id,
                amount_cents=amount_cents,
                reason=reason.value,
                status=RefundStatus.DENIED.value,
                gate_code=gate.value,
                apply_balance=False,
            )
