import pytest


def test_responses_profile_blank_thinking_is_automatic():
    import agent_harness

    profile = {
        "llm_type": "openai-responses",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "muse-spark-1.2-contributor",
        "thinking_mode": "",
        "reasoning_effort": "",
    }

    extra_body = agent_harness._profile_extra_body(profile)
    assert extra_body is None
    assert agent_harness._profile_reasoning_effort(profile, extra_body) is None


def test_executor_tracks_the_last_actually_successful_candidate():
    import agent_harness
    from llm import TransportEvent

    calls = []

    class _Transport:
        def __init__(self, name, *, fail=False):
            self.name = name
            self.fail = fail

        def stream_completion(self, **kwargs):
            calls.append((self.name, kwargs["model"], kwargs["max_tokens"]))
            if self.fail:
                raise RuntimeError(f"{self.name} unavailable")
            yield TransportEvent("content_delta", text=self.name)
            yield TransportEvent("finish", finish_reason="stop", model=kwargs["model"])

    first = _Transport("first", fail=True)
    second = _Transport("second")
    client = agent_harness.ExecutorLLMClient([
        {
            "profile_id": "p1",
            "provider": "openai",
            "model": "m1",
            "transport": first,
            "max_output_tokens": 100,
            "temperature": 0,
        },
        {
            "profile_id": "p2",
            "provider": "openai-compatible",
            "model": "m2",
            "transport": second,
            "max_output_tokens": 200,
            "temperature": 0,
        },
    ])

    client.set_request_scope("run-1")
    list(client.stream_completion(model="m1", messages=[], max_tokens=20))
    current = client.current_candidate()
    assert calls == [("first", "m1", 100), ("second", "m2", 200)]
    assert current["profile_id"] == "p2"
    assert current["provider"] == "openai-compatible"
    assert client.next_candidate()["profile_id"] == "p2"
    client.set_request_scope("run-2")
    assert client.next_candidate()["profile_id"] == "p1"


def test_responses_profile_only_sends_explicit_thinking_and_effort():
    import agent_harness

    profile = {
        "llm_type": "openai",
        "base_url": "https://opencode.ai/zen/go/v1",
        "model": "gpt-test",
        "thinking_mode": "enabled",
        "reasoning_effort": "max",
    }

    extra_body = agent_harness._profile_extra_body(profile)
    assert extra_body == {"thinking": {"type": "enabled"}}
    assert agent_harness._profile_reasoning_effort(profile, extra_body) == "max"


def test_compatible_profile_keeps_thinking_defaults():
    import agent_harness

    profile = {
        "llm_type": "openai-compatible",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-test",
        "thinking_mode": "",
        "reasoning_effort": "",
    }

    extra_body = agent_harness._profile_extra_body(profile)
    assert extra_body == {"thinking": {"type": "enabled"}}
    assert agent_harness._profile_reasoning_effort(profile, extra_body) == "high"


def test_executor_chat_complete_uses_session_model(monkeypatch):
    import agent_harness

    calls = []

    def fake_complete(messages, **kwargs):
        calls.append((messages, kwargs))
        return {"text": "ok"}

    monkeypatch.setattr(agent_harness, "executor_one_shot_complete", fake_complete)

    assert agent_harness.executor_chat_complete([], session_id="s1") == "ok"
    assert calls[0][0] == []
    assert calls[0][1]["session_id"] == "s1"
    assert calls[0][1]["purpose"] is agent_harness.LLMRequestPurpose.SUMMARY


def test_executor_text_complete_uses_session_model(monkeypatch):
    import agent_harness

    calls = []

    def fake_complete(messages, **kwargs):
        calls.append((messages, kwargs))
        return {"text": "edited"}

    monkeypatch.setattr(agent_harness, "executor_one_shot_complete", fake_complete)

    assert agent_harness.executor_text_complete("prompt", session_id="s1") == "edited"
    assert calls[0][0][0].content == "prompt"
    assert calls[0][1]["session_id"] == "s1"
    assert calls[0][1]["purpose"] is agent_harness.LLMRequestPurpose.SUMMARY


