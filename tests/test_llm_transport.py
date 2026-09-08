import json
import sys
from pathlib import Path
from queue import Queue

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from llm import (  # noqa: E402
    AnthropicMessagesTransport,
    LLMProvider,
    OpenAICompatibleTransport,
    OpenAIResponsesTransport,
    ResponsesStateMode,
    TransportEvent,
    chat_messages_to_anthropic,
    detect_provider,
    merge_streamed_tool_name,
    normalize_provider,
    normalize_responses_state_mode,
    resolve_provider,
    resolve_profile_provider,
)
from llm.responses import responses_capability_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_responses_capability_cache():
    responses_capability_cache.clear()
    yield
    responses_capability_cache.clear()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@ai-sdk/openai", LLMProvider.OPENAI),
        ("responses", LLMProvider.OPENAI),
        ("@ai-sdk/openai-compatible", LLMProvider.OPENAI_COMPATIBLE),
        ("local", LLMProvider.OPENAI_COMPATIBLE),
        ("@ai-sdk/anthropic", LLMProvider.ANTHROPIC),
    ],
)
def test_provider_aliases(value, expected):
    assert normalize_provider(value) is expected


def test_auto_provider_detection_is_host_first_and_conservative():
    assert detect_provider("https://api.openai.com/v1", "gpt-5") is LLMProvider.OPENAI
    assert detect_provider("https://api.anthropic.com", "claude-sonnet-4") is LLMProvider.ANTHROPIC
    assert (
        detect_provider("https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4")
        is LLMProvider.OPENAI_COMPATIBLE
    )


def test_explicit_openai_profile_uses_responses_on_custom_host():
    # After semantics change: "openai" => chat (openai-compatible), "openai-responses" => responses (openai)
    profile_chat = {
        "llm_type": "openai",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "muse-spark-1.2-contributor",
    }
    profile_responses = {
        "llm_type": "openai-responses",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "muse-spark-1.2-contributor",
    }

    assert resolve_profile_provider(profile_chat) is LLMProvider.OPENAI_COMPATIBLE
    assert resolve_profile_provider(profile_responses) is LLMProvider.OPENAI


def test_custom_openai_profile_is_exposed_as_responses():
    import model_profiles

    # "openai" now means chat (openai-compatible)
    profile_chat = {
        "id": "legacy",
        "llm_type": "openai",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "muse-spark-1.2-contributor",
        "api_key": "secret",
        "context_window": 1000,
        "max_output_tokens": 100,
    }
    public_chat = model_profiles.public_profile(profile_chat)
    assert public_chat["llm_type"] == "openai-compatible"
    # "openai-responses" means responses
    profile_responses = dict(profile_chat)
    profile_responses["llm_type"] = "openai-responses"
    public = model_profiles.public_profile(profile_responses)
    assert public["llm_type"] == "openai-responses"
    assert "legacy_llm_type" not in public
    assert (
        resolve_provider("auto", "http://localhost:11434/v1", "qwen3")
        is LLMProvider.OPENAI_COMPATIBLE
    )


def test_responses_issuer_isolated_by_endpoint_model_and_credential_scope():
    client = type("Client", (), {"responses": object()})()
    base = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        credential_scope="credential-a",
    )
    same = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1/",
        model="gpt-test",
        credential_scope="credential-a",
    )
    other_key = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        credential_scope="credential-b",
    )
    other_model = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-other",
        credential_scope="credential-a",
    )

    assert base.issuer == same.issuer
    assert base.issuer != other_key.issuer
    assert base.issuer != other_model.issuer


def test_unknown_manual_provider_is_rejected():
    with pytest.raises(ValueError, match="unsupported LLM provider"):
        normalize_provider("openai-respones")


def test_responses_state_mode_aliases_and_validation():
    assert normalize_responses_state_mode("previous_response_id") is ResponsesStateMode.STATEFUL
    assert normalize_responses_state_mode("replay") is ResponsesStateMode.STATELESS
    with pytest.raises(ValueError, match="Responses state mode"):
        normalize_responses_state_mode("magic")


def test_official_responses_stateful_request_sends_only_items_after_previous_response():
    calls = []

    class _Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            response_id = f"resp_{len(calls)}"
            return iter([{
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "status": "completed",
                    "model": "gpt-test",
                    "output": [{
                        "id": f"msg_{len(calls)}",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }],
                },
            }])

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
    )
    first_messages = [
        {"role": "system", "content": "Always be concise."},
        {"role": "user", "content": "first"},
    ]
    first_events = list(
        transport.stream_completion(model="gpt-test", messages=first_messages, max_tokens=32)
    )
    provider_state = next(
        event.provider_data for event in first_events if event.kind == "provider_state"
    )
    provider_state = json.loads(json.dumps(provider_state))
    assert provider_state["schema_version"] == 2

    messages = [
        *first_messages,
        {
            "role": "assistant",
            "content": "done",
            "_myagent_responses": provider_state,
        },
        {"role": "user", "content": "second"},
    ]

    restarted_transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
    )
    list(restarted_transport.stream_completion(
        model="gpt-test", messages=messages, max_tokens=32
    ))

    captured = calls[-1]
    assert captured["store"] is True
    assert captured["previous_response_id"] == "resp_1"
    assert captured["instructions"] == "Always be concise."
    assert captured["input"] == [{"role": "user", "content": "second"}]
    assert "include" not in captured


def test_custom_responses_stateless_request_replays_native_items():
    captured = {}

    class _Responses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return iter([])

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://proxy.example/v1",
        model="muse-test",
        state_mode="stateless",
    )
    reasoning_item = {
        "id": "rs_1",
        "type": "reasoning",
        "encrypted_content": "ciphertext",
        "summary": [],
    }
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "_myagent_responses": {
                "issuer": transport.issuer,
                "response_id": "resp_1",
                "output_items": [reasoning_item],
            },
        },
        {"role": "user", "content": "second"},
    ]

    list(transport.stream_completion(model="muse-test", messages=messages, max_tokens=32))

    assert captured["store"] is False
    assert captured["include"] == ["reasoning.encrypted_content"]
    assert captured["input"] == [
        {"role": "user", "content": "first"},
        reasoning_item,
        {"role": "user", "content": "second"},
    ]
    assert captured["prompt_cache_key"].startswith("myagent-")
    assert "previous_response_id" not in captured


