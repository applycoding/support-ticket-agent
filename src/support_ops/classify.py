"""Keyword classifier used by the offline mock provider."""

from support_ops.models import Intent, Urgency
from support_ops.util import extract_order_id

INTENT_RULES: list[tuple[Intent, tuple[str, ...]]] = [
    (
        Intent.ACCOUNT_ACCESS,
        (
            "can't log in",
            "cannot log in",
            "locked out",
            "password",
            "hacked",
            "sign in",
            "account access",
        ),
    ),
    (
        Intent.BILLING_DISPUTE,
        (
            "charged twice",
            "double charge",
            "double charged",
            "unauthorized charge",
            "billing",
            "invoice",
        ),
    ),
    (
        Intent.PRODUCT_DEFECT,
        (
            "broken",
            "defective",
            "doesn't work",
            "does not work",
            "damaged",
            "stopped working",
        ),
    ),
    (Intent.CANCELLATION, ("cancel",)),
    (
        Intent.SHIPPING_DELAY,
        (
            "lost package",
            "lost in transit",
            "delayed",
            "delay",
            "hasn't arrived",
            "has not arrived",
            "where is my package",
            "still in transit",
        ),
    ),
    (
        Intent.ORDER_STATUS,
        ("order status", "tracking", "where is my order", "when will"),
    ),
    (Intent.REFUND_REQUEST, ("refund", "money back")),
]

CRITICAL_PHRASES = ("hacked", "fraud", "unauthorized", "attorney", "lawyer", "chargeback")
HIGH_PHRASES = ("urgent", "asap", "furious", "lost package", "broken", "defective", "damaged")
MEDIUM_PHRASES = ("refund", "money back", "delayed", "delay", "cancel", "charged twice")


def classify_text(subject: str, body: str) -> dict:
    """Classify a ticket from subject and body. The result is deterministic."""
    text = f"{subject}\n{body}".lower()
    intent = Intent.GENERAL_INQUIRY
    for candidate, phrases in INTENT_RULES:
        if any(phrase in text for phrase in phrases):
            intent = candidate
            break
    urgency = _urgency(text)
    return {
        "intent": intent.value,
        "urgency": urgency.value,
        "order_id": extract_order_id(f"{subject}\n{body}"),
        "rationale": f"Matched intent {intent.value} with urgency {urgency.value}.",
    }


def _urgency(text: str) -> Urgency:
    if any(phrase in text for phrase in CRITICAL_PHRASES):
        return Urgency.CRITICAL
    if any(phrase in text for phrase in HIGH_PHRASES):
        return Urgency.HIGH
    if any(phrase in text for phrase in MEDIUM_PHRASES):
        return Urgency.MEDIUM
    return Urgency.LOW