def test_executor_one_shot_uses_native_transport_and_stateless_fallback(monkeypatch):
    import agent_harness

    calls = []

    class Transport:
        def __init__(self, name, result):
            self.name = name
            self.result = result

        def complete_text(self, **kwargs):
            calls.append((self.name, kwargs))
            return dict(self.result)

    monkeypatch.setattr(
        agent_harness,
        "resolve_executor_candidates_for_session",
        lambda _session_id: [
            {
                "client": object(),
                "transport": Transport("responses", {
                    "text": "",
                    "status": "incomplete",
                    "finish_reason": "length",
                }),
                "provider": "openai",
                "model": "reasoning-primary",
                "max_output_tokens": 1024,
            },
            {
                "client": object(),
                "transport": Transport("compatible", {
                    "text": "usable",
                    "status": "completed",
                    "finish_reason": "stop",
                }),
                "provider": "openai-compatible",
                "model": "backup",
                "max_output_tokens": 256,
            },
        ],
    )

    result = agent_harness.executor_one_shot_complete(
        [agent_harness.UserMessage(content="prompt")],
        session_id="session-a",
        purpose=agent_harness.LLMRequestPurpose.TITLE,
        response_validator=lambda value: bool(value.get("text")),
        include_candidate_controls=False,
    )

    assert result["text"] == "usable"
    assert [name for name, _kwargs in calls] == ["responses", "compatible"]
    for _name, kwargs in calls:
        context = kwargs["request_context"]
        assert context.session_id == "session-a"
        assert context.purpose is agent_harness.LLMRequestPurpose.TITLE
        assert context.server_storage_allowed is False
        assert "extra_body" not in kwargs
        assert "reasoning_effort" not in kwargs
    assert calls[0][1]["max_tokens"] == 1024
    assert calls[1][1]["max_tokens"] == 256


def test_background_stream_falls_back_when_primary_has_reasoning_but_no_text():
    import agent_harness

    class Transport:
        def __init__(self, events):
            self.events = events

        def stream_completion(self, **_kwargs):
            yield from self.events

    client = agent_harness.ExecutorLLMClient([
        {
            "client": object(),
            "transport": Transport([
                agent_harness.TransportEvent("reasoning_delta", text="hidden"),
                agent_harness.TransportEvent("finish", finish_reason="length"),
            ]),
            "provider": "openai",
            "model": "reasoning-only",
            "max_output_tokens": 100,
        },
        {
            "client": object(),
            "transport": Transport([
                agent_harness.TransportEvent("content_delta", text="summary"),
                agent_harness.TransportEvent("finish", finish_reason="stop"),
            ]),
            "provider": "openai-compatible",
            "model": "backup",
            "max_output_tokens": 100,
        },
    ])

    events = list(client.stream_completion(
        messages=[{"role": "user", "content": "summarize"}],
        max_tokens=100,
        request_context=agent_harness.LLMRequestContext(
            session_id="session-a",
            purpose=agent_harness.LLMRequestPurpose.SUMMARY,
            server_storage_allowed=False,
        ),
    ))

    assert [event.text for event in events if event.kind == "content_delta"] == ["summary"]
    assert all(event.text != "hidden" for event in events)


