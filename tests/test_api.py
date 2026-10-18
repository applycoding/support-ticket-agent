"""FastAPI routes for processing tickets and approving refunds."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from support_ops.api import create_app
from support_ops.config import make_settings
from tests.helpers import load_ticket


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(make_settings(tmp_path / "api.db"))
    with TestClient(app) as test_client:
        yield test_client
    app.state.agent.store.close()


def test_health_and_docs(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["demo"] is True
    assert body["llm_provider"] == "mock"
    assert body["business_date"] == "2026-04-10"
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["demo"] is True
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Support Ops Agent"


def test_process_ticket_and_audit(client: TestClient) -> None:
    ticket = load_ticket("TCK-1001")
    response = client.post("/v1/tickets/process", json=ticket.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "refund_request"
    assert body["refund_status"] == "approved"
    assert body["refund_amount_cents"] == 4200
    audit = client.get("/v1/tickets/TCK-1001/audit")
    assert audit.status_code == 200
    kinds = [event["event_type"] for event in audit.json()]
    assert "classification" in kinds
    assert "reply_drafted" in kinds
    customer = client.get("/v1/customers", params={"email": "maya.chen@example.com"})
    assert customer.status_code == 200
    assert customer.json()["name"] == "Maya Chen"


def test_helpdesk_process_open(client: TestClient) -> None:
    ticket = load_ticket("TCK-1007")
    created = client.post("/v1/helpdesk/tickets", json=ticket.model_dump(mode="json"))
    assert created.status_code == 201
    listing = client.get("/v1/helpdesk/tickets", params={"status": "open"})
    assert listing.status_code == 200
    assert any(item["id"] == "TCK-1007" for item in listing.json())
    processed = client.post("/v1/helpdesk/process-open")
    assert processed.status_code == 200
    payload = processed.json()
    assert payload["count"] == 1
    assert payload["results"][0]["intent"] == "order_status"
    stored = client.get("/v1/tickets/TCK-1007")
    assert stored.status_code == 200
    assert stored.json()["status"] == "resolved"


def test_human_approval_endpoint(client: TestClient) -> None:
    ticket = load_ticket("TCK-1002")
    processed = client.post("/v1/tickets/process", json=ticket.model_dump(mode="json"))
    assert processed.status_code == 200
    refund_id = processed.json()["refund_id"]
    before = client.get("/v1/orders/ORD-1002")
    assert before.json()["refunded_cents"] == 0
    decision = client.post(
        f"/v1/refunds/{refund_id}/decision",
        json={"decision": "approve", "reviewer": "A. Bom"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"
    after = client.get("/v1/orders/ORD-1002")
    assert after.json()["refunded_cents"] == 24000
    assert after.json()["status"] == "refunded"


def test_unknown_refund_and_bad_decision(client: TestClient) -> None:
    missing = client.post(
        "/v1/refunds/REF-9999/decision",
        json={"decision": "deny", "reviewer": "A. Bom"},
    )
    assert missing.status_code == 404
    invalid = client.post(
        "/v1/refunds/REF-9999/decision",
        json={"decision": "maybe", "reviewer": "A. Bom"},
    )
    assert invalid.status_code == 422
    absent = client.get("/v1/orders/ORD-0000")
    assert absent.status_code == 404