def test_auto_stateful_invalid_previous_response_falls_back_to_stateless_replay():
    calls = []
    fail_previous = False

    class _Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            if fail_previous and kwargs.get("previous_response_id"):
                raise RuntimeError("invalid previous_response_id: response not found")
            return iter([{
                "type": "response.completed",
                "response": {
                    "id": "resp_recovered" if fail_previous else "resp_expired",
                    "status": "completed",
                    "model": "gpt-test",
                    "output": [native_item],
                },
            }])

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
    )
    native_item = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "first answer"}],
    }
    first_events = list(transport.stream_completion(
        model="gpt-test",
        messages=[{"role": "user", "content": "first"}],
        max_tokens=32,
    ))
    provider_state = next(
        event.provider_data for event in first_events if event.kind == "provider_state"
    )
    calls.clear()
    fail_previous = True
    messages = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "first answer",
            "_myagent_responses": provider_state,
        },
        {"role": "user", "content": "second"},
    ]

    list(transport.stream_completion(model="gpt-test", messages=messages, max_tokens=32))

    assert len(calls) == 2
    assert calls[0]["previous_response_id"] == "resp_expired"
    assert calls[1]["store"] is True
    assert "previous_response_id" not in calls[1]
    assert native_item in calls[1]["input"]
    assert responses_capability_cache.get(transport.issuer).previous_response_id is True


def test_auto_custom_responses_uses_stateless_replay_without_stateful_probe():
    calls = []

    class _Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return iter([{
                "type": "response.completed",
                "response": {
                    "id": "resp_stateless",
                    "status": "completed",
                    "model": "proxy-model",
                    "output": [{
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }],
                },
            }])

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://proxy.example/v1",
        model="proxy-model",
    )

    first_events = list(transport.stream_completion(
        model="proxy-model",
        messages=[{"role": "user", "content": "first"}],
        max_tokens=32,
    ))
    provider_state = next(
        event.provider_data
        for event in first_events
        if event.kind == "provider_state"
    )
    assert provider_state["stateful_supported"] is False
    assert "fallback_reason" not in provider_state
    assert [call["store"] for call in calls] == [False]

    calls.clear()
    list(transport.stream_completion(
        model="proxy-model",
        messages=[
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "content": "ok",
                "_myagent_responses": provider_state,
            },
            {"role": "user", "content": "second"},
        ],
        max_tokens=32,
    ))

    assert len(calls) == 1
    assert calls[0]["store"] is False
    assert "previous_response_id" not in calls[0]


def test_legacy_stateful_setting_cannot_force_custom_proxy_continuation():
    calls = []
    fail_previous = False

    class _Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            if fail_previous and kwargs.get("previous_response_id"):
                raise RuntimeError(
                    "referenced response not found or expired"
                )
            return iter([{
                "type": "response.completed",
                "response": {
                    "id": "resp_recovered" if fail_previous else "resp_missing",
                    "status": "completed",
                    "model": "muse-test",
                    "output": [native_item],
                },
            }])

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://proxy.example/v1",
        model="muse-test",
        state_mode="stateful",
    )
    native_item = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "first answer"}],
    }
    first_events = list(transport.stream_completion(
        model="muse-test",
        messages=[{"role": "user", "content": "first"}],
        max_tokens=32,
    ))
    provider_state = next(
        event.provider_data for event in first_events if event.kind == "provider_state"
    )
    calls.clear()
    fail_previous = True
    messages = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "first answer",
            "_myagent_responses": provider_state,
        },
        {"role": "user", "content": "second"},
    ]

    events = list(transport.stream_completion(
        model="muse-test",
        messages=messages,
        max_tokens=32,
    ))

    assert len(calls) == 1
    assert calls[0]["store"] is False
    assert "previous_response_id" not in calls[0]
    provider_state = next(
        event.provider_data for event in events if event.kind == "provider_state"
    )
    assert provider_state["state_mode"] == "stateless"
    assert "fallback_reason" not in provider_state


def test_stateless_proxy_retries_once_without_unsupported_encrypted_include():
    calls = []

    class _IncludeError(RuntimeError):
        status_code = 400
        body = {
            "error": {
                "param": "include",
                "type": "invalid_request_error",
                "message": "Unsupported value reasoning.encrypted_content",
            }
        }

    class _Responses:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            if "include" in kwargs:
                raise _IncludeError("unsupported include")
            return iter([{
                "type": "response.completed",
                "response": {
                    "id": "resp_ok",
                    "status": "completed",
                    "model": "proxy-model",
                    "output": [],
                },
            }])

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://proxy.example/v1",
        model="proxy-model",
    )

    events = list(transport.stream_completion(
        model="proxy-model",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=32,
    ))

    assert len(calls) == 2
    assert calls[0]["include"] == ["reasoning.encrypted_content"]
    assert "include" not in calls[1]
    state = next(event.provider_data for event in events if event.kind == "provider_state")
    assert state["fallback_reason"] == "unsupported_encrypted_reasoning"
    assert responses_capability_cache.get(transport.issuer).encrypted_reasoning_replay is False

    list(transport.stream_completion(
        model="proxy-model",
        messages=[{"role": "user", "content": "next"}],
        max_tokens=32,
    ))
    assert len(calls) == 3
    assert "include" not in calls[2]


def test_responses_native_compact_checkpoint_is_used_for_next_request():
    create_calls = []
    compact_calls = []

    class _Responses:
        @staticmethod
        def compact(**kwargs):
            compact_calls.append(kwargs)
            return {
                "output": [
                    {"type": "message", "role": "user", "content": "first"},
                    {"type": "compaction", "encrypted_content": "opaque"},
                ],
                "usage": {"input_tokens": 120, "output_tokens": 24},
            }

        @staticmethod
        def create(**kwargs):
            create_calls.append(kwargs)
            return iter([{
                "type": "response.completed",
                "response": {
                    "id": "resp_after_compact",
                    "status": "completed",
                    "model": "gpt-test",
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }],
                },
            }])

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
    )
    checkpoint = transport.compact_history(
        model="gpt-test",
        messages=[{"role": "user", "content": "first"}],
        request_context={"session_id": "s1", "history_generation": 3},
        source_estimated_tokens=120,
    )

    events = list(transport.stream_completion(
        model="gpt-test",
        messages=[
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ],
        request_context={
            "session_id": "s1",
            "history_generation": 3,
            "responses_compaction": checkpoint.to_dict(),
        },
        max_tokens=32,
    ))

    assert compact_calls[0]["input"] == [{"role": "user", "content": "first"}]
    assert create_calls[0]["input"] == [
        {"type": "message", "role": "user", "content": "first"},
        {"type": "compaction", "encrypted_content": "opaque"},
        {"role": "user", "content": "second"},
    ]
    assert create_calls[0]["store"] is True
    assert "previous_response_id" not in create_calls[0]
    state = next(event.provider_data for event in events if event.kind == "provider_state")
    assert state["responses_mode"] == "compacted_store"

    list(transport.stream_completion(
        model="gpt-test",
        messages=[
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
            {
                "role": "assistant",
                "content": "done",
                "_myagent_responses": state,
            },
            {"role": "user", "content": "third"},
        ],
        request_context={
            "session_id": "s1",
            "history_generation": 3,
            "responses_compaction": checkpoint.to_dict(),
        },
        max_tokens=32,
    ))
    assert create_calls[1]["previous_response_id"] == "resp_after_compact"
    assert create_calls[1]["input"] == [{"role": "user", "content": "third"}]


