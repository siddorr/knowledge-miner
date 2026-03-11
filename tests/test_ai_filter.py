import json

from knowledge_miner.ai_filter import AIAuthError, AIRelevanceFilter, describe_ai_filter_runtime


def test_ai_filter_parse_result_valid():
    f = AIRelevanceFilter(enabled=True, api_key="x", model="m", base_url="http://example.com", timeout_seconds=1)
    out = f._parse_result(json.dumps({"decision": "needs_review", "confidence": 0.77, "reason": "acronym ambiguous"}))  # noqa: SLF001
    assert out.decision == "needs_review"
    assert out.confidence == 0.77


def test_ai_filter_disabled_returns_none():
    f = AIRelevanceFilter(enabled=False)
    out = f.evaluate(title="UPW paper", abstract="...", base_score=5.0, base_decision="auto_accept")
    assert out is None


def test_ai_filter_valid_token_success(monkeypatch):
    f = AIRelevanceFilter(enabled=True, api_key="ok", model="m", base_url="http://example.com", timeout_seconds=1)
    monkeypatch.setattr(
        f,
        "_chat",
        lambda payload: json.dumps({"decision": "auto_accept", "confidence": 0.91, "reason": "strong match"}),  # noqa: ARG005
    )
    out = f.evaluate(title="UPW paper", abstract="...", base_score=5.0, base_decision="needs_review")
    assert out is not None
    assert out.decision == "auto_accept"
    assert out.confidence == 0.91


def test_describe_ai_filter_runtime_states():
    active, warning = describe_ai_filter_runtime(use_ai_filter=False, api_key=None)
    assert active is False
    assert warning is not None

    active, warning = describe_ai_filter_runtime(use_ai_filter=True, api_key=None)
    assert active is False
    assert warning is not None

    active, warning = describe_ai_filter_runtime(use_ai_filter=True, api_key="token")
    assert active is True
    assert warning is None


def test_ai_filter_auth_error_disables_for_rest_of_run(monkeypatch):
    f = AIRelevanceFilter(enabled=True, api_key="bad", model="m", base_url="http://example.com", timeout_seconds=1)
    calls = {"n": 0}

    def fail_auth(payload):  # noqa: ANN001
        calls["n"] += 1
        raise AIAuthError("ai_filter_http_401")

    monkeypatch.setattr(f, "_chat", fail_auth)
    out1 = f.evaluate(title="UPW 1", abstract="a", base_score=1.0, base_decision="needs_review")
    assert f.pop_last_error_category() == "auth_error"
    out2 = f.evaluate(title="UPW 2", abstract="a", base_score=1.0, base_decision="needs_review")
    assert out1 is None
    assert out2 is None
    assert calls["n"] == 1
