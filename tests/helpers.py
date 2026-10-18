"""Shared lookup for labeled eval tickets."""

from support_ops.eval_harness import load_dataset
from support_ops.models import EvalCase, Ticket


def load_case(case_id: str) -> EvalCase:
    for case in load_dataset():
        if case.id == case_id:
            return case
    raise AssertionError(f"Missing eval case {case_id}")


def load_ticket(case_id: str) -> Ticket:
    return load_case(case_id).ticket
