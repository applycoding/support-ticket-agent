"""Support operations agent: classify, ground, act, draft, and audit."""

from collections.abc import Callable

from pydantic import ValidationError

from support_ops.config import Settings
from support_ops.errors import AgentError, RefundError
from support_ops.llm import LLMClient, build_llm
from support_ops.models import (
    ALLOWED_QUEUES,
    ActionPlan,
    ActionTool,
    AgentResult,
    Classification,
    Customer,
    EscalationReason,
    EscalationRecord,
    HumanDecision,
    Order,
    PlannedAction,
    RefundReason,
    RefundRecord,
    RefundStatus,
    StoredTicket,
    Task,
    Ticket,
    TicketStatus,
)
from support_ops.prompts import system_for, user_prompt
from support_ops.retrieval import Passage, PolicyRetriever
from support_ops.store import Store
from support_ops.tools import Toolbox
from support_ops.util import assert_never, extract_order_id, jsonable

_MAX_ACTIONS = 8


class SupportAgent:
    """Run one ticket through the support operations loop."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.retriever = PolicyRetriever(settings.policy_dir)
        self.llm: LLMClient = build_llm(settings)
        self.tools = Toolbox(self.store, settings, self.retriever)

    @property
    def llm_name(self) -> str:
        return self.llm.name

    def handle(self, ticket: Ticket) -> AgentResult:
        """Classify the ticket, call tools, and draft a reply."""
        run_id = f"run-{ticket.id}"
        tools: list[str] = []
        self.store.upsert_ticket(ticket)
        self._audit(
            run_id,
            ticket.id,
            "ticket_received",
            {"ticket": ticket.model_dump(mode="json")},
        )
        classification = self._classify(run_id, ticket)
        customer, order, passages = self._ground(run_id, ticket, classification, tools)
        refund, escalation, status, note = self._act(
            run_id,
            ticket,
            classification,
            customer,
            order,
            passages,
            tools,
        )
        reply = self._draft(
            run_id,
            ticket,
            classification,
            customer,
            order,
            passages,
            refund,
            escalation,
            status,
            note,
        )
        self.store.save_reply(ticket.id, reply)
        self._audit(
            run_id,
            ticket.id,
            "completed",
            {"ticket_status": status.value, "tools": list(tools)},
        )
        events = self.store.list_audit(ticket.id)
        return AgentResult(
            ticket_id=ticket.id,
            intent=classification.intent,
            urgency=classification.urgency,
            rationale=classification.rationale,
            reply=reply,
            tools=tools,
            ticket_status=status,
            escalated=escalation is not None,
            escalation_reason=escalation.reason if escalation else None,
            escalation_queue=escalation.queue if escalation else None,
            refund_id=refund.id if refund else None,
            refund_status=RefundStatus(refund.status) if refund else None,
            refund_amount_cents=refund.amount_cents if refund else None,
            gate_code=refund.gate_code if refund else None,
            order_id=order.id if order else None,
            customer_name=customer.name if customer else None,
            customer_email=ticket.from_email,
            policy_titles=[passage.title for passage in passages],
            audit_event_types=[event.event_type for event in events],
        )

    def process_open(self) -> list[AgentResult]:
        """Process every ticket the mock helpdesk currently marks open."""
        results: list[AgentResult] = []
        for stored in self.store.list_tickets(status=TicketStatus.OPEN.value):
            results.append(self.handle(_ticket_from_stored(stored)))
        return results

    def decide_refund(self, refund_id: str, decision: str, reviewer: str) -> RefundRecord:
        """Record a human approve or deny. Approval is the only path that pays a parked refund."""
        cleaned = reviewer.strip()
        if not cleaned:
            raise RefundError("Reviewer is required")
        try:
            choice = HumanDecision(decision)
        except ValueError as exc:
            raise RefundError(f"Unknown decision: {decision}") from exc
        refund = self.store.apply_human_decision(refund_id, choice, cleaned)
        self._audit(
            f"approval-{refund_id}",
            refund.ticket_id,
            "approval_decision",
            {
                "refund_id": refund_id,
                "decision": choice.value,
                "reviewer": cleaned,
                "status": refund.status,
                "gate_code": refund.gate_code,
            },
            actor="human",
        )
        return refund

    def _classify(self, run_id: str, ticket: Ticket) -> Classification:
        raw = self._complete(Task.CLASSIFY, {"ticket": ticket.model_dump(mode="json")})
        try:
            classification = Classification.model_validate(raw)
        except ValidationError as exc:
            raise AgentError("Classifier returned an invalid payload") from exc
        if classification.order_id is None:
            found = extract_order_id(f"{ticket.subject}\n{ticket.body}")
            if found:
                classification = classification.model_copy(update={"order_id": found})
        self._audit(
            run_id,
            ticket.id,
            "classification",
            classification.model_dump(mode="json"),
        )
        return classification

    def _ground(
        self,
        run_id: str,
        ticket: Ticket,
        classification: Classification,
        tools: list[str],
    ) -> tuple[Customer | None, Order | None, list[Passage]]:
        query = " ".join([classification.intent.value, ticket.subject, ticket.body])
        context = {
            "classification": classification.model_dump(mode="json"),
            "query": query,
            "ticket": ticket.model_dump(mode="json"),
        }
        plan = self._parse_plan(self._complete(Task.SELECT_TOOLS, context))
        customer: Customer | None = None
        order: Order | None = None
        passages: list[Passage] = []
        for action in plan.actions:
            match action.tool:
                case ActionTool.LOOKUP_CUSTOMER:
                    tools.append(action.tool.value)
                    customer = _as_customer(
                        self._invoke(
                            run_id,
                            ticket.id,
                            action.tool,
                            {"email": ticket.from_email},
                            lambda: self.tools.lookup_customer(ticket.from_email),
                        )
                    )
                case ActionTool.LOOKUP_ORDER:
                    tools.append(action.tool.value)
                    requested = action.order_id or classification.order_id
                    order = _as_order(
                        self._invoke(
                            run_id,
                            ticket.id,
                            action.tool,
                            {"fallback_email": ticket.from_email, "order_id": requested},
                            lambda requested=requested: self.tools.lookup_order(
                                requested,
                                ticket.from_email,
                            ),
                        )
                    )
                case ActionTool.SEARCH_POLICY:
                    tools.append(action.tool.value)
                    search_query = action.query or query
                    found = self._invoke(
                        run_id,
                        ticket.id,
                        action.tool,
                        {"query": search_query},
                        lambda search_query=search_query: self.tools.search_policy(search_query),
                    )
                    passages = _passages(found)
                case (
                    ActionTool.ISSUE_REFUND
                    | ActionTool.ESCALATE_TO_HUMAN
                    | ActionTool.UPDATE_TICKET_STATUS
                ):
                    raise AgentError(f"{action.tool.value} is not available during grounding")
                case _ as unreachable:
                    assert_never(unreachable)
        return customer, order, passages

    def _act(
        self,
        run_id: str,
        ticket: Ticket,
        classification: Classification,
        customer: Customer | None,
        order: Order | None,
        passages: list[Passage],
        tools: list[str],
    ) -> tuple[RefundRecord | None, EscalationRecord | None, TicketStatus, str]:
        context = {
            "classification": classification.model_dump(mode="json"),
            "customer": None if customer is None else customer.model_dump(mode="json"),
            "order": None if order is None else order.model_dump(mode="json"),
            "policies": [passage.model_dump(mode="json") for passage in passages],
            "ticket": ticket.model_dump(mode="json"),
            **_limits(self.settings),
        }
        plan = self._parse_plan(self._complete(Task.PLAN, context))
        refund: RefundRecord | None = None
        escalation: EscalationRecord | None = None
        status = TicketStatus.OPEN
        note = ""
        for action in plan.actions:
            match action.tool:
                case ActionTool.ISSUE_REFUND:
                    tools.append(action.tool.value)
                    refund = self._issue(run_id, ticket, action, customer, order)
                case ActionTool.ESCALATE_TO_HUMAN:
                    tools.append(action.tool.value)
                    escalation = self._escalate(run_id, ticket, action)
                case ActionTool.UPDATE_TICKET_STATUS:
                    tools.append(action.tool.value)
                    status, note = self._update(run_id, ticket, action)
                case (
                    ActionTool.LOOKUP_CUSTOMER | ActionTool.LOOKUP_ORDER | ActionTool.SEARCH_POLICY
                ):
                    raise AgentError(f"{action.tool.value} is not available while planning writes")
                case _ as unreachable:
                    assert_never(unreachable)
        desired = _desired_status(refund, escalation)
        if desired is not status:
            if not note:
                note = "Updated the ticket to match the tool results."
            status, note = self._write_status(run_id, ticket.id, desired, note, tools)
        return refund, escalation, status, note

    def _draft(
        self,
        run_id: str,
        ticket: Ticket,
        classification: Classification,
        customer: Customer | None,
        order: Order | None,
        passages: list[Passage],
        refund: RefundRecord | None,
        escalation: EscalationRecord | None,
        status: TicketStatus,
        note: str,
    ) -> str:
        context = {
            "auto_approve_limit_cents": self.settings.auto_approve_limit_cents,
            "classification": classification.model_dump(mode="json"),
            "customer": None if customer is None else customer.model_dump(mode="json"),
            "decision_note": note,
            "escalation": jsonable(escalation),
            "order": None if order is None else order.model_dump(mode="json"),
            "policies": [passage.model_dump(mode="json") for passage in passages],
            "refund": None if refund is None else refund.model_dump(mode="json"),
            "ticket": ticket.model_dump(mode="json"),
            "ticket_status": status.value,
        }
        raw = self._complete(Task.DRAFT, context)
        reply = raw.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            raise AgentError("The model returned an empty reply")
        text = reply.strip()
        self._audit(run_id, ticket.id, "reply_drafted", {"reply": text})
        return text

    def _issue(
        self,
        run_id: str,
        ticket: Ticket,
        action: PlannedAction,
        customer: Customer | None,
        order: Order | None,
    ) -> RefundRecord:
        if action.amount_cents is None or action.reason is None:
            raise AgentError("issue_refund is missing amount_cents or reason")
        try:
            reason = RefundReason(action.reason)
        except ValueError as exc:
            raise AgentError(f"Unknown refund reason: {action.reason}") from exc
        order_id = order.id if order is not None else action.order_id
        if not order_id:
            raise AgentError("issue_refund is missing an order id")
        customer_id = customer.id if customer is not None else None
        args = {
            "amount_cents": action.amount_cents,
            "customer_id": customer_id,
            "order_id": order_id,
            "reason": reason.value,
        }
        result = self._invoke(
            run_id,
            ticket.id,
            ActionTool.ISSUE_REFUND,
            args,
            lambda: self.tools.issue_refund(
                ticket_id=ticket.id,
                order_id=order_id,
                customer_id=customer_id,
                amount_cents=action.amount_cents or 0,
                reason=reason,
            ),
        )
        if not isinstance(result, RefundRecord):
            raise AgentError("issue_refund returned an unexpected result")
        return result

    def _escalate(self, run_id: str, ticket: Ticket, action: PlannedAction) -> EscalationRecord:
        if action.escalate_reason is None or action.queue is None:
            raise AgentError("escalate_to_human is missing a reason or queue")
        try:
            reason = EscalationReason(action.escalate_reason)
        except ValueError as exc:
            raise AgentError(f"Unknown escalation reason: {action.escalate_reason}") from exc
        if action.queue not in ALLOWED_QUEUES:
            raise AgentError(f"Unknown queue: {action.queue}")
        args = {"queue": action.queue, "reason": reason.value}
        result = self._invoke(
            run_id,
            ticket.id,
            ActionTool.ESCALATE_TO_HUMAN,
            args,
            lambda: self.tools.escalate(
                ticket_id=ticket.id,
                reason=reason.value,
                queue=action.queue or "",
            ),
        )
        if not isinstance(result, EscalationRecord):
            raise AgentError("escalate_to_human returned an unexpected result")
        return result

    def _update(
        self,
        run_id: str,
        ticket: Ticket,
        action: PlannedAction,
    ) -> tuple[TicketStatus, str]:
        if action.status is None:
            raise AgentError("update_ticket_status is missing a status")
        try:
            status = TicketStatus(action.status)
        except ValueError as exc:
            raise AgentError(f"Unknown ticket status: {action.status}") from exc
        note = action.note or ""
        self._write_status(run_id, ticket.id, status, note, tools=None)
        return status, note

    def _write_status(
        self,
        run_id: str,
        ticket_id: str,
        status: TicketStatus,
        note: str,
        tools: list[str] | None,
    ) -> tuple[TicketStatus, str]:
        if tools is not None:
            tools.append(ActionTool.UPDATE_TICKET_STATUS.value)
        self._invoke(
            run_id,
            ticket_id,
            ActionTool.UPDATE_TICKET_STATUS,
            {"note": note, "status": status.value},
            lambda: self.tools.update_ticket(ticket_id, status.value, note),
        )
        return status, note

    def _parse_plan(self, raw: dict) -> ActionPlan:
        try:
            plan = ActionPlan.model_validate(raw)
        except ValidationError as exc:
            raise AgentError("The model returned an invalid action plan") from exc
        if len(plan.actions) > _MAX_ACTIONS:
            raise AgentError("Action plan is too long")
        return plan

    def _complete(self, task: Task, context: dict) -> dict:
        return self.llm.complete(
            task=task.value,
            system=system_for(task),
            user=user_prompt(context),
            context=context,
        )

    def _invoke(
        self,
        run_id: str,
        ticket_id: str,
        tool: ActionTool,
        args: dict,
        fn: Callable[[], object],
    ) -> object:
        self._audit(run_id, ticket_id, "tool_call", {"args": args, "tool": tool.value})
        try:
            result = fn()
        except Exception as exc:
            self._audit(
                run_id,
                ticket_id,
                "tool_result",
                {"error": type(exc).__name__, "tool": tool.value},
            )
            raise
        self._audit(
            run_id,
            ticket_id,
            "tool_result",
            {"result": jsonable(result), "tool": tool.value},
        )
        return result

    def _audit(
        self,
        run_id: str,
        ticket_id: str,
        event_type: str,
        payload: dict,
        actor: str = "agent",
    ) -> None:
        self.store.add_audit(
            run_id=run_id,
            ticket_id=ticket_id,
            event_type=event_type,
            actor=actor,
            payload=payload,
        )


def _limits(settings: Settings) -> dict[str, object]:
    return {
        "auto_approve_limit_cents": settings.auto_approve_limit_cents,
        "courtesy_cents": settings.courtesy_cents,
        "defect_window_days": settings.defect_window_days,
        "refund_window_days": settings.refund_window_days,
        "shipping_courtesy_after_days": settings.shipping_courtesy_after_days,
        "shipping_lost_after_days": settings.shipping_lost_after_days,
        "today": settings.today.isoformat(),
    }


def _desired_status(
    refund: RefundRecord | None,
    escalation: EscalationRecord | None,
) -> TicketStatus:
    if refund is not None and refund.status == RefundStatus.PENDING_APPROVAL.value:
        return TicketStatus.AWAITING_APPROVAL
    if escalation is not None and (refund is None or refund.status != RefundStatus.APPROVED.value):
        return TicketStatus.ESCALATED
    return TicketStatus.RESOLVED


def _as_customer(value: object) -> Customer | None:
    if value is None:
        return None
    if isinstance(value, Customer):
        return value
    raise AgentError("lookup_customer returned an unexpected result")


def _as_order(value: object) -> Order | None:
    if value is None:
        return None
    if isinstance(value, Order):
        return value
    raise AgentError("lookup_order returned an unexpected result")


def _passages(found: object) -> list[Passage]:
    if not isinstance(found, list):
        return []
    return [item for item in found if isinstance(item, Passage)]


def _ticket_from_stored(stored: StoredTicket) -> Ticket:
    channel = "helpdesk" if stored.channel == "helpdesk" else "email"
    return Ticket(
        id=stored.id,
        channel=channel,
        from_email=stored.from_email,
        subject=stored.subject,
        body=stored.body,
        received_at=stored.received_at,
    )
