from __future__ import annotations

import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class _ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        body=None,
        code=None,
        response=None,
    ):
        super().__init__(message)
        self.message = message
        self.body = body
        self.code = code
        self.response = response


class _Response:
    def __init__(self, body):
        self.text = ""
        self.reason_phrase = "Bad Request"
        self._body = body

    def json(self):
        return self._body


def test_context_limit_error_extracts_reported_window_from_message():
    import agent_loop

    error = RuntimeError(
        "This model's maximum context length is 128,000 tokens. "
        "Your messages resulted in 140000 tokens."
    )

    info = agent_loop._context_limit_error_info(error)
    classified = agent_loop._classify_api_error(error)

    assert info == {"matched": True, "context_window": 128000}
    assert classified["code"] == "CTX"
    assert classified["context_window"] == 128000


def test_context_limit_error_accepts_nested_body_code_without_numeric_limit():
    import agent_loop

    error = _ProviderError(
        "bad request",
        body={
            "error": {
                "code": "context_length_exceeded",
                "message": "The input is too long for this model.",
            }
        },
    )

    assert agent_loop._context_limit_error_info(error) == {
        "matched": True,
        "context_window": 0,
    }
    assert agent_loop._classify_api_error(error)["code"] == "CTX"


def test_context_limit_error_reads_http_response_json():
    import agent_loop

    error = _ProviderError(
        "provider rejected request",
        response=_Response(
            {
                "error": {
                    "code": "context_window_exceeded",
                    "message": "Maximum context window is 64k tokens.",
                }
            }
        ),
    )

    info = agent_loop._context_limit_error_info(error)

    assert info == {"matched": True, "context_window": 64000}


def test_regular_bad_request_is_not_context_limit_error():
    import agent_loop

    error = _ProviderError(
        "invalid request body",
        body={"error": {"code": "invalid_request", "message": "tools must be an array"}},
    )

    assert agent_loop._context_limit_error_info(error) == {
        "matched": False,
        "context_window": 0,
    }
    assert agent_loop._classify_api_error(error)["code"] != "CTX"


def test_context_limit_recovery_uses_smaller_reported_window_without_mutation():
    import agent_loop

    configured = 1_000_000
    reported = 128_000

    assert agent_loop._context_limit_recovery_window(configured, reported) == 128_000
    assert agent_loop._context_limit_recovery_window(configured, 0) == configured
    assert configured == 1_000_000
    assert reported == 128_000


def test_react_context_limit_recovery_contract_is_bounded_and_reuses_compaction():
    import agent_loop

    source = inspect.getsource(agent_loop._react_node_once)

    assert 'stream_error_code in {"NET", "CTX"}' in source
    assert "forced_context_limit_compress or not _skip_compress" in source
    assert "context_window=int(active_context_window)" in source
    assert '"api_context_limit_recovery"' in source
    assert (
        "context_limit_recovery_attempts\n"
        "                            < CONTEXT_EMERGENCY_SHRINK_MAX_RETRIES"
    ) in source
    assert 'reason": "context_window_exceeded"' in source
    assert '"clear_latest_request_scope_failure"' in source
    assert "iter_count = max(0, iter_count - 1)" in source