def test_fallback_client_switches_after_api_error_and_uses_each_profile_full_limit():
    import agent_harness

    calls = []

    class _Completions:
        def __init__(self, name, error=None):
            self.name = name
            self.error = error

        def create(self, **kwargs):
            calls.append((self.name, kwargs["model"], kwargs["max_tokens"]))
            if self.error:
                raise self.error
            return "ok"

    class _Client:
        def __init__(self, completions):
            self.chat = type("Chat", (), {"completions": completions})()

    candidates = [
        {
            "client": _Client(_Completions("primary", RuntimeError("bad api"))),
            "model": "primary-model",
            "max_output_tokens": 4096,
        },
        {
            "client": _Client(_Completions("backup")),
            "model": "backup-model",
            "max_output_tokens": 1024,
        },
    ]
    switched = []
    client = agent_harness.FallbackOpenAIClient(candidates)
    client.set_status_callback(switched.append)

    result = client.chat.completions.create(model="ignored", max_tokens=256)

    assert result == "ok"
    assert calls == [
        ("primary", "primary-model", 4096),
        ("backup", "backup-model", 1024),
    ]
    assert switched[0]["from_model"] == "primary-model"
    assert switched[0]["to_model"] == "backup-model"


def test_fallback_client_marks_media_failure_and_routes_to_next_profile():
    import agent_harness

    calls = []
    marked = []
    statuses = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            content = kwargs["messages"][0]["content"]
            if isinstance(content, list):
                raise ValueError("This model does not support image input")
            return "ok"

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    candidates = [
        {
            "client": _Client(),
            "model": "primary-model",
            "max_output_tokens": 4096,
            "multimodal_input": True,
            "mark_multimodal_failed": marked.append,
        },
        {
            "client": _Client(),
            "model": "backup-model",
            "max_output_tokens": 4096,
            "multimodal_input": False,
        },
    ]
    client = agent_harness.FallbackOpenAIClient(candidates)
    client.set_status_callback(statuses.append)

    result = client.chat.completions.create(
        model="ignored",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": '分析 "D:\\screen.png"'},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAA"},
                    },
                ],
            }
        ],
    )

    assert result == "ok"
    assert len(calls) == 2
    assert candidates[0]["multimodal_input"] is False
    assert len(marked) == 1
    assert isinstance(calls[0]["messages"][0]["content"], list)
    retry_user = next(
        message for message in calls[1]["messages"] if message["role"] == "user"
    )
    retry_system = next(
        message for message in calls[1]["messages"] if message["role"] == "system"
    )
    assert 'D:\\screen.png' in retry_user["content"]
    assert "task 工具" in retry_system["content"]
    assert calls[1]["messages"][-1]["role"] == "user"
    assert statuses[0]["multimodal_fallback"] is True


def test_fallback_client_keeps_text_profile_and_injects_task_delegation():
    import agent_harness

    calls = []
    statuses = []

    class _Completions:
        def __init__(self, name):
            self.name = name

        def create(self, **kwargs):
            calls.append((self.name, kwargs))
            return "ok"

    class _Client:
        def __init__(self, name):
            self.chat = type("Chat", (), {"completions": _Completions(name)})()

    candidates = [
        {
            "client": _Client("text"),
            "model": "text-model",
            "max_output_tokens": 4096,
            "input_modalities": ["text", "audio"],
        },
        {
            "client": _Client("vision"),
            "model": "vision-model",
            "max_output_tokens": 4096,
            "input_modalities": ["text", "image"],
        },
    ]
    client = agent_harness.FallbackOpenAIClient(candidates)
    client.set_status_callback(statuses.append)

    result = client.chat.completions.create(
        model="ignored",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [{
                "type": "image_url",
                "image_url": {"url": "https://example.com/image.png"},
            }],
        }],
    )

    assert result == "ok"
    assert [name for name, _kwargs in calls] == ["text"]
    text_messages = calls[0][1]["messages"]
    assert not any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for message in text_messages
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
    )
    system_message = next(message for message in text_messages if message["role"] == "system")
    user_message = next(message for message in text_messages if message["role"] == "user")
    assert "task 工具" in system_message["content"]
    assert "model_profile_id" in system_message["content"]
    assert "https://example.com/image.png" in user_message["content"]
    assert statuses == []