def test_responses_compact_unsupported_is_cached_per_issuer():
    calls = 0

    class _CompactError(RuntimeError):
        status_code = 404

    class _Responses:
        @staticmethod
        def compact(**_kwargs):
            nonlocal calls
            calls += 1
            raise _CompactError("unknown endpoint")

    transport = OpenAIResponsesTransport(
        type("Client", (), {"responses": _Responses()})(),
        base_url="https://proxy.example/v1",
        model="muse-test",
    )

    with pytest.raises(_CompactError):
        transport.compact_history(model="muse-test", messages=[])
    with pytest.raises(RuntimeError, match="disabled for this issuer"):
        transport.compact_history(model="muse-test", messages=[])

    assert calls == 1


def test_fresh_compaction_checkpoint_supersedes_older_valid_anchor_once():
    create_calls = []

    class _Responses:
        @staticmethod
        def create(**kwargs):
            create_calls.append(kwargs)
            return iter([{
                "type": "response.completed",
                "response": {
                    "id": f"resp_{len(create_calls)}",
                    "status": "completed",
                    "model": "gpt-test",
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }],
                },
            }])

        @staticmethod
        def compact(**_kwargs):
            return {
                "output": [{"type": "compaction", "encrypted_content": "opaque"}],
                "usage": {"input_tokens": 500, "output_tokens": 50},
            }

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
    )
    first_events = list(transport.stream_completion(
        model="gpt-test",
        messages=[{"role": "user", "content": "first"}],
        request_context={"session_id": "s1", "history_generation": 2},
        max_tokens=32,
    ))
    old_state = next(
        event.provider_data for event in first_events if event.kind == "provider_state"
    )
    current_messages = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "done",
            "_myagent_responses": old_state,
        },
        {"role": "user", "content": "second"},
    ]
    checkpoint = transport.compact_history(
        model="gpt-test",
        messages=current_messages,
        request_context={"session_id": "s1", "history_generation": 2},
        source_estimated_tokens=500,
    )

    list(transport.stream_completion(
        model="gpt-test",
        messages=current_messages,
        request_context={
            "session_id": "s1",
            "history_generation": 2,
            "responses_compaction": checkpoint.to_dict(),
        },
        max_tokens=32,
    ))

    assert "previous_response_id" not in create_calls[1]
    assert create_calls[1]["input"] == [
        {"type": "compaction", "encrypted_content": "opaque"}
    ]


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (["ls", "ls", "ls"], "ls"),
        (["run_", "shell"], "run_shell"),
        (["get_", "get_weather"], "get_weather"),
    ],
)
def test_streamed_tool_name_accepts_repeated_snapshots_and_real_deltas(parts, expected):
    merged = ""
    for part in parts:
        merged = merge_streamed_tool_name(merged, part)
    assert merged == expected


def test_model_profile_persists_canonical_manual_provider(tmp_path):
    import model_profiles

    profile = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "claude-test",
            "llm_type": "@ai-sdk/anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    assert profile["llm_type"] == "anthropic"
    assert profile["responses_state_mode"] == "auto"
    assert model_profiles.is_usable_profile(profile) is True


def test_model_profile_persists_provider_semantics_v2_values(tmp_path, monkeypatch):
    import model_profiles

    # Explicit choices must never hit the network; only ``auto`` probes.
    probes = []

    def _fake_detect(base_url, api_key, model_id, **_kwargs):
        probes.append((base_url, model_id))
        return None

    monkeypatch.setattr(model_profiles, "detect_wire_protocol", _fake_detect)

    responses = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "muse-test",
            "llm_type": "openai-responses",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    # The explicit Responses choice must survive persistence instead of
    # being flattened onto the legacy ``openai`` enum value.
    assert responses["llm_type"] == "openai-responses"

    chat = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "chat-test",
            "llm_type": "openai",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    # Provider semantics v2: a bare explicit ``openai`` selects chat.
    assert chat["llm_type"] == "openai-compatible"

    compatible = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "compat-test",
            "llm_type": "openai-compatible",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    assert compatible["llm_type"] == "openai-compatible"

    # Explicit selections never probe the endpoint.
    assert probes == []

    automatic = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "auto-test",
            "llm_type": "auto",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    # The fake detector cannot tell, so the profile keeps ``auto`` and the
    # endpoint inference applies on every load.
    assert automatic["llm_type"] == "auto"
    assert probes == [("https://opencode.ai/zen/go/v1", "auto-test")]


def test_auto_profile_persists_probed_wire_protocol(tmp_path, monkeypatch):
    import model_profiles

    def _fake_detect(base_url, api_key, model_id, **_kwargs):
        return "openai-responses"

    monkeypatch.setattr(model_profiles, "detect_wire_protocol", _fake_detect)

    profile = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "muse-probed",
            "llm_type": "auto",
            "base_url": "https://opencode.ai/zen/v1",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    assert profile["llm_type"] == "openai-responses"


def test_detect_wire_protocol_judges_routes_by_error_shape(monkeypatch):
    import httpx

    import model_profiles

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            # A request-level complaint proves the route exists.
            return httpx.Response(
                400,
                json={"error": {"message": "max_tokens is too large"}},
            )
        if request.url.path.endswith("/responses"):
            return httpx.Response(404, json={"error": "Not found"})
        return httpx.Response(500, text="boom")

    real_client = httpx.Client

    class MockClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(model_profiles.httpx, "Client", MockClient)
    detected = model_profiles.detect_wire_protocol(
        "https://gateway.example.com/v1", "k", "some-model"
    )
    assert detected == "openai-compatible"


