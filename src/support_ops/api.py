"""HTTP API for the support operations demo."""

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from support_ops import __version__
from support_ops.agent import SupportAgent
from support_ops.config import Settings
from support_ops.errors import AgentError, LLMError, NotFoundError, RefundError
from support_ops.models import (
    AgentResult,
    AuditEvent,
    BatchResult,
    Customer,
    Order,
    RefundDecisionIn,
    RefundRecord,
    StoredTicket,
    Ticket,
)


def get_agent(request: Request) -> SupportAgent:
    """Return the agent stored on the current app."""
    return request.app.state.agent


AgentDep = Annotated[SupportAgent, Depends(get_agent)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API. Tests pass a temporary database; the CLI uses the environment."""
    resolved = settings or Settings.from_env()
    app = FastAPI(
        title="Support Ops Agent",
        summary="Portfolio demo: a customer-support operations agent with policy gates.",
        version=__version__,
    )
    app.state.settings = resolved
    app.state.agent = SupportAgent(resolved)
    _install_errors(app)
    app.include_router(_router())

    @app.get("/")
    def root() -> dict[str, str | bool]:
        return {
            "name": "Support Ops Agent",
            "demo": True,
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/health")
    def health(request: Request) -> dict[str, str | bool]:
        agent = get_agent(request)
        return {
            "status": "ok",
            "demo": True,
            "llm_provider": agent.llm_name,
            "business_date": agent.settings.today.isoformat(),
        }

    return app


def _router() -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.post("/tickets/process", response_model=AgentResult)
    def process_ticket(ticket: Ticket, agent: AgentDep) -> AgentResult:
        return agent.handle(ticket)

    @router.get("/tickets/{ticket_id}", response_model=StoredTicket)
    def get_ticket(ticket_id: str, agent: AgentDep) -> StoredTicket:
        stored = agent.store.get_ticket(ticket_id)
        if stored is None:
            raise NotFoundError(f"Ticket {ticket_id} was not found")
        return stored

    @router.get("/tickets/{ticket_id}/audit", response_model=list[AuditEvent])
    def get_audit(ticket_id: str, agent: AgentDep) -> list[AuditEvent]:
        if agent.store.get_ticket(ticket_id) is None:
            raise NotFoundError(f"Ticket {ticket_id} was not found")
        return agent.store.list_audit(ticket_id)

    @router.post("/helpdesk/tickets", response_model=StoredTicket, status_code=201)
    def create_ticket(ticket: Ticket, agent: AgentDep) -> StoredTicket:
        return agent.store.upsert_ticket(ticket)

    @router.get("/helpdesk/tickets", response_model=list[StoredTicket])
    def list_tickets(agent: AgentDep, status: str | None = None) -> list[StoredTicket]:
        return agent.store.list_tickets(status=status)

    @router.post("/helpdesk/process-open", response_model=BatchResult)
    def process_open(agent: AgentDep) -> BatchResult:
        results = agent.process_open()
        return BatchResult(count=len(results), results=results)

    @router.get("/orders/{order_id}", response_model=Order)
    def get_order(order_id: str, agent: AgentDep) -> Order:
        order = agent.store.get_order(order_id)
        if order is None:
            raise NotFoundError(f"Order {order_id} was not found")
        return order

    @router.get("/customers", response_model=Customer)
    def get_customer(email: str, agent: AgentDep) -> Customer:
        customer = agent.store.get_customer_by_email(email)
        if customer is None:
            raise NotFoundError(f"Customer {email} was not found")
        return customer

    @router.post("/refunds/{refund_id}/decision", response_model=RefundRecord)
    def decide_refund(refund_id: str, body: RefundDecisionIn, agent: AgentDep) -> RefundRecord:
        return agent.decide_refund(refund_id, body.decision.value, body.reviewer)

    return router


def _install_errors(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(RefundError)
    def refund_error(_request: Request, exc: RefundError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AgentError)
    def agent_error(_request: Request, exc: AgentError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(LLMError)
    def llm_error(_request: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})


app = create_app()
