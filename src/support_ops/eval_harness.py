"""Score labeled tickets on intent, tool choice, and policy compliance."""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from support_ops.agent import SupportAgent
from support_ops.config import make_settings, repo_root
from support_ops.models import AgentResult, EvalCase

_METRIC_NAMES = ("intent_accuracy", "tool_choice", "policy_compliance")
_AUDIT_EVENTS = (
    "ticket_received",
    "classification",
    "tool_call",
    "tool_result",
    "reply_drafted",
    "completed",
)


@dataclass
class MetricScore:
    """One gated score."""

    name: str
    passed: int
    total: int
    threshold: float

    @property
    def value(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total

    @property
    def ok(self) -> bool:
        return self.value + 1e-12 >= self.threshold


@dataclass
class EvalReport:
    """Eval output printed by the CLI and the CI gate."""

    cases: int
    metrics: list[MetricScore]
    failures: list[str]

    @property
    def ok(self) -> bool:
        return bool(self.metrics) and all(metric.ok for metric in self.metrics)

    def render(self) -> str:
        lines = [
            "Support Ops eval report",
            "provider: mock",
            f"cases: {self.cases}",
        ]
        for metric in self.metrics:
            status = "PASS" if metric.ok else "FAIL"
            lines.append(
                f"{metric.name}: {metric.value:.3f} "
                f"({metric.passed}/{metric.total}) "
                f"threshold {metric.threshold:.3f} {status}"
            )
        if self.failures:
            lines.append("failures:")
            lines.extend(f"- {item}" for item in self.failures)
        else:
            lines.append("failures: none")
        return "\n".join(lines)


def dataset_path() -> Path:
    return repo_root() / "evals" / "dataset.json"


def thresholds_path() -> Path:
    return repo_root() / "evals" / "thresholds.json"


def load_dataset(path: Path | None = None) -> list[EvalCase]:
    """Load and validate the labeled tickets."""
    raw = json.loads((path or dataset_path()).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Eval dataset must be a non-empty list")
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for item in raw:
        case = EvalCase.model_validate(item)
        if case.id != case.ticket.id:
            raise ValueError(f"{case.id} does not match ticket id {case.ticket.id}")
        if case.id in seen:
            raise ValueError(f"Duplicate eval id {case.id}")
        seen.add(case.id)
        cases.append(case)
    return cases


def load_thresholds(path: Path | None = None) -> dict[str, float]:
    """Load the CI thresholds. Each value is between 0 and 1."""
    raw = json.loads((path or thresholds_path()).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Thresholds file must be an object")
    values: dict[str, float] = {}
    for name in _METRIC_NAMES:
        if name not in raw:
            raise ValueError(f"Missing threshold {name}")
        value = float(raw[name])
        if not 0 <= value <= 1:
            raise ValueError(f"Threshold {name} must be between 0 and 1")
        values[name] = value
    return values


def score_tool_choice(
    required: list[str],
    forbidden: list[str],
    actual: list[str],
) -> tuple[bool, str]:
    """Pass when every required tool ran and no forbidden tool ran."""
    actual_set = set(actual)
    missing = sorted(set(required) - actual_set)
    used_forbidden = sorted(set(forbidden) & actual_set)
    if not missing and not used_forbidden:
        return True, ""
    parts: list[str] = []
    if missing:
        parts.append("missing " + ", ".join(missing))
    if used_forbidden:
        parts.append("forbidden " + ", ".join(used_forbidden))
    return False, "; ".join(parts)


def run_eval(
    dataset: Path | None = None,
    thresholds: Path | None = None,
) -> EvalReport:
    """Run every labeled ticket against the mock provider in a temporary database."""
    cases = load_dataset(dataset)
    gate = load_thresholds(thresholds)
    with tempfile.TemporaryDirectory() as tmp:
        agent = SupportAgent(make_settings(Path(tmp) / "eval.db"))
        try:
            return _score(agent, cases, gate)
        finally:
            agent.store.close()


def main() -> int:
    """Print the report and return 0 only when every gated score passes."""
    report = run_eval()
    print(report.render())
    return 0 if report.ok else 1


def _score(agent: SupportAgent, cases: list[EvalCase], gate: dict[str, float]) -> EvalReport:
    intent_pass = 0
    tool_pass = 0
    policy_pass = 0
    failures: list[str] = []
    for case in cases:
        before = _balances(agent)
        try:
            result = agent.handle(case.ticket)
        except Exception as exc:
            failures.append(f"{case.id}: agent error {type(exc).__name__}: {exc}")
            continue
        after = _balances(agent)
        intent_ok = result.intent == case.expected.intent
        tool_ok, tool_reason = score_tool_choice(
            case.expected.required_tools,
            case.expected.forbidden_tools,
            result.tools,
        )
        violations = _policy_violations(agent, case, result, before, after)
        if intent_ok:
            intent_pass += 1
        if tool_ok:
            tool_pass += 1
        if not violations:
            policy_pass += 1
        parts: list[str] = []
        if not intent_ok:
            parts.append(f"intent expected {case.expected.intent.value} got {result.intent.value}")
        if not tool_ok:
            parts.append(tool_reason)
            parts.append("tools=" + ",".join(result.tools))
        parts.extend(violations)
        if parts:
            failures.append(f"{case.id}: " + "; ".join(parts))
    total = len(cases)
    metrics = [
        MetricScore("intent_accuracy", intent_pass, total, gate["intent_accuracy"]),
        MetricScore("tool_choice", tool_pass, total, gate["tool_choice"]),
        MetricScore("policy_compliance", policy_pass, total, gate["policy_compliance"]),
    ]
    return EvalReport(cases=total, metrics=metrics, failures=failures)


def _balances(agent: SupportAgent) -> dict[str, int]:
    return {order.id: order.refunded_cents for order in agent.store.list_orders()}


def _policy_violations(
    agent: SupportAgent,
    case: EvalCase,
    result: AgentResult,
    before: dict[str, int],
    after: dict[str, int],
) -> list[str]:
    expected = case.expected
    violations: list[str] = []
    actual_status = result.refund_status.value if result.refund_status else "none"
    if actual_status != expected.refund_status:
        violations.append(f"refund status expected {expected.refund_status} got {actual_status}")
    if (
        expected.refund_amount_cents is not None
        and result.refund_amount_cents != expected.refund_amount_cents
    ):
        violations.append(
            "refund amount expected "
            f"{expected.refund_amount_cents} got {result.refund_amount_cents}"
        )
    if result.ticket_status.value != expected.ticket_status:
        violations.append(
            f"ticket status expected {expected.ticket_status} got {result.ticket_status.value}"
        )
    if result.escalated != expected.escalated:
        violations.append(f"escalated expected {expected.escalated} got {result.escalated}")
    stored = agent.store.get_ticket(case.id)
    if stored is None:
        violations.append("ticket row missing")
    elif stored.status != expected.ticket_status:
        violations.append(f"helpdesk status expected {expected.ticket_status} got {stored.status}")
    violations.extend(_balance_violations(agent, result, before, after))
    violations.extend(_record_violations(agent, case, actual_status))
    violations.extend(_audit_violations(result))
    violations.extend(_reply_violations(result))
    return violations


def _balance_violations(
    agent: SupportAgent,
    result: AgentResult,
    before: dict[str, int],
    after: dict[str, int],
) -> list[str]:
    violations: list[str] = []
    keys = set(before) | set(after)
    changed = {
        key: (before.get(key), after.get(key)) for key in keys if before.get(key) != after.get(key)
    }
    status = result.refund_status.value if result.refund_status else "none"
    amount = result.refund_amount_cents
    limit = agent.settings.auto_approve_limit_cents
    if status == "approved":
        if amount is None or amount <= 0 or amount > limit:
            violations.append(f"approved refund {amount} is outside 1..{limit}")
        if result.escalated:
            violations.append("approved refund was also escalated")
        if set(changed) != {result.order_id}:
            violations.append(f"approved refund changed balances {changed}")
        elif amount is not None and result.order_id is not None:
            start, end = changed[result.order_id]
            if start is None or end is None or end - start != amount:
                violations.append("balance delta does not match the refund amount")
    elif changed:
        violations.append(f"balances changed without an approved refund: {changed}")
    for order in agent.store.list_orders():
        if order.refunded_cents > order.total_cents or order.refunded_cents < 0:
            violations.append(f"{order.id} balance is invalid")
    return violations


def _record_violations(agent: SupportAgent, case: EvalCase, actual_status: str) -> list[str]:
    violations: list[str] = []
    expected = case.expected
    refunds = agent.store.list_refunds_for_ticket(case.id)
    escalations = agent.store.list_escalations_for_ticket(case.id)
    if expected.refund_status == "none":
        if refunds:
            violations.append("refund row exists when none was expected")
    elif len(refunds) != 1:
        violations.append(f"expected one refund row, found {len(refunds)}")
    else:
        refund = refunds[0]
        if refund.status != expected.refund_status:
            violations.append(
                f"stored refund status expected {expected.refund_status} got {refund.status}"
            )
        if (
            expected.refund_amount_cents is not None
            and refund.amount_cents != expected.refund_amount_cents
        ):
            violations.append("stored refund amount mismatch")
        if expected.refund_status == "pending_approval":
            approval = agent.store.get_approval(refund.id)
            if approval is None or approval.status != "pending":
                violations.append("pending refund is missing an open approval")
            if refund.amount_cents <= agent.settings.auto_approve_limit_cents:
                violations.append("pending refund is not above the auto-approve limit")
    if actual_status == "pending_approval" and expected.refund_status != "pending_approval":
        violations.append("refund was parked but the label expected a different status")
    if expected.escalated and not escalations:
        violations.append("expected an escalation row")
    if not expected.escalated and escalations:
        violations.append("unexpected escalation row")
    return violations


def _audit_violations(result: AgentResult) -> list[str]:
    violations: list[str] = []
    missing = [name for name in _AUDIT_EVENTS if name not in result.audit_event_types]
    if missing:
        violations.append("audit missing " + ", ".join(missing))
    calls = result.audit_event_types.count("tool_call")
    results = result.audit_event_types.count("tool_result")
    if calls != results:
        violations.append("tool_call and tool_result counts differ")
    return violations


def _reply_violations(result: AgentResult) -> list[str]:
    if not result.reply.strip():
        return ["reply is empty"]
    if not result.policy_titles or not any(title in result.reply for title in result.policy_titles):
        return ["reply does not cite a retrieved policy"]
    return []


if __name__ == "__main__":
    raise SystemExit(main())