def test_legacy_stateless_profile_migrates_to_storage_privacy_flag(tmp_path):
    import model_profiles

    profile = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "gpt-test",
            "llm_type": "openai",
            "responses_state_mode": "stateless",
            "base_url": "https://api.openai.com/v1",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    assert profile["responses_state_mode"] == "stateless"
    assert profile["responses_store_disabled"] is True
    assert model_profiles.public_profile(profile)["responses_state_mode"] == "stateless"
    assert model_profiles.public_profile(profile)["responses_store_disabled"] is True


def test_legacy_stateful_profile_is_migrated_to_automatic_mode(tmp_path):
    import model_profiles

    profile = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "gpt-test",
            "llm_type": "openai",
            "responses_state_mode": "stateful",
            "base_url": "https://api.openai.com/v1",
            "api_key": "secret",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    assert profile["responses_state_mode"] == "auto"
    assert profile["responses_store_disabled"] is False


def test_responses_profile_ui_exposes_only_storage_privacy_switch():
    html = (APP_DIR / "templates" / "advance_config.html").read_text(
        encoding="utf-8"
    )

    assert 'id="model-responses-store-disabled"' in html
    assert "Responses 会话状态" not in html
    assert "stateful（优先" not in html
    assert "responses_store_disabled:" in html


def test_auto_compatible_profile_can_use_an_authless_local_endpoint(tmp_path):
    import model_profiles

    profile = model_profiles.upsert_profile(
        tmp_path,
        {
            "model": "qwen3",
            "llm_type": "auto",
            "base_url": "http://localhost:11434/v1",
            "context_window": 1000,
            "max_output_tokens": 100,
        },
    )

    assert profile["llm_type"] == "auto"
    assert model_profiles.is_usable_profile(profile) is True


def test_responses_done_event_restores_tool_name_and_call_id_after_arguments():
    events = [
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": '{"city":"Bei',
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": 'jing"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc_internal",
                "call_id": "call_weather_1",
                "name": "get_weather",
                "arguments": '{"city":"Beijing"}',
            },
        },
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-test",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        },
    ]

    normalized = list(OpenAIResponsesTransport._events(events, fallback_model="fallback"))
    tool_events = [event for event in normalized if event.kind == "tool_call_delta"]

    assert "".join(event.arguments_delta for event in tool_events) == '{"city":"Beijing"}'
    assert tool_events[-1].tool_call_id == "call_weather_1"
    assert tool_events[-1].tool_name == "get_weather"
    assert normalized[-1].finish_reason == "tool_calls"


def test_responses_completed_snapshot_alone_restores_full_tool_call():
    events = [{
        "type": "response.completed",
        "response": {
            "id": "resp_tool",
            "status": "completed",
            "model": "gpt-test",
            "output": [{
                "type": "function_call",
                "id": "fc_internal",
                "call_id": "call_ls_1",
                "name": "ls",
                "arguments": '{"path":"/"}',
            }],
        },
    }]

    normalized = list(OpenAIResponsesTransport._events(events, fallback_model="fallback"))
    tool = next(event for event in normalized if event.kind == "tool_call_delta")

    assert tool.tool_call_id == "call_ls_1"
    assert tool.tool_name == "ls"
    assert tool.arguments_delta == '{"path":"/"}'
    assert normalized[-1].finish_reason == "tool_calls"


def test_responses_repeated_tool_metadata_is_emitted_once():
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "call_id": "call_1", "name": "ls"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "call_id": "call_1",
            "name": "ls",
            "delta": '{"path":"',
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "call_id": "call_1",
            "name": "ls",
            "delta": 'sessions"}',
        },
    ]

    tool_events = list(OpenAIResponsesTransport._events(events, fallback_model="m"))[:-1]
    assert [event.tool_name for event in tool_events] == ["ls", "", ""]
    assert tool_events[0].tool_call_id == "call_1"
    assert [event.tool_call_id for event in tool_events[1:]] == ["", ""]
    assert "".join(event.arguments_delta for event in tool_events) == '{"path":"sessions"}'


def test_responses_text_done_and_final_snapshots_restore_text_without_duplication():
    events = [
        {
            "type": "response.output_text.delta",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "Hello",
        },
        {
            "type": "response.output_text.done",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "text": "Hello world",
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg_1",
                "type": "message",
                "content": [{"type": "output_text", "text": "Hello world"}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "model": "actual-model",
                "output": [{
                    "id": "msg_1",
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Hello world"}],
                }],
            },
        },
    ]

    normalized = list(OpenAIResponsesTransport._events(events, fallback_model="fallback"))
    assert "".join(event.text for event in normalized if event.kind == "content_delta") == "Hello world"
    finish = normalized[-1]
    assert finish.kind == "finish"
    assert finish.model == "actual-model"


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "response.output_text.done",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "text": "done-only",
        },
        {
            "type": "response.content_part.done",
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "done-only"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "msg_1",
                "type": "message",
                "content": [{"type": "output_text", "text": "done-only"}],
            },
        },
    ],
)
def test_responses_done_only_variants_emit_visible_text(event):
    normalized = list(OpenAIResponsesTransport._events([event], fallback_model="m"))
    assert "".join(row.text for row in normalized if row.kind == "content_delta") == "done-only"


def test_responses_completed_snapshot_alone_emits_visible_text():
    events = [{
        "type": "response.completed",
        "response": {
            "id": "resp_snapshot",
            "status": "completed",
            "model": "m",
            "output": [{
                "id": "msg_snapshot",
                "type": "message",
                "content": [{"type": "output_text", "text": "snapshot-only"}],
            }],
        },
    }]

    normalized = list(OpenAIResponsesTransport._events(events, fallback_model="fallback"))
    assert "".join(row.text for row in normalized if row.kind == "content_delta") == "snapshot-only"


