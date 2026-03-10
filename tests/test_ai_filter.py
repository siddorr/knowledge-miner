import json

from knowledge_miner.ai_filter import AIRelevanceFilter, describe_ai_filter_runtime


def test_ai_filter_parse_result_valid():
    f = AIRelevanceFilter(enabled=True, api_key="x", model="m", base_url="http://example.com", timeout_seconds=1)
    out = f._parse_result(json.dumps({"decision": "needs_review", "confidence": 0.77, "reason": "acronym ambiguous"}))  # noqa: SLF001
    assert out.decision == "needs_review"
    assert out.confidence == 0.77


def test_ai_filter_disabled_returns_none():
    f = AIRelevanceFilter(enabled=False)
    out = f.evaluate(title="UPW paper", abstract="...", base_score=5.0, base_decision="auto_accept")
    assert out is None


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
