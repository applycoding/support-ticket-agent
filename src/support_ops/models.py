"""Domain models for tickets, orders, refunds, and agent results."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Intent(StrEnum):
    """Why the customer wrote in."""

    REFUND_REQUEST = "refund_request"
    ORDER_STATUS = "order_status"
    SHIPPING_DELAY = "shipping_delay"
    BILLING_DISPUTE = "billing_dispute"
    PRODUCT_DEFECT = "product_defect"
    ACCOUNT_ACCESS = "account_access"
    CANCELLATION = "cancellation"
    GENERAL_INQUIRY = "general_inquiry"


class Urgency(StrEnum):
    """How quickly a person should look at the ticket."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Provider(StrEnum):
    """Supported LLM backends. mock needs no API key."""

    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Task(StrEnum):
    """JSON tasks the agent asks the model to perform."""

    CLASSIFY = "classify"
    SELECT_TOOLS = "select_tools"
    PLAN = "plan"
    DRAFT = "draft"


class ActionTool(StrEnum):
    """Tools the agent can execute."""

    LOOKUP_CUSTOMER = "lookup_customer"
    LOOKUP_ORDER = "lookup_order"
    SEARCH_POLICY = "search_policy"
    ISSUE_REFUND = "issue_refund"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    UPDATE_TICKET_STATUS = "update_ticket_status"


class RefundReason(StrEnum):
    """Why a refund was requested."""

    CHANGE_OF_MIND = "change_of_mind"
    DEFECT = "defect"
    CANCELLATION = "cancellation"
    SHIPPING_COURTESY = "shipping_courtesy"
    LOST_PACKAGE = "lost_package"


class RefundStatus(StrEnum):
    """Lifecycle of a refund row."""

    APPROVED = "approved"
    PENDING_APPROVAL = "pending_approval"
    DENIED = "denied"


class GateCode(StrEnum):
    """Policy result recorded on a refund attempt."""

    APPROVED_UNDER_LIMIT = "approved_under_limit"
    PENDING_APPROVAL = "pending_approval"
    ORDER_NOT_FOUND = "order_not_found"
    CUSTOMER_MISMATCH = "customer_mismatch"
    ALREADY_REFUNDED = "already_refunded"
    EXCEEDS_ORDER_TOTAL = "exceeds_order_total"
    OUTSIDE_WINDOW = "outside_window"
    NOT_ELIGIBLE = "not_eligible"
    INVALID_AMOUNT = "invalid_amount"
    DUPLICATE_REQUEST = "duplicate_request"
    HUMAN_APPROVED = "human_approved"
    HUMAN_DENIED = "human_denied"


class TicketStatus(StrEnum):
    """Helpdesk status values the agent writes."""

    OPEN = "open"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    AWAITING_APPROVAL = "awaiting_approval"


class OrderStatus(StrEnum):
    """Fulfillment status stored on an order."""

    NOT_SHIPPED = "not_shipped"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    REFUNDED = "refunded"


class EscalationReason(StrEnum):
    """Why a ticket was handed to a person."""

    ACCOUNT_SECURITY = "account_security"
    BILLING_DISPUTE = "billing_dispute"
    REFUND_APPROVAL = "refund_approval"
    CUSTOMER_MISMATCH = "customer_mismatch"
    MISSING_ORDER = "missing_order"
    CANCELLATION_AFTER_SHIPMENT = "cancellation_after_shipment"
    SHIPPING_INVESTIGATION = "shipping_investigation"


class HumanDecision(StrEnum):
    """A reviewer's approve or deny choice."""

    APPROVE = "approve"
    DENY = "deny"


ALLOWED_QUEUES = frozenset(
    {"security", "billing", "refunds", "fraud_review", "support", "shipping"}
)

READ_TOOLS = frozenset(
    {
        ActionTool.LOOKUP_CUSTOMER,
        ActionTool.LOOKUP_ORDER,
        ActionTool.SEARCH_POLICY,
    }
)

WRITE_TOOLS = frozenset(
    {
        ActionTool.ISSUE_REFUND,
        ActionTool.ESCALATE_TO_HUMAN,
        ActionTool.UPDATE_TICKET_STATUS,
    }
)