def test_responses_terminal_event_preserves_response_id_and_native_output_items():
    native_item = {
        "id": "rs_1",
        "type": "reasoning",
        "encrypted_content": "ciphertext",
        "summary": [],
    }
    events = [{
        "type": "response.completed",
        "response": {
            "id": "resp_native",
            "status": "completed",
            "model": "gpt-test",
            "output": [native_item],
        },
    }]

    normalized = list(OpenAIResponsesTransport._events(
        events,
        fallback_model="fallback",
        issuer="issuer-1",
        state_mode="stateless",
    ))
    state_event = next(row for row in normalized if row.kind == "provider_state")

    assert state_event.provider_data == {
        "api": "responses",
        "issuer": "issuer-1",
        "state_mode": "stateless",
        "response_id": "resp_native",
        "status": "completed",
        "output_items": [native_item],
    }


def test_official_responses_websocket_reuses_connection_and_reports_transport():
    websocket_create_calls = []
    connect_calls = []

    class _Connection:
        def __init__(self):
            self.response = self
            self.events = []
            self.closed = False

        def create(self, **body):
            websocket_create_calls.append(body)
            response_id = f"resp_ws_{len(websocket_create_calls)}"
            self.events = [{
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "status": "completed",
                    "model": "gpt-test",
                    "output": [],
                },
            }]

        def recv(self):
            return self.events.pop(0)

        def close(self):
            self.closed = True

    connection = _Connection()

    class _Manager:
        def enter(self):
            return connection

    class _Responses:
        def connect(self, **kwargs):
            connect_calls.append(kwargs)
            return _Manager()

        def create(self, **_body):
            raise AssertionError("HTTP should not be used after a healthy WebSocket")

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        websocket_mode="enabled",
    )

    first = list(transport.stream_completion(model="gpt-test", messages=[]))
    second = list(transport.stream_completion(model="gpt-test", messages=[]))

    assert len(connect_calls) == 1
    assert len(websocket_create_calls) == 2
    assert all("stream" not in body for body in websocket_create_calls)
    states = [event for event in first + second if event.kind == "provider_state"]
    assert [event.provider_data["wire_transport"] for event in states] == [
        "websocket",
        "websocket",
    ]
    assert responses_capability_cache.get(transport.issuer).websocket is True


def test_custom_responses_endpoint_never_probes_websocket_even_when_enabled():
    http_calls = []

    class _Responses:
        def connect(self, **_kwargs):
            raise AssertionError("custom Responses proxy must not be probed over WebSocket")

        def create(self, **body):
            http_calls.append(body)
            return [{
                "type": "response.completed",
                "response": {
                    "id": "resp_http",
                    "status": "completed",
                    "model": "muse-test",
                    "output": [],
                },
            }]

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://proxy.example/v1",
        model="muse-test",
        websocket_mode="enabled",
    )

    events = list(transport.stream_completion(model="muse-test", messages=[]))

    assert len(http_calls) == 1
    state = next(event for event in events if event.kind == "provider_state")
    assert state.provider_data["wire_transport"] == "http_sse"
    assert responses_capability_cache.get(transport.issuer).websocket is None


def test_websocket_404_is_cached_and_http_failure_is_not_retried_inside_transport():
    connect_calls = []
    http_calls = []

    class _HandshakeError(RuntimeError):
        status_code = 404

    class _Manager:
        def enter(self):
            raise _HandshakeError("server rejected WebSocket connection: HTTP 404")

    class _HTTPError(RuntimeError):
        status_code = 500

    class _Responses:
        def connect(self, **kwargs):
            connect_calls.append(kwargs)
            return _Manager()

        def create(self, **body):
            http_calls.append(body)
            raise _HTTPError("upstream failed")

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        websocket_mode="enabled",
    )

    with pytest.raises(_HTTPError):
        list(transport.stream_completion(model="gpt-test", messages=[]))
    with pytest.raises(_HTTPError):
        list(transport.stream_completion(model="gpt-test", messages=[]))

    assert len(connect_calls) == 1
    assert len(http_calls) == 2
    capabilities = responses_capability_cache.get(transport.issuer)
    assert capabilities.websocket is False
    assert capabilities.responses is None


def test_websocket_500_falls_back_for_one_request_without_poisoning_capability():
    connect_calls = []
    http_calls = []

    class _HandshakeError(RuntimeError):
        status_code = 500

    class _Manager:
        def enter(self):
            raise _HandshakeError("temporary WebSocket gateway failure")

    class _Responses:
        def connect(self, **kwargs):
            connect_calls.append(kwargs)
            return _Manager()

        def create(self, **body):
            http_calls.append(body)
            return [{
                "type": "response.completed",
                "response": {
                    "id": f"resp_http_{len(http_calls)}",
                    "status": "completed",
                    "model": "gpt-test",
                    "output": [],
                },
            }]

    client = type("Client", (), {"responses": _Responses()})()
    transport = OpenAIResponsesTransport(
        client,
        base_url="https://api.openai.com/v1",
        model="gpt-test",
        websocket_mode="enabled",
    )

    first = list(transport.stream_completion(model="gpt-test", messages=[]))
    second = list(transport.stream_completion(model="gpt-test", messages=[]))

    assert len(connect_calls) == 2
    assert len(http_calls) == 2
    assert responses_capability_cache.get(transport.issuer).websocket is None
    states = [event for event in first + second if event.kind == "provider_state"]
    assert all(event.provider_data["wire_transport"] == "http_sse" for event in states)
    assert all(
        event.provider_data["transport_fallback_reason"].startswith("websocket_error:")
        for event in states
    )


def test_openai_compatible_normalizes_repeated_names_and_cumulative_arguments():
    chunks = [
        {
            "model": "m",
            "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call_1",
                "function": {"name": "ls", "arguments": '{"path":"'},
            }]}, "finish_reason": None}],
        },
        {
            "model": "m",
            "choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "call_1",
                "function": {"name": "ls", "arguments": 'sessions"}'},
            }]}, "finish_reason": "tool_calls"}],
        },
    ]

    class _Completions:
        @staticmethod
        def create(**_kwargs):
            return iter(chunks)

    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _Completions()})()},
    )()
    events = list(OpenAICompatibleTransport(client).stream_completion(model="m", messages=[]))
    tools = [event for event in events if event.kind == "tool_call_delta"]
    assert [event.tool_name for event in tools] == ["ls", ""]
    assert tools[0].tool_call_id == "call_1"
    assert [event.tool_call_id for event in tools[1:]] == [""]
    # Chat Completions arguments chunks are pure deltas and must be appended
    # verbatim — a prefix-dropping merge would corrupt JSON whose fragments
    # repeat (e.g. every nested object opening `{"`).
    assert "".join(event.arguments_delta for event in tools) == '{"path":"sessions"}'


