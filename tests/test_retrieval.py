"""Keyword retrieval over the policy files."""

from support_ops.config import repo_root
from support_ops.retrieval import PolicyRetriever


def _retriever() -> PolicyRetriever:
    return PolicyRetriever(repo_root() / "data" / "policies")


def test_refund_query_hits_refund_policy() -> None:
    hits = _retriever().search("change of mind refund window delivery")
    assert hits
    assert hits[0].title.startswith("Refund Policy")


def test_shipping_query_hits_shipping_policy() -> None:
    hits = _retriever().search("courtesy credit lost package transit")
    assert hits
    assert hits[0].title.startswith("Shipping Policy")


def test_escalation_query_hits_escalation_policy() -> None:
    hits = _retriever().search("account compromise specialist queue")
    assert hits
    assert hits[0].title.startswith("Escalation Policy")


def test_search_is_stable_and_never_empty() -> None:
    retriever = _retriever()
    first = retriever.search("refund window")
    assert first == retriever.search("refund window")
    assert retriever.search("zzzz-unmatched-token")
