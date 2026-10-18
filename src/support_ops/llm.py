"""Provider-agnostic JSON LLM client. The default backend is a deterministic mock."""

import json
from typing import Protocol

import httpx

from support_ops.classify import classify_text
from support_ops.config import Settings
from support_ops.errors import LLMError
from support_ops.models import Provider, Task
from support_ops.planner import plan_actions, select_grounding_actions
from support_ops.replies import render_reply
from support_ops.util import assert_never

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class LLMClient(Protocol):
    """JSON completion used by the agent."""

    name: str

    def complete(self, *, task: str, system: str, user: str, context: dict) -> dict:
        """Return a JSON object for the named task."""


class MockLLM:
    """Offline stand-in. Same ticket in, same JSON out, with no network."""

    name = "mock"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def complete(self, *, task: str, system: str, user: str, context: dict) -> dict:
        del system, user
        try:
            selected = Task(task)
        except ValueError as exc:
            raise LLMError(f"Unknown LLM task: {task}") from exc
        match selected:
            case Task.CLASSIFY:
                ticket = context["ticket"]
                return classify_text(str(ticket["subject"]), str(ticket["body"]))
            case Task.SELECT_TOOLS:
                return {"actions": select_grounding_actions(context)}
            case Task.PLAN:
                return {"actions": plan_actions(context, self._settings)}
            case Task.DRAFT:
                return {"reply": render_reply(context)}
            case _ as unreachable:
                assert_never(unreachable)


class OpenAIClient:
    """Chat Completions client. Temperature is 0 and the response is a JSON object."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._transport = transport

    def complete(self, *, task: str, system: str, user: str, context: dict) -> dict:
        del task
        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user or json.dumps(context)},
            ],
        }
        response = _post(
            OPENAI_URL,
            payload,
            {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            self._transport,
            "OpenAI",
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("OpenAI response did not include message content") from exc
        if not isinstance(content, str):
            raise LLMError("OpenAI message content was empty")
        return parse_json_content(content)


class AnthropicClient:
    """Messages API client. Temperature is 0."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._transport = transport

    def complete(self, *, task: str, system: str, user: str, context: dict) -> dict:
        del task
        payload = {
            "model": self._model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user or json.dumps(context)}],
        }
        response = _post(
            ANTHROPIC_URL,
            payload,
            {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            self._transport,
            "Anthropic",
        )
        try:
            content = response["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Anthropic response did not include text content") from exc
        if not isinstance(content, str):
            raise LLMError("Anthropic text content was empty")
        return parse_json_content(content)


def build_llm(settings: Settings) -> LLMClient:
    """Return the configured client. A missing key falls back to the mock."""
    try:
        provider = Provider(settings.llm_provider)
    except ValueError as exc:
        raise LLMError(f"Unknown LLM provider: {settings.llm_provider}") from exc
    match provider:
        case Provider.MOCK:
            return MockLLM(settings)
        case Provider.OPENAI:
            if not settings.openai_api_key:
                return MockLLM(settings)
            return OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)
        case Provider.ANTHROPIC:
            if not settings.anthropic_api_key:
                return MockLLM(settings)
            return AnthropicClient(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
            )
        case _ as unreachable:
            assert_never(unreachable)


def parse_json_content(content: str) -> dict:
    """Parse a JSON object, including one wrapped in a markdown fence."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError("LLM response was not valid JSON") from exc
    if not isinstance(data, dict):
        raise LLMError("LLM JSON must be an object")
    return data


def _post(
    url: str,
    payload: dict,
    headers: dict[str, str],
    transport: httpx.BaseTransport | None,
    provider_name: str,
) -> dict:
    try:
        with httpx.Client(transport=transport, timeout=30) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"{provider_name} request failed") from exc
    if response.status_code >= 400:
        raise LLMError(f"{provider_name} request failed with status {response.status_code}")
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise LLMError(f"{provider_name} response was not JSON") from exc
    if not isinstance(body, dict):
        raise LLMError(f"{provider_name} response JSON must be an object")
    return body