def test_stream_worker_keeps_late_responses_tool_metadata(monkeypatch):
    import agent_openai
    from agent_messages import UserMessage

    monkeypatch.setattr(agent_openai, "OPENAI_MAX_RETRIES", 1)
    monkeypatch.setattr(agent_openai, "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC", 0)

    class _Client:
        _myagent_transport_enabled = True
        _myagent_thinking_format = "canonical"
        _myagent_input_modalities = ["text"]
        _myagent_multimodal_input = False

        @staticmethod
        def stream_completion(**_kwargs):
            yield TransportEvent(
                "tool_call_delta", index=0, arguments_delta='{"city":"Bei'
            )
            yield TransportEvent(
                "tool_call_delta", index=0, arguments_delta='jing"}'
            )
            yield TransportEvent(
                "tool_call_delta",
                index=0,
                tool_call_id="call_weather_1",
                tool_name="get_weather",
            )
            yield TransportEvent("finish", finish_reason="tool_calls", model="gpt-test")

    queue = Queue()
    agent_openai.run_chat_completion_stream_worker(
        queue,
        _Client(),
        "gpt-test",
        [UserMessage(content="weather")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        temperature=0,
        max_tokens=128,
    )
    turn = None
    error = None
    while not queue.empty():
        item = queue.get()
        if item is None:
            continue
        if item[0] == "turn":
            turn = item[1]
        elif item[0] == "err":
            error = item[1]

    assert error is None
    assert turn is not None
    assert turn.tool_calls == [
        {
            "name": "get_weather",
            "args": {"city": "Beijing"},
            "id": "call_weather_1",
            "index": 0,
        }
    ]


def test_stream_worker_carries_responses_provider_state_to_turn(monkeypatch):
    import agent_openai
    from agent_messages import UserMessage

    monkeypatch.setattr(agent_openai, "OPENAI_MAX_RETRIES", 1)
    monkeypatch.setattr(agent_openai, "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC", 0)
    provider_data = {
        "api": "responses",
        "issuer": "issuer-1",
        "response_id": "resp_1",
        "output_items": [{"id": "msg_1", "type": "message", "role": "assistant", "content": []}],
    }

    class _Client:
        _myagent_transport_enabled = True
        _myagent_thinking_format = "canonical"
        _myagent_input_modalities = ["text"]
        _myagent_multimodal_input = False

        @staticmethod
        def stream_completion(**_kwargs):
            yield TransportEvent("content_delta", text="ok")
            yield TransportEvent("provider_state", provider_data=provider_data)
            yield TransportEvent("finish", finish_reason="stop", model="gpt-test")

    queue = Queue()
    agent_openai.run_chat_completion_stream_worker(
        queue,
        _Client(),
        "gpt-test",
        [UserMessage(content="hello")],
        temperature=0,
        max_tokens=32,
    )
    turn = None
    while not queue.empty():
        item = queue.get()
        if item and item[0] == "turn":
            turn = item[1]

    assert turn is not None
    assert turn.content == "ok"
    assert turn.provider_data == provider_data


def test_assistant_message_serialization_keeps_internal_responses_state():
    from agent_messages import AssistantMessage
    from agent_openai import messages_to_openai_params

    state = {"issuer": "issuer-1", "response_id": "resp_1", "output_items": []}
    params = messages_to_openai_params([
        AssistantMessage(
            content="ok",
            additional_kwargs={"_myagent_responses": state},
        )
    ])

    assert params[0]["_myagent_responses"] == state


def test_responses_complete_text_uses_one_non_streaming_request():
    captured = {}

    class _Responses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return {
                "id": "resp_actual",
                "status": "completed",
                "model": "actual-model",
                "output_text": '{"verdict":"done","reason":"verified"}',
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 7,
                    "total_tokens": 17,
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
            }

    client = type("Client", (), {"responses": _Responses()})()
    result = OpenAIResponsesTransport(client).complete_text(
        model="configured-model",
        messages=[{"role": "user", "content": "judge this"}],
        request_context={
            "session_id": "s1",
            "purpose": "goal_judge",
            "server_storage_allowed": False,
        },
        temperature=0,
        max_tokens=128,
    )

    assert captured["stream"] is False
    assert captured["store"] is False
    assert "include" not in captured
    assert "text" not in captured
    assert result["text"] == '{"verdict":"done","reason":"verified"}'
    assert result["model"] == "actual-model"
    assert result["response_id"] == "resp_actual"
    assert result["usage"]["reasoning_tokens"] == 5


def test_compatible_complete_text_uses_one_non_streaming_request():
    captured = {}

    class _Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return {
                "id": "chat_1",
                "model": "deepseek-v4-flash",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"verdict":"continue","reason":"more work"}'
                    },
                }],
                "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
            }

    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _Completions()})()},
    )()
    result = OpenAICompatibleTransport(client).complete_text(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "judge this"}],
        temperature=0,
        max_tokens=128,
    )

    assert captured["stream"] is False
    assert result["text"].startswith('{"verdict":"continue"')
    assert result["response_id"] == "chat_1"


def test_anthropic_complete_text_uses_one_non_streaming_request():
    captured = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "id": "msg_1",
                "model": "claude-test",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            }

    class _HTTPClient:
        @staticmethod
        def post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _Response()

    result = AnthropicMessagesTransport(
        api_key="secret",
        base_url="https://api.anthropic.com",
        http_client=_HTTPClient(),
    ).complete_text(
        model="claude-test",
        messages=[{"role": "user", "content": "judge this"}],
        temperature=0,
        max_tokens=128,
    )

    assert captured["json"]["stream"] is False
    assert result["text"] == "ok"
    assert result["usage"]["total_tokens"] == 6


