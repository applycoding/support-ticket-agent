"""SQLite CRM, mock helpdesk, refunds, and the audit trail."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from support_ops.errors import NotFoundError, RefundError
from support_ops.models import (
    ApprovalRecord,
    AuditEvent,
    Customer,
    EscalationRecord,
    HumanDecision,
    Order,
    OrderStatus,
    RefundRecord,
    RefundStatus,
    StoredTicket,
    Ticket,
    TicketStatus,
)
from support_ops.seed import CUSTOMERS, ORDERS
from support_ops.util import assert_never, jsonable, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    sku TEXT NOT NULL,
    item_name TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    refunded_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    shipped_at TEXT,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    from_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    reply TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    gate_code TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    queue TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    refund_id TEXT PRIMARY KEY REFERENCES refunds(id),
    status TEXT NOT NULL,
    reviewer TEXT,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (ticket_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_audit_ticket ON audit_events(ticket_id, seq);
"""


class Store:
    """Local database for the demo. One connection, guarded by a reentrant lock."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(SCHEMA)
        with self.transaction():
            self._seed()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit on the outermost block. Nested calls share the transaction."""
        with self._lock:
            start = self._depth == 0
            if start:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except Exception:
                if start:
                    self._conn.rollback()
                raise
            else:
                if start:
                    self._conn.commit()
            finally:
                self._depth -= 1

    def get_customer_by_email(self, email: str) -> Customer | None:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM customers WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
            return None if row is None else Customer.model_validate(dict(row))

    def list_customers(self) -> list[Customer]:
        with self.transaction():
            rows = self._conn.execute("SELECT * FROM customers ORDER BY id").fetchall()
            return [Customer.model_validate(dict(row)) for row in rows]

    def get_order(self, order_id: str) -> Order | None:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM orders WHERE id = ?",
                (order_id.strip().upper(),),
            ).fetchone()
            return None if row is None else Order.model_validate(dict(row))

    def list_orders(self) -> list[Order]:
        with self.transaction():
            rows = self._conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
            return [Order.model_validate(dict(row)) for row in rows]

    def latest_order_for_customer(self, customer_id: str) -> Order | None:
        with self.transaction():
            row = self._conn.execute(
                """
                SELECT * FROM orders
                WHERE customer_id = ?
                ORDER BY COALESCE(delivered_at, shipped_at, '') DESC, id DESC
                LIMIT 1
                """,
                (customer_id,),
            ).fetchone()
            return None if row is None else Order.model_validate(dict(row))

    def upsert_ticket(self, ticket: Ticket) -> StoredTicket:
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO tickets (
                    id, channel, from_email, subject, body, received_at, status, note, reply
                ) VALUES (?, ?, ?, ?, ?, ?, 'open', '', '')
                ON CONFLICT(id) DO UPDATE SET
                    channel = excluded.channel,
                    from_email = excluded.from_email,
                    subject = excluded.subject,
                    body = excluded.body,
                    received_at = excluded.received_at
                """,
                (
                    ticket.id,
                    ticket.channel,
                    ticket.from_email.strip().lower(),
                    ticket.subject,
                    ticket.body,
                    ticket.received_at,
                ),
            )
            stored = self.get_ticket(ticket.id)
            if stored is None:
                raise RuntimeError("Ticket upsert failed")
            return stored

    def get_ticket(self, ticket_id: str) -> StoredTicket | None:
        with self.transaction():
            row = self._conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            return None if row is None else StoredTicket.model_validate(dict(row))

    def list_tickets(self, status: str | None = None) -> list[StoredTicket]:
        with self.transaction():
            if status is None:
                rows = self._conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tickets WHERE status = ? ORDER BY id",
                    (status,),
                ).fetchall()
            return [StoredTicket.model_validate(dict(row)) for row in rows]

    def update_ticket(self, ticket_id: str, *, status: str, note: str) -> StoredTicket:
        with self.transaction():
            cursor = self._conn.execute(
                "UPDATE tickets SET status = ?, note = ? WHERE id = ?",
                (status, note, ticket_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"Ticket {ticket_id} was not found")
            stored = self.get_ticket(ticket_id)
            if stored is None:
                raise NotFoundError(f"Ticket {ticket_id} was not found")
            return stored

    def save_reply(self, ticket_id: str, reply: str) -> None:
        with self.transaction():
            cursor = self._conn.execute(
                "UPDATE tickets SET reply = ? WHERE id = ?",
                (reply, ticket_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"Ticket {ticket_id} was not found")

    def pending_refund_for_order(self, order_id: str) -> RefundRecord | None:
        with self.transaction():
            row = self._conn.execute(
                """
                SELECT * FROM refunds
                WHERE order_id = ? AND status = ?
                ORDER BY id
                LIMIT 1
                """,
                (order_id, RefundStatus.PENDING_APPROVAL.value),
            ).fetchone()
            return None if row is None else RefundRecord.model_validate(dict(row))

    def get_refund(self, refund_id: str) -> RefundRecord | None:
        with self.transaction():
            row = self._conn.execute("SELECT * FROM refunds WHERE id = ?", (refund_id,)).fetchone()
            return None if row is None else RefundRecord.model_validate(dict(row))

    def list_refunds_for_ticket(self, ticket_id: str) -> list[RefundRecord]:
        with self.transaction():
            rows = self._conn.execute(
                "SELECT * FROM refunds WHERE ticket_id = ? ORDER BY id",
                (ticket_id,),
            ).fetchall()
            return [RefundRecord.model_validate(dict(row)) for row in rows]

    def insert_refund(
        self,
        *,
        ticket_id: str,
        order_id: str,
        amount_cents: int,
        reason: str,
        status: str,
        gate_code: str,
        apply_balance: bool,
    ) -> RefundRecord:
        with self.transaction():
            if apply_balance:
                self._apply_balance(order_id, amount_cents)
            refund_id = self._next_count_id("refunds", "REF")
            self._conn.execute(
                """
                INSERT INTO refunds (
                    id, ticket_id, order_id, amount_cents, reason, status, gate_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    refund_id,
                    ticket_id,
                    order_id,
                    amount_cents,
                    reason,
                    status,
                    gate_code,
                    utc_now(),
                ),
            )
            if status == RefundStatus.PENDING_APPROVAL.value:
                self._conn.execute(
                    """
                    INSERT INTO approvals (refund_id, status, reviewer, decided_at)
                    VALUES (?, 'pending', NULL, NULL)
                    """,
                    (refund_id,),
                )
            stored = self.get_refund(refund_id)
            if stored is None:
                raise RuntimeError("Refund insert failed")
            return stored

    def get_approval(self, refund_id: str) -> ApprovalRecord | None:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM approvals WHERE refund_id = ?",
                (refund_id,),
            ).fetchone()
            return None if row is None else ApprovalRecord.model_validate(dict(row))

    def apply_human_decision(
        self,
        refund_id: str,
        decision: HumanDecision,
        reviewer: str,
    ) -> RefundRecord:
        with self.transaction():
            refund = self.get_refund(refund_id)
            if refund is None:
                raise NotFoundError(f"Refund {refund_id} was not found")
            if refund.status != RefundStatus.PENDING_APPROVAL.value:
                raise RefundError(f"Refund {refund_id} is {refund.status}, not pending approval")
            approval = self.get_approval(refund_id)
            if approval is None:
                raise RefundError(f"Refund {refund_id} has no approval record")
            match decision:
                case HumanDecision.APPROVE:
                    self._apply_balance(refund.order_id, refund.amount_cents)
                    new_status = RefundStatus.APPROVED.value
                    gate_code = "human_approved"
                    note = f"{reviewer} approved the refund."
                    approval_status = "approved"
                case HumanDecision.DENY:
                    new_status = RefundStatus.DENIED.value
                    gate_code = "human_denied"
                    note = f"{reviewer} denied the refund."
                    approval_status = "denied"
                case _ as unreachable:
                    assert_never(unreachable)
            self._conn.execute(
                "UPDATE refunds SET status = ?, gate_code = ? WHERE id = ?",
                (new_status, gate_code, refund_id),
            )
            self._conn.execute(
                """
                UPDATE approvals
                SET status = ?, reviewer = ?, decided_at = ?
                WHERE refund_id = ?
                """,
                (approval_status, reviewer, utc_now(), refund_id),
            )
            self.update_ticket(refund.ticket_id, status=TicketStatus.RESOLVED.value, note=note)
            updated = self.get_refund(refund_id)
            if updated is None:
                raise RuntimeError("Refund disappeared after the decision")
            return updated

    def create_escalation(self, *, ticket_id: str, reason: str, queue: str) -> EscalationRecord:
        with self.transaction():
            escalation_id = self._next_count_id("escalations", "ESC")
            self._conn.execute(
                """
                INSERT INTO escalations (id, ticket_id, reason, queue, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (escalation_id, ticket_id, reason, queue, utc_now()),
            )
            row = self._conn.execute(
                "SELECT * FROM escalations WHERE id = ?",
                (escalation_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Escalation insert failed")
            return EscalationRecord.model_validate(dict(row))

    def list_escalations_for_ticket(self, ticket_id: str) -> list[EscalationRecord]:
        with self.transaction():
            rows = self._conn.execute(
                "SELECT * FROM escalations WHERE ticket_id = ? ORDER BY id",
                (ticket_id,),
            ).fetchall()
            return [EscalationRecord.model_validate(dict(row)) for row in rows]

    def add_audit(
        self,
        *,
        run_id: str,
        ticket_id: str,
        event_type: str,
        actor: str,
        payload: dict,
    ) -> AuditEvent:
        with self.transaction():
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM audit_events WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
            seq = int(row["seq"]) + 1
            created_at = utc_now()
            cursor = self._conn.execute(
                """
                INSERT INTO audit_events (
                    run_id, ticket_id, seq, event_type, actor, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    ticket_id,
                    seq,
                    event_type,
                    actor,
                    json.dumps(jsonable(payload), sort_keys=True),
                    created_at,
                ),
            )
            return AuditEvent(
                id=int(cursor.lastrowid),
                run_id=run_id,
                ticket_id=ticket_id,
                seq=seq,
                event_type=event_type,
                actor=actor,
                payload=dict(payload),
                created_at=created_at,
            )

    def list_audit(self, ticket_id: str) -> list[AuditEvent]:
        with self.transaction():
            rows = self._conn.execute(
                "SELECT * FROM audit_events WHERE ticket_id = ? ORDER BY seq",
                (ticket_id,),
            ).fetchall()
            events: list[AuditEvent] = []
            for row in rows:
                payload = json.loads(row["payload"])
                events.append(
                    AuditEvent(
                        id=int(row["id"]),
                        run_id=row["run_id"],
                        ticket_id=row["ticket_id"],
                        seq=int(row["seq"]),
                        event_type=row["event_type"],
                        actor=row["actor"],
                        payload=payload,
                        created_at=row["created_at"],
                    )
                )
            return events

    def _apply_balance(self, order_id: str, amount_cents: int) -> None:
        row = self._conn.execute(
            "SELECT total_cents, refunded_cents FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise RefundError(f"Order {order_id} was not found while applying a refund")
        updated = int(row["refunded_cents"]) + amount_cents
        if updated > int(row["total_cents"]):
            raise RefundError("Refund exceeds the remaining balance")
        status = OrderStatus.REFUNDED.value if updated >= int(row["total_cents"]) else None
        if status is None:
            self._conn.execute(
                "UPDATE orders SET refunded_cents = ? WHERE id = ?",
                (updated, order_id),
            )
            return
        self._conn.execute(
            "UPDATE orders SET refunded_cents = ?, status = ? WHERE id = ?",
            (updated, status, order_id),
        )

    def _next_count_id(self, table: str, prefix: str) -> str:
        if table == "refunds":
            row = self._conn.execute("SELECT COUNT(*) AS c FROM refunds").fetchone()
        elif table == "escalations":
            row = self._conn.execute("SELECT COUNT(*) AS c FROM escalations").fetchone()
        else:
            raise RuntimeError(f"Unexpected id table: {table}")
        return f"{prefix}-{int(row['c']) + 1:04d}"

    def _seed(self) -> None:
        count = self._conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()
        if int(count["c"]) > 0:
            return
        self._conn.executemany(
            "INSERT INTO customers (id, name, email, tier) VALUES (:id, :name, :email, :tier)",
            CUSTOMERS,
        )
        self._conn.executemany(
            """
            INSERT INTO orders (
                id, customer_id, sku, item_name, total_cents, refunded_cents,
                status, shipped_at, delivered_at
            ) VALUES (
                :id, :customer_id, :sku, :item_name, :total_cents, :refunded_cents,
                :status, :shipped_at, :delivered_at
            )
            """,
            ORDERS,
        )