class Ticket(BaseModel):
    """An inbound email or helpdesk ticket."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    channel: Literal["email", "helpdesk"]
    from_email: str = Field(min_length=3)
    subject: str
    body: str
    received_at: str


class Customer(BaseModel):
    """A synthetic CRM customer."""

    id: str
    name: str
    email: str
    tier: str


class Order(BaseModel):
    """A synthetic order."""

    id: str
    customer_id: str
    sku: str
    item_name: str
    total_cents: int
    refunded_cents: int
    status: OrderStatus
    shipped_at: str | None = None
    delivered_at: str | None = None


class StoredTicket(BaseModel):
    """A ticket row in the mock helpdesk."""

    id: str
    channel: str
    from_email: str
    subject: str
    body: str
    received_at: str
    status: str
    note: str
    reply: str


class RefundRecord(BaseModel):
    """A refund attempt, including denials and parked approvals."""

    id: str
    ticket_id: str
    order_id: str
    amount_cents: int
    reason: str
    status: str
    gate_code: str
    created_at: str


class EscalationRecord(BaseModel):
    """A handoff to a human queue."""

    id: str
    ticket_id: str
    reason: str
    queue: str
    created_at: str


class ApprovalRecord(BaseModel):
    """The human decision attached to a parked refund."""

    refund_id: str
    status: str
    reviewer: str | None = None
    decided_at: str | None = None


class AuditEvent(BaseModel):
    """One step in the audit trail."""

    id: int
    run_id: str
    ticket_id: str
    seq: int
    event_type: str
    actor: str
    payload: dict
    created_at: str


class Classification(BaseModel):
    """Intent, urgency, and an optional order id from the model."""

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    urgency: Urgency
    order_id: str | None = None
    rationale: str

    @field_validator("order_id")
    @classmethod
    def _clean_order_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or cleaned.lower() == "null":
            return None
        return cleaned.upper()


class PlannedAction(BaseModel):
    """One tool call returned by the model."""

    model_config = ConfigDict(extra="ignore")

    tool: ActionTool
    email: str | None = None
    order_id: str | None = None
    query: str | None = None
    amount_cents: int | None = None
    reason: str | None = None
    escalate_reason: str | None = None
    queue: str | None = None
    status: str | None = None
    note: str | None = None


class ActionPlan(BaseModel):
    """A list of tool calls."""

    model_config = ConfigDict(extra="ignore")

    actions: list[PlannedAction]


class AgentResult(BaseModel):
    """What the agent did with one ticket."""

    ticket_id: str
    intent: Intent
    urgency: Urgency
    rationale: str
    reply: str
    tools: list[str]
    ticket_status: TicketStatus
    escalated: bool
    escalation_reason: str | None = None
    escalation_queue: str | None = None
    refund_id: str | None = None
    refund_status: RefundStatus | None = None
    refund_amount_cents: int | None = None
    gate_code: str | None = None
    order_id: str | None = None
    customer_name: str | None = None
    customer_email: str
    policy_titles: list[str]
    audit_event_types: list[str]


class RefundDecisionIn(BaseModel):
    """Body for the human approval endpoint."""

    model_config = ConfigDict(extra="forbid")

    decision: HumanDecision
    reviewer: str = Field(min_length=1, max_length=80)


class ExpectedOutcome(BaseModel):
    """Labeled outcome for one eval ticket."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    urgency: Urgency
    order_id: str | None
    required_tools: list[str]
    forbidden_tools: list[str]
    refund_status: Literal["approved", "pending_approval", "denied", "none"]
    refund_amount_cents: int | None = None
    ticket_status: Literal["resolved", "escalated", "awaiting_approval", "open"]
    escalated: bool

    @field_validator("required_tools", "forbidden_tools")
    @classmethod
    def _known_tools(cls, value: list[str]) -> list[str]:
        known = {tool.value for tool in ActionTool}
        unknown = [tool for tool in value if tool not in known]
        if unknown:
            raise ValueError(f"Unknown tools: {unknown}")
        return value

    @model_validator(mode="after")
    def _amount_matches_status(self) -> Self:
        if self.refund_status == "none" and self.refund_amount_cents is not None:
            raise ValueError("refund_amount_cents must be null when no refund is expected")
        needs_amount = self.refund_status in {"approved", "pending_approval"}
        if needs_amount and self.refund_amount_cents is None:
            raise ValueError("refund_amount_cents is required for this refund status")
        return self


class EvalCase(BaseModel):
    """One labeled ticket in the eval set."""

    model_config = ConfigDict(extra="forbid")

    id: str
    ticket: Ticket
    expected: ExpectedOutcome


class BatchResult(BaseModel):
    """Result of processing every open helpdesk ticket."""

    count: int
    results: list[AgentResult]