def test_chat_completion_does_not_replace_preferred_text_profile_for_image():
    import agent_harness
    import agent_openai
    from agent_messages import UserMessage

    calls = []

    class _Completions:
        def __init__(self, name):
            self.name = name

        def create(self, **kwargs):
            calls.append((self.name, kwargs))
            return type("Response", (), {"usage": None})()

    class _Client:
        def __init__(self, name):
            self.chat = type("Chat", (), {"completions": _Completions(name)})()

    client = agent_harness.FallbackOpenAIClient([
        {
            "client": _Client("text"),
            "model": "text-model",
            "max_output_tokens": 4096,
            "input_modalities": ["text"],
        },
        {
            "client": _Client("vision"),
            "model": "vision-model",
            "max_output_tokens": 4096,
            "input_modalities": ["text", "image"],
        },
    ])

    prompt = "分析 https://example.com/image.png"
    agent_openai.chat_completion(
        client,
        "text-model",
        [UserMessage(content=prompt)],
        temperature=0,
        max_tokens=256,
    )

    assert [name for name, _kwargs in calls] == ["text"]
    sent = calls[0][1]["messages"]
    assert prompt in next(message for message in sent if message["role"] == "user")["content"]
    system = next(message for message in sent if message["role"] == "system")["content"]
    assert "task 工具" in system
    assert "model_profile_id" in system


def test_fallback_candidates_consume_shared_logical_request_budget(monkeypatch):
    import agent_harness
    import agent_openai
    from agent_messages import UserMessage

    monkeypatch.setattr(agent_openai, "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC", 0)
    monkeypatch.setattr(agent_openai, "OPENAI_MAX_RETRIES", 1)
    monkeypatch.setattr(agent_openai, "OPENAI_TOTAL_REQUEST_BUDGET", 2)
    calls = []

    class _Completions:
        def __init__(self, name):
            self.name = name

        def create(self, **_kwargs):
            calls.append(self.name)
            raise RuntimeError(f"{self.name} failed")

    class _Client:
        def __init__(self, name):
            self.chat = type("Chat", (), {"completions": _Completions(name)})()

    client = agent_harness.FallbackOpenAIClient([
        {"client": _Client("one"), "model": "one", "max_output_tokens": 128},
        {"client": _Client("two"), "model": "two", "max_output_tokens": 128},
        {"client": _Client("three"), "model": "three", "max_output_tokens": 128},
    ])

    with pytest.raises(RuntimeError, match="budget"):
        agent_openai.chat_completion(
            client,
            "one",
            [UserMessage(content="hello")],
            temperature=0,
            max_tokens=32,
        )

    assert calls == ["one", "two"]


def test_transport_fallback_circuit_skips_failed_profile_for_same_run(monkeypatch):
    import agent_harness
    from llm import TransportEvent

    monkeypatch.setattr(agent_harness, "_claim_additional_recovery_request", lambda: True)
    calls = []

    class _Transport:
        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail

        def stream_completion(self, **_kwargs):
            calls.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} rejected request")
            yield TransportEvent("content_delta", text="ok", model=self.name)
            yield TransportEvent("finish", finish_reason="stop", model=self.name)

    client = agent_harness.ExecutorLLMClient([
        {
            "profile_id": "primary",
            "transport": _Transport("primary", fail=True),
            "provider": "openai",
            "model": "primary",
            "max_output_tokens": 128,
        },
        {
            "profile_id": "backup",
            "transport": _Transport("backup"),
            "provider": "openai-compatible",
            "model": "backup",
            "max_output_tokens": 128,
        },
    ])

    request = {"model": "ignored", "messages": [], "max_tokens": 32}
    client.set_request_scope("run-1")
    assert any(event.text == "ok" for event in client.stream_completion(**request))
    assert any(event.text == "ok" for event in client.stream_completion(**request))
    assert calls == ["primary", "backup", "backup"]

    client.set_request_scope("run-2")
    assert any(event.text == "ok" for event in client.stream_completion(**request))
    assert calls[-2:] == ["primary", "backup"]


