"""Command line for the support operations demo."""

import argparse
from dataclasses import replace
from pathlib import Path

import uvicorn

from support_ops.agent import SupportAgent
from support_ops.config import Settings
from support_ops.eval_harness import main as eval_main
from support_ops.inbox import load_tickets
from support_ops.models import AgentResult, RefundStatus
from support_ops.util import assert_never, format_usd


def main(argv: list[str] | None = None) -> None:
    """CLI entry point. Exits with the command status code."""
    raise SystemExit(_run(argv))


def _run(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(
        prog="support-ops",
        description="Customer-support operations agent (portfolio demo).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    process = sub.add_parser("process", help="Classify and act on a JSON inbox.")
    process.add_argument("--inbox", required=True, help="Path to a JSON list of tickets.")
    process.add_argument("--db", help="SQLite path. Defaults to data/support_ops.db.")
    process.add_argument("--provider", choices=["mock", "openai", "anthropic"])
    process.add_argument(
        "--reset",
        action="store_true",
        help="Delete the local demo database before processing.",
    )
    process.set_defaults(func=_process)

    evaluate = sub.add_parser("eval", help="Score the labeled tickets in mock mode.")
    evaluate.set_defaults(func=_eval)

    serve = sub.add_parser("serve", help="Run the FastAPI demo server.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=_serve)

    approve = sub.add_parser("approve", help="Approve a parked refund.")
    _add_decision_args(approve)
    approve.set_defaults(func=_approve)

    deny = sub.add_parser("deny", help="Deny a parked refund.")
    _add_decision_args(deny)
    deny.set_defaults(func=_deny)

    args = parser.parse_args(argv)
    return int(args.func(args))


def _add_decision_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("refund_id")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--db")


def _process(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    if args.reset:
        settings.db_path.unlink(missing_ok=True)
    tickets = load_tickets(Path(args.inbox))
    agent = SupportAgent(settings)
    try:
        print("Support Ops Agent")
        print("portfolio demo, synthetic inbox")
        print(f"llm_provider: {agent.llm_name}")
        print(f"business_date: {settings.today.isoformat()}")
        print(f"auto_approve_limit: {format_usd(settings.auto_approve_limit_cents)}")
        print(f"tickets: {len(tickets)}")
        print()
        for ticket in tickets:
            result = agent.handle(ticket)
            print(format_result(result, settings.auto_approve_limit_cents))
            print()
        return 0
    finally:
        agent.store.close()


def _eval(_args: argparse.Namespace) -> int:
    return eval_main()


def _serve(args: argparse.Namespace) -> int:
    uvicorn.run("support_ops.api:app", host=args.host, port=args.port)
    return 0


def _approve(args: argparse.Namespace) -> int:
    return _decide(args, "approve")


def _deny(args: argparse.Namespace) -> int:
    return _decide(args, "deny")


def _decide(args: argparse.Namespace, decision: str) -> int:
    agent = SupportAgent(_settings_from_args(args))
    try:
        refund = agent.decide_refund(args.refund_id, decision, args.reviewer)
        order = agent.store.get_order(refund.order_id)
        ticket = agent.store.get_ticket(refund.ticket_id)
        print(f"Refund {refund.id}  decision={decision}  reviewer={args.reviewer}")
        print(f"  status: {refund.status}")
        print(f"  gate: {refund.gate_code}")
        if order is not None:
            print(
                f"  order {order.id} refunded_cents={order.refunded_cents} "
                f"status={order.status.value}"
            )
        if ticket is not None:
            print(f"  ticket {ticket.id} status={ticket.status}")
        return 0
    finally:
        agent.store.close()


def format_result(result: AgentResult, limit_cents: int) -> str:
    """Render one agent result for the terminal."""
    customer = result.customer_name or "unknown"
    order = result.order_id or "none"
    reply = "    " + result.reply.replace("\n", "\n    ")
    lines = [
        f"{result.ticket_id}  {result.intent.value}  {result.urgency.value}",
        f"  customer: {customer} <{result.customer_email}>",
        f"  order: {order}",
        f"  tools: {', '.join(result.tools)}",
        f"  refund: {_refund_line(result, limit_cents)}",
        f"  escalated: {str(result.escalated).lower()}",
        f"  status: {result.ticket_status.value}",
        "  reply:",
        reply,
    ]
    return "\n".join(lines)


def _refund_line(result: AgentResult, limit_cents: int) -> str:
    if result.refund_status is None or result.refund_amount_cents is None:
        if result.refund_status is RefundStatus.DENIED:
            return f"denied ({result.gate_code})"
        return "none"
    amount = format_usd(result.refund_amount_cents)
    limit = format_usd(limit_cents)
    status = result.refund_status
    match status:
        case RefundStatus.APPROVED:
            return f"approved {amount} (under {limit} auto-approve limit)"
        case RefundStatus.PENDING_APPROVAL:
            return f"pending_approval {amount} (above {limit} limit, human approval required)"
        case RefundStatus.DENIED:
            return f"denied ({result.gate_code})"
        case _ as unreachable:
            assert_never(unreachable)


def _settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    updates: dict[str, object] = {}
    db = getattr(args, "db", None)
    if db:
        updates["db_path"] = Path(db)
    provider = getattr(args, "provider", None)
    if provider:
        updates["llm_provider"] = provider
    if not updates:
        return settings
    return replace(settings, **updates)