def test_anthropic_message_conversion_preserves_tool_round_trip():
    system, messages = chat_messages_to_anthropic(
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "weather"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Beijing"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "sunny"},
        ]
    )

    assert system == "Be concise."
    assert messages[1]["content"][-1]["type"] == "tool_use"
    assert messages[1]["content"][-1]["id"] == "toolu_1"
    assert messages[1]["content"][-1]["name"] == "weather"
    assert messages[1]["content"][-1]["input"] == {"city": "Beijing"}
    assert messages[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert messages[1]["content"][-1]["id"] == messages[2]["content"][0]["tool_use_id"]


def test_compatible_stream_usage_preserves_chat_completions_cache_tokens():
    """Regression: transport must not drop Chat Completions cache fields.

    openai-compatible endpoints (deepseek/glm/opencode zen) report cache usage
    as flat prompt_cache_hit_tokens / prompt_cache_miss_tokens or nested
    prompt_tokens_details.cached_tokens.  These were lost after the provider
    transport refactor, so the cache hit rate always showed 0.
    """
    from llm.transport import _usage_dict

    flat = _usage_dict(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "prompt_cache_hit_tokens": 700,
            "prompt_cache_miss_tokens": 300,
        }
    )
    assert flat["prompt_cache_hit_tokens"] == 700
    assert flat["prompt_cache_miss_tokens"] == 300

    nested = _usage_dict(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
    )
    assert nested["prompt_cache_hit_tokens"] == 800

    # Anthropic / Responses formats must keep working.
    anthropic = _usage_dict(
        {
            "input_tokens": 900,
            "output_tokens": 100,
            "cache_read_input_tokens": 600,
            "cache_creation_input_tokens": 300,
        }
    )
    assert anthropic["prompt_cache_hit_tokens"] == 600
    assert anthropic["prompt_cache_miss_tokens"] == 300

    responses = _usage_dict(
        {
            "input_tokens": 900,
            "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 500},
        }
    )
    assert responses["prompt_cache_hit_tokens"] == 500


def test_classify_candidate_failure_buckets():
    from agent_openai import _classify_candidate_failure

    class WireError(Exception):
        def __init__(self, status_code, message):
            super().__init__(message)
            self.status_code = status_code

    # 确定性失败：4xx 参数/鉴权类 → 立即换模型
    assert _classify_candidate_failure(WireError(400, "unknown parameter `thinking`")) == "switch"
    assert _classify_candidate_failure(WireError(401, "invalid api key")) == "switch"
    assert _classify_candidate_failure(WireError(404, "model not found")) == "switch"
    assert _classify_candidate_failure(WireError(422, "invalid request")) == "switch"

    # 瞬时故障：429 / 5xx / 超时 / 连接 → 同模型重试
    assert _classify_candidate_failure(WireError(429, "rate limit exceeded")) == "retry"
    assert _classify_candidate_failure(WireError(500, "Internal server error")) == "retry"
    assert _classify_candidate_failure(WireError(503, "upstream unavailable")) == "retry"
    assert _classify_candidate_failure(WireError(408, "request timeout")) == "retry"
    assert _classify_candidate_failure(TimeoutError("request timed out")) == "retry"
    # 连接抖动：同模型重试吸收（断网由调用方先行检查，不会到达分类器）
    assert _classify_candidate_failure(ConnectionError("connection reset")) == "retry"

    # 断网等本机不可用错误不重试，交给上层暂停回退
    class LocalNetworkUnavailableError(ConnectionError):
        pass

    assert _classify_candidate_failure(LocalNetworkUnavailableError("offline")) == "switch"

    # SDK 连接错误（消息 "Connection error."）必须重试而非直接切换
    try:
        from openai import APIConnectionError
    except ImportError:
        APIConnectionError = None
    if APIConnectionError is not None:
        import httpx as _httpx
        sdk_conn = APIConnectionError(
            message="Connection error.",
            request=_httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
        )
        assert _classify_candidate_failure(sdk_conn) == "retry"
        assert _classify_candidate_failure(Exception("Connection error.")) == "retry"

    # 未知错误保守直接换模型
    assert _classify_candidate_failure(RuntimeError("something weird")) == "fallback"


def test_candidate_retry_policy_env_overrides(monkeypatch):
    from agent_openai import _candidate_retry_policy

    monkeypatch.delenv("LLM_CANDIDATE_RETRY_ATTEMPTS", raising=False)
    monkeypatch.delenv("LLM_CANDIDATE_RETRY_BACKOFF_SEC", raising=False)
    assert _candidate_retry_policy() == (10, 1.0)

    monkeypatch.setenv("LLM_CANDIDATE_RETRY_ATTEMPTS", "0")
    assert _candidate_retry_policy()[0] == 0

    monkeypatch.setenv("LLM_CANDIDATE_RETRY_ATTEMPTS", "5")
    monkeypatch.setenv("LLM_CANDIDATE_RETRY_BACKOFF_SEC", "0.5")
    assert _candidate_retry_policy() == (5, 0.5)


def test_manual_model_switch_resets_run_circuit_and_sticky_candidate():
    import agent_harness

    client = agent_harness.ExecutorLLMClient(
        [
            agent_harness._profile_candidate(
                {
                    "id": "p1",
                    "model": "m1",
                    "llm_type": "openai-compatible",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "k",
                    "context_window": 1000,
                    "max_output_tokens": 100,
                }
            ),
            agent_harness._profile_candidate(
                {
                    "id": "p2",
                    "model": "m2",
                    "llm_type": "openai-responses",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "k",
                    "context_window": 1000,
                    "max_output_tokens": 100,
                }
            ),
        ]
    )

    class _Item:
        session_id = "sess-switch"

        def get(self, key, default=None):
            return {"session_id": self.session_id}.get(key, default)

    client.set_request_scope("run-1")
    client.note_scope_session("sess-switch")

    # run 内 p1 失败 → 熔断记录 + fallback 接管成为“最近成功”。
    with client._failure_lock:
        client._failed_candidates_by_scope.setdefault("run-1", {})[
            client._candidate_circuit_key(0, client.candidates[0])
        ] = ValueError("simulated provider failure")
        client._last_successful_candidate_key = client._candidate_circuit_key(
            1, client.candidates[1]
        )

    # fallback 之后：下一个请求会跳过 p1。
    assert client.next_candidate()["model"] == "m2"
    assert client.current_candidate()["model"] == "m2"

    # 用户手动切回 p1 → 会话重置必须让 p1 立即重新成为第一候选。
    agent_harness.reset_executor_failure_state_for_session("sess-switch")

    assert client.next_candidate()["model"] == "m1"
    assert client.current_candidate()["model"] == "m1"


