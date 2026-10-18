"""Eval gate and dataset checks."""

import json
from pathlib import Path

import pytest

from support_ops.cli import main
from support_ops.config import repo_root
from support_ops.eval_harness import load_dataset, load_thresholds, run_eval, score_tool_choice
from support_ops.inbox import load_tickets
from support_ops.models import Intent
from tests.helpers import load_case


def test_eval_gate() -> None:
    report = run_eval()
    assert report.ok, report.render()


def test_thresholds_are_strict() -> None:
    assert load_thresholds(repo_root() / "evals" / "thresholds.json") == {
        "intent_accuracy": 1.0,
        "tool_choice": 1.0,
        "policy_compliance": 1.0,
    }


def test_dataset_covers_every_intent() -> None:
    found = {case.expected.intent for case in load_dataset()}
    assert found == set(Intent)


def test_tool_choice_rejects_a_forbidden_tool() -> None:
    ok, reason = score_tool_choice(
        required=["issue_refund"],
        forbidden=["escalate_to_human"],
        actual=["issue_refund", "escalate_to_human", "lookup_customer"],
    )
    assert ok is False
    assert "forbidden" in reason


def test_sample_inbox_matches_labeled_tickets() -> None:
    inbox = load_tickets(repo_root() / "data" / "inbox" / "sample_tickets.json")
    assert [ticket.id for ticket in inbox] == [
        "TCK-1001",
        "TCK-1002",
        "TCK-1005",
        "TCK-1006",
        "TCK-1008",
        "TCK-1011",
    ]
    for ticket in inbox:
        assert ticket == load_case(ticket.id).ticket


def test_cli_process_prints_the_mock_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = load_case("TCK-1001")
    inbox = tmp_path / "inbox.json"
    inbox.write_text(
        json.dumps([case.ticket.model_dump(mode="json")]),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "process",
                "--inbox",
                str(inbox),
                "--db",
                str(tmp_path / "demo.db"),
                "--provider",
                "mock",
                "--reset",
            ]
        )
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "llm_provider: mock" in output
    assert "TCK-1001" in output
    assert "refund_request" in output
    assert "approved" in output
