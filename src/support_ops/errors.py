"""Errors raised by the support operations demo."""


class SupportOpsError(Exception):
    """Base error for the support operations demo."""


class LLMError(SupportOpsError):
    """The language model request failed or returned invalid JSON."""


class RefundError(SupportOpsError):
    """A refund decision could not be applied."""


class NotFoundError(SupportOpsError):
    """The requested record does not exist."""


class AgentError(SupportOpsError):
    """The agent could not complete a ticket."""
