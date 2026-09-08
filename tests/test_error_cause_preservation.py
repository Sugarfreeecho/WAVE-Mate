from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _provider_api_error(status_code: int, message: str):
    """Build a real openai.SDK API status error without network access."""
    import openai

    request = httpx.Request("POST", "http://model.invalid/v1/responses")
    response = httpx.Response(status_code, request=request)
    return {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        429: openai.RateLimitError,
    }[status_code](
        message,
        response=response,
        body={"error": {"type": "invalid_request_error", "message": message}},
    )


def _wrapped_as_candidates_unavailable(inner: BaseException) -> RuntimeError:
    """Simulate the harness wrapping a provider failure as 'candidates unavailable'."""
    wrapped = RuntimeError("all model candidates are unavailable for this run")
    wrapped.__cause__ = inner  # mirrors `raise RuntimeError(...) from inner`
    return wrapped


def test_classify_follows_cause_chain_to_permission_denied():
    import agent_loop

    inner = _provider_api_error(403, "The api key is not authorized")
    wrapped = _wrapped_as_candidates_unavailable(inner)

    classified = agent_loop._classify_api_error(wrapped)
    assert classified["code"] == "403"
    assert "访问被拒绝" in classified["title"]


def test_classify_follows_cause_chain_to_bad_request():
    import agent_loop

    inner = _provider_api_error(400, "unknown parameter `reasoning_effort`")
    wrapped = _wrapped_as_candidates_unavailable(inner)

    assert agent_loop._classify_api_error(wrapped)["code"] == "400"


def test_classify_without_cause_keeps_original_behavior():
    import agent_loop

    assert agent_loop._classify_api_error(RuntimeError("all model candidates are unavailable for this run"))["code"] == "OTHER"
    # Direct 403 (no wrapper) must keep classifying as before.
    direct = _provider_api_error(403, "forbidden")
    assert agent_loop._classify_api_error(direct)["code"] == "403"


def test_format_exception_chain_includes_root_cause():
    import agent_loop

    inner = _provider_api_error(403, "The api key is not authorized")
    wrapped = _wrapped_as_candidates_unavailable(inner)

    detail = agent_loop._format_exception_chain(wrapped)
    assert detail.startswith("RuntimeError: ")
    assert "PermissionDeniedError" in detail
    assert "403" in detail or "not authorized" in detail


def test_harness_raises_wrapped_runtime_error_with_root_cause():
    import agent_harness

    inner = _provider_api_error(403, "The api key is not authorized")

    class _FailingTransport:
        def stream_completion(self, **_kwargs):
            raise inner

    client = agent_harness.ExecutorLLMClient(
        candidates=[
            {
                "profile_id": "p1",
                "model": "muse-spark-1.3-contributor-free",
                "provider": "openai-responses",
                "transport": _FailingTransport(),
            }
        ],
    )
    client.set_request_scope("scope-for-test")

    try:
        list(client._stream_completion_iter({"messages": []}))
    except type(inner) as exc:
        assert exc is inner
    else:
        raise AssertionError("expected the initial provider error")

    snapshot = client._failed_candidates_by_scope["scope-for-test"]["p1"]
    assert isinstance(snapshot, agent_harness.CandidateFailureSnapshot)
    assert snapshot is not inner
    assert snapshot.__traceback__ is None

    try:
        list(client._stream_completion_iter({"messages": []}))
    except agent_harness.ModelCandidatesUnavailableError as exc:
        assert "all model candidates are unavailable" in str(exc)
        assert exc.__cause__ is snapshot
        assert agent_harness._exception_http_status(exc.__cause__) == 403
    else:
        raise AssertionError("expected RuntimeError was not raised")


def test_harness_retains_safe_summaries_for_every_failed_candidate():
    import agent_harness
    import agent_loop

    failures = []
    for key, status in (("p1", 403), ("p2", 429)):
        failures.append(
            agent_harness.CandidateFailureSnapshot(
                candidate_key=key,
                model=f"model-{key}",
                provider="test-provider",
                error=_provider_api_error(status, f"failure for {key}"),
            )
        )
    wrapped = agent_harness.ModelCandidatesUnavailableError(failures)
    wrapped.__cause__ = failures[-1]

    detail = agent_loop._format_exception_chain(wrapped)

    assert "model-p1" in detail
    assert "model-p2" in detail
    assert agent_loop._classify_api_error(wrapped)["code"] == "429"


def test_failure_snapshot_redacts_credentials_and_drops_original_traceback():
    import agent_harness
    import agent_loop

    token = "opaque-example-secret-value-1234567890"
    error = RuntimeError(
        f"Authorization: Bearer {token} at https://provider.invalid/v1?token={token}"
    )
    snapshot = agent_harness.CandidateFailureSnapshot(
        candidate_key="p1",
        model="model-p1",
        provider="test-provider",
        error=error,
    )

    assert token not in str(snapshot)
    assert "provider.invalid" not in str(snapshot)
    assert snapshot.__traceback__ is None
    assert token not in agent_loop._format_exception_chain(snapshot)


def test_clear_latest_request_scope_failure_allows_candidate_retry():
    import agent_harness
    from llm import TransportEvent

    calls = []

    class _Transport:
        def stream_completion(self, **_kwargs):
            calls.append("called")
            yield TransportEvent("content_delta", text="ok", model="model-p1")
            yield TransportEvent("finish", finish_reason="stop", model="model-p1")

    client = agent_harness.ExecutorLLMClient(
        candidates=[
            {
                "profile_id": "p1",
                "model": "model-p1",
                "provider": "test-provider",
                "transport": _Transport(),
            }
        ]
    )
    client.set_request_scope("scope-for-test")
    client._failed_candidates_by_scope["scope-for-test"] = {
        "older-candidate": agent_harness.CandidateFailureSnapshot(
            candidate_key="older-candidate",
            model="older-model",
            provider="test-provider",
            error=RuntimeError("older failure"),
        ),
        "p1": agent_harness.CandidateFailureSnapshot(
            candidate_key="p1",
            model="model-p1",
            provider="test-provider",
            error=RuntimeError("maximum context length exceeded"),
        )
    }

    client.clear_latest_request_scope_failure()
    assert list(client._failed_candidates_by_scope["scope-for-test"]) == [
        "older-candidate"
    ]
    events = list(client._stream_completion_iter({"messages": []}))

    assert calls == ["called"]
    assert any(event.text == "ok" for event in events)


def test_exception_chain_honors_suppressed_context():
    import agent_loop

    hidden = _provider_api_error(403, "hidden provider failure")
    outer = RuntimeError("safe wrapper")
    outer.__context__ = hidden
    outer.__suppress_context__ = True

    assert agent_loop._classify_api_error(outer)["code"] == "OTHER"
    assert "hidden provider failure" not in agent_loop._format_exception_chain(outer)