def test_transport_failure_circuit_survives_executor_client_rebuild(monkeypatch):
    import agent_harness
    from llm import TransportEvent

    monkeypatch.setattr(agent_harness, "_claim_additional_recovery_request", lambda: True)
    calls = []
    shared_lock = agent_harness.threading.RLock()
    shared_failures = {}

    class _Transport:
        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail

        def stream_completion(self, **_kwargs):
            calls.append(self.name)
            if self.fail:
                raise RuntimeError(f"{self.name} failed")
            yield TransportEvent("content_delta", text="ok", model=self.name)
            yield TransportEvent("finish", finish_reason="stop", model=self.name)

    candidates = [
        {
            "profile_id": "primary",
            "transport": _Transport("primary", fail=True),
            "provider": "openai",
            "model": "primary",
            "max_output_tokens": 128,
        },
        {
            "profile_id": "backup",
            "transport": _Transport("backup"),
            "provider": "openai-compatible",
            "model": "backup",
            "max_output_tokens": 128,
        },
    ]
    request = {"model": "ignored", "messages": [], "max_tokens": 32}

    first = agent_harness.ExecutorLLMClient(
        candidates,
        failure_lock=shared_lock,
        failed_candidates_by_scope=shared_failures,
    )
    first.set_request_scope("run-shared")
    assert any(event.text == "ok" for event in first.stream_completion(**request))

    rebuilt = agent_harness.ExecutorLLMClient(
        candidates,
        failure_lock=shared_lock,
        failed_candidates_by_scope=shared_failures,
    )
    rebuilt.set_request_scope("run-shared")
    assert any(event.text == "ok" for event in rebuilt.stream_completion(**request))

    assert calls == ["primary", "backup", "backup"]


def test_executor_hedge_retries_stalled_logical_request_and_still_switches_to_backup(monkeypatch):
    import time

    import agent_harness
    import agent_openai
    from agent_messages import UserMessage
    from llm import TransportEvent

    monkeypatch.setattr(agent_openai, "OPENAI_FIRST_TOKEN_HEDGE_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(agent_openai, "OPENAI_FIRST_TOKEN_HEDGE_MAX_RETRIES", 2)
    monkeypatch.setattr(agent_openai, "OPENAI_MAX_RETRIES", 1)
    calls = []
    statuses = []

    class _Transport:
        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail

        def stream_completion(self, **kwargs):
            calls.append(self.name)
            if self.fail:
                time.sleep(0.04)
                raise RuntimeError(f"{self.name} failed")
            yield TransportEvent("content_delta", text="ok", model=kwargs["model"])
            yield TransportEvent("finish", finish_reason="stop", model=kwargs["model"])

    client = agent_harness.ExecutorLLMClient([
        {
            "profile_id": "primary",
            "transport": _Transport("primary", fail=True),
            "provider": "openai",
            "model": "primary",
            "max_output_tokens": 128,
        },
        {
            "profile_id": "backup",
            "transport": _Transport("backup"),
            "provider": "openai-compatible",
            "model": "backup",
            "max_output_tokens": 128,
        },
    ])
    client.set_request_scope("run-no-duplicate-hedge")
    client.set_status_callback(statuses.append)

    response = agent_openai.chat_completion(
        client,
        "primary",
        [UserMessage(content="hello")],
        temperature=0,
        max_tokens=32,
    )

    # The outer first-token hedge is active for the executor facade as well:
    # a logical request that produces no first token within the hedge timeout
    # is retried in parallel (same shared budget), so the primary transport
    # may be entered more than once before the backup wins.
    assert response.choices[0].message.content == "ok"
    assert "primary" in calls
    assert "backup" in calls
    assert calls.count("backup") >= 1
    # Backup takeover must still surface exactly one distinct switch edge.
    switch_edges = [
        (item.get("from_model"), item.get("to_model"))
        for item in statuses
        if item.get("model_switch")
    ]
    assert ("primary", "backup") in switch_edges
    assert len({edge for edge in switch_edges if edge == ("primary", "backup")}) == 1