def test_fallback_takeover_rebinds_session_profile(tmp_path, monkeypatch):
    """fallback 接管成功后会话绑定改为实际服务的 profile(选择器跟随)。"""
    import json as _json

    import agent_harness

    sid = "sess-adopt"
    sessions_dir = tmp_path / "sessions" / sid
    sessions_dir.mkdir(parents=True)
    meta_path = sessions_dir / "metadata.json"
    p1 = {"id": "p1", "model": "m1", "llm_type": "openai-compatible",
          "base_url": "https://api.example.com/v1", "api_key": "k",
          "context_window": 1000, "max_output_tokens": 100}
    p2 = {**p1, "id": "p2", "model": "m2", "llm_type": "openai-responses"}
    meta_path.write_text(_json.dumps({
        "model_profile_id": "p1",
        "profile_by_id": {"p1": p1, "p2": p2},
    }), encoding="utf-8")

    real_load = agent_harness.session_manager._load_metadata
    real_save = agent_harness.session_manager._save_metadata_unlocked

    def fake_load(target):
        if str(target) == sid:
            return _json.loads(meta_path.read_text(encoding="utf-8"))
        return real_load(target)

    saves = []

    def fake_save(target, metadata):
        if str(target) == sid:
            saves.append(dict(metadata))
            meta_path.write_text(_json.dumps(metadata), encoding="utf-8")
            return
        real_save(target, metadata)

    monkeypatch.setattr(agent_harness.session_manager, "_load_metadata", fake_load)
    monkeypatch.setattr(
        agent_harness.session_manager, "_save_metadata_unlocked", fake_save
    )

    client = agent_harness.ExecutorLLMClient(
        [
            agent_harness._profile_candidate(p1),
            agent_harness._profile_candidate(p2),
        ]
    )
    client.set_request_scope("run-adopt")
    client.note_scope_session(sid)

    # p2(fallback)成功服务 → 会话绑定必须改写为 p2。
    client._maybe_adopt_fallback_profile(client.candidates[1])
    assert saves, "adopt must persist the takeover"
    assert saves[-1]["model_profile_id"] == "p2"
    assert saves[-1]["model_switch_history"][-1]["requested_by"] == "fallback"

    # 绑定一致时不再重复写盘。
    saves.clear()
    client._maybe_adopt_fallback_profile(client.candidates[1])
    assert not saves


def _compatible_transport_with_chunks(chunks):
    class _Completions:
        @staticmethod
        def create(**_kwargs):
            return iter(chunks)

    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": _Completions()})()},
    )()
    return OpenAICompatibleTransport(client)


def test_compatible_stream_arguments_survive_single_char_fragments():
    """Regression: gateways that split arguments into 1-4 char fragments used
    to lose every `{"` fragment to the snapshot-prefix heuristic, corrupting
    the JSON and silently degrading ask_user to {} args."""
    payload = (
        '{"questions": [{"header": "代理", "question": "用哪个代理？", '
        '"options": [{"label": "本机", "description": "127.0.0.1"}]}]}'
    )
    # Fragment exactly like the micro-fragmenting gateways: first chunk carries
    # id+name with empty arguments, then 2-char slices.
    chunks = [
        {"model": "m", "choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": "call_00_X",
            "type": "function",
            "function": {"name": "ask_user", "arguments": ""},
        }]}}]},
    ]
    chunks += [
        {"model": "m", "choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": payload[i : i + 2]},
        }]}}]}
        for i in range(0, len(payload), 2)
    ]
    chunks.append(
        {"model": "m", "choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    )

    events = list(_compatible_transport_with_chunks(chunks).stream_completion(model="m", messages=[]))
    merged = "".join(event.arguments_delta for event in events if event.kind == "tool_call_delta")
    assert json.loads(merged) == json.loads(payload)


def test_stream_worker_parsed_args_reject_corrupt_json_instead_of_empty_object():
    """Corrupt streamed arguments must surface as an identifiable transport
    error, not as {} (which reads back to the model as 'you built the
    arguments wrong' and triggers blind retries)."""
    import agent_openai

    acc = {0: {"id": "call_1", "name": "ask_user", "arguments": '{"questions": [label": broken'}}
    parsed = agent_openai._tool_acc_to_parsed_list(acc)
    assert parsed is not None
    call = parsed[0]
    assert call["name"] == "ask_user"
    assert "_stream_corrupted_arguments" in call["args"]

    # Empty-arguments calls (no args streamed at all) still parse to {}.
    empty = agent_openai._tool_acc_to_parsed_list(
        {0: {"id": "call_2", "name": "ask_user", "arguments": ""}}
    )
    assert empty[0]["args"] == {}


def test_session_candidates_only_fallback_to_same_wire_protocol(tmp_path, monkeypatch):
    """fallback 候选链只包含同 llm_type 的模型：responses 不切 chat,反之亦然。"""
    import model_profiles
    import agent_harness

    model_profiles.upsert_profile(tmp_path, {
        "model": "muse-responses", "llm_type": "openai-responses",
        "base_url": "https://opencode.ai/zen/v1", "api_key": "k",
        "context_window": 1000, "max_output_tokens": 100,
    })
    model_profiles.upsert_profile(tmp_path, {
        "model": "deepseek-chat", "llm_type": "openai-compatible",
        "base_url": "https://api.deepseek.com", "api_key": "k2",
        "context_window": 1000, "max_output_tokens": 100,
    })
    model_profiles.upsert_profile(tmp_path, {
        "model": "gpt-responses-2", "llm_type": "openai-responses",
        "base_url": "https://opencode.ai/zen/v1", "api_key": "k",
        "context_window": 1000, "max_output_tokens": 100,
    })

    monkeypatch.setattr(agent_harness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_harness, "_executor_profile_catalog_cache", None)

    profiles, ordered_ids, _top = agent_harness._executor_profile_catalog()
    monkeypatch.setattr(
        agent_harness.session_manager, "_load_metadata", lambda _sid: {
            "model_profile_id": ordered_ids[0]
        }
    )

    candidates = agent_harness.resolve_executor_candidates_for_session("sess-x")
    models = [str(c.get("model") or "") for c in candidates]
    # 绑定 responses → 链里只能有 responses 模型
    assert models == ["muse-responses", "gpt-responses-2"], models

    # 绑定 chat → 链里只能有 chat 模型
    chat_pid = next(
        pid for pid, p in profiles.items() if str(p.get("model")) == "deepseek-chat"
    )
    monkeypatch.setattr(
        agent_harness.session_manager, "_load_metadata", lambda _sid: {
            "model_profile_id": chat_pid
        }
    )
    candidates = agent_harness.resolve_executor_candidates_for_session("sess-y")
    assert [str(c.get("model") or "") for c in candidates] == ["deepseek-chat"]
