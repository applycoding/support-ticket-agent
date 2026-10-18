"""Mock routing and the HTTP clients, with no live network calls."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from support_ops.config import make_settings
from support_ops.errors import LLMError
from support_ops.llm import (
    ANTHROPIC_URL,
    OPENAI_URL,
    AnthropicClient,
    MockLLM,
    OpenAIClient,
    build_llm,
    parse_json_content,
)
from tests.helpers import load_dataset


def test_mock_classifier_matches_labels(tmp_path: Path) -> None:
    client = build_llm(make_settings(tmp_path / "unused.db"))
    assert isinstance(client, MockLLM)
    for case in load_dataset():
        raw = client.complete(
            task="classify",
            system="system",
            user="user",
            context={"ticket": case.ticket.model_dump(mode="json")},
        )
        assert raw["intent"] == case.expected.intent.value, case.id
        assert raw["urgency"] == case.expected.urgency.value, case.id
        assert raw["order_id"] == case.expected.order_id, case.id


def test_missing_keys_fall_back_to_mock(tmp_path: Path) -> None:
    base = make_settings(tmp_path / "unused.db")
    openai = build_llm(replace(base, llm_provider="openai", openai_api_key=None))
    anthropic = build_llm(replace(base, llm_provider="anthropic", anthropic_api_key=None))
    assert isinstance(openai, MockLLM)
    assert isinstance(anthropic, MockLLM)


def test_keys_select_live_clients(tmp_path: Path) -> None:
    base = make_settings(tmp_path / "unused.db")
    openai = build_llm(replace(base, llm_provider="openai", openai_api_key="test-key"))
    anthropic = build_llm(replace(base, llm_provider="anthropic", anthropic_api_key="test-key"))
    assert isinstance(openai, OpenAIClient)
    assert openai.name == "openai"
    assert isinstance(anthropic, AnthropicClient)
    assert anthropic.name == "anthropic"


def test_parse_json_strips_fences_and_rejects_arrays() -> None:
    assert parse_json_content('```json\n{"reply": "Hi"}\n```') == {"reply": "Hi"}
    with pytest.raises(LLMError):
        parse_json_content("[1, 2]")


def test_openai_client_parses_json_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENAI_URL
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content.decode())
        assert body["temperature"] == 0
        assert body["model"] == "gpt-4o-mini"
        assert body["response_format"] == {"type": "json_object"}
        content = json.dumps({"reply": "Hello Maya"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = OpenAIClient(
        api_key="test-key",
        model="gpt-4o-mini",
        transport=httpx.MockTransport(handler),
    )
    assert client.complete(task="draft", system="sys", user="user", context={}) == {
        "reply": "Hello Maya"
    }


def test_anthropic_client_parses_text_block() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ANTHROPIC_URL
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content.decode())
        assert body["temperature"] == 0
        assert body["max_tokens"] == 1024
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"reply": "Hello"}'}]},
        )

    client = AnthropicClient(
        api_key="test-key",
        model="claude-3-5-sonnet-latest",
        transport=httpx.MockTransport(handler),
    )
    assert client.complete(task="draft", system="sys", user="user", context={}) == {
        "reply": "Hello"
    }


def test_openai_http_error() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(401, json={"error": "no"}))
    client = OpenAIClient(api_key="test-key", model="gpt-4o-mini", transport=transport)
    with pytest.raises(LLMError):
        client.complete(task="draft", system="sys", user="user", context={})
