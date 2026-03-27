import knowledge_miner.connectors as connectors
from knowledge_miner.domain_allowlist import is_allowed_url


def _reset_semantic_scholar_state() -> None:
    connectors._SEMANTIC_SCHOLAR_NEXT_REQUEST_AT = 0.0  # noqa: SLF001
    connectors._SEMANTIC_SCHOLAR_COOLDOWN_UNTIL = 0.0  # noqa: SLF001
    connectors._SEMANTIC_SCHOLAR_CACHE.clear()  # noqa: SLF001


def test_openalex_abstract_reconstruction():
    idx = {"ultrapure": [0], "water": [1], "semiconductor": [2]}
    text = connectors._openalex_abstract(idx)  # noqa: SLF001
    assert text == "ultrapure water semiconductor"


def test_extract_year_from_age_text():
    assert connectors._extract_year("Published 2023-10-10") == 2023  # noqa: SLF001
    assert connectors._extract_year("recent") is None  # noqa: SLF001


def test_build_real_connectors_types():
    items = connectors.build_real_connectors()
    names = [c.name for c in items]
    assert names == ["openalex", "semantic_scholar", "brave"]


def test_semantic_scholar_search_uses_bulk_endpoint():
    connector = connectors.SemanticScholarConnector(search_limit=10)
    requested: list[tuple[str, dict | None, dict | None]] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del method, headers
        requested.append((url, params, json_body))
        assert retry_multiplier == 1.0
        return {"data": [{"paperId": "S2_1", "title": "Result", "year": 2024, "url": "https://example.org"}]}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        rows = connector.search("semiconductor wastewater", run_id="run1", iteration=1)
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert len(rows) == 1
    assert requested[0][0].endswith("/paper/search/bulk")
    assert requested[0][1]["query"] == "semiconductor wastewater"
    assert requested[0][1]["limit"] == 10
    assert requested[0][2] is None


def test_build_connectors_mock_toggle():
    original = connectors.settings.use_mock_connectors
    try:
        object.__setattr__(connectors.settings, "use_mock_connectors", True)
        items = connectors.build_connectors()
        assert len(items) == 3
        assert all(isinstance(c, connectors.MockConnector) for c in items)
    finally:
        object.__setattr__(connectors.settings, "use_mock_connectors", original)


def test_mock_connector_citation_expansion():
    from types import SimpleNamespace

    connector = connectors.MockConnector("openalex", "academic")
    source = SimpleNamespace(id="src1", title="UPW in semiconductor fabs", year=2020, type="academic")
    backward, forward = connector.expand_citations(source, per_direction_limit=5, iteration=1)
    assert len(backward) == 3
    assert len(forward) == 3
    assert all(item["discovery_method"] == "backward_citation" for item in backward)
    assert all(item["discovery_method"] == "forward_citation" for item in forward)


def test_semantic_scholar_citation_expansion_mapping():
    from types import SimpleNamespace

    connector = connectors.SemanticScholarConnector()
    source = SimpleNamespace(id="src_s2", source_native_id="S2_PARENT", doi=None)

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        assert method == "GET"
        assert "/paper/S2_PARENT" in url
        assert "fields" in (params or {})
        assert json_body is None
        assert retry_multiplier == 1.0
        return {
            "references": [
                {
                    "paperId": "S2_REF_1",
                    "title": "Reference paper",
                    "year": 2018,
                    "url": "https://example.org/ref1",
                    "abstract": "Reference abstract",
                    "externalIds": {"DOI": "10.1000/ref1"},
                }
            ],
            "citations": [
                {
                    "paperId": "S2_CIT_1",
                    "title": "Citing paper",
                    "year": 2021,
                    "url": "https://example.org/cit1",
                    "abstract": "Citing abstract",
                    "externalIds": {"DOI": "10.1000/cit1"},
                }
            ],
        }

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        backward, forward = connector.expand_citations(source, per_direction_limit=10, iteration=1)
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert len(backward) == 1
    assert len(forward) == 1
    assert backward[0]["discovery_method"] == "backward_citation"
    assert backward[0]["semantic_scholar_id"] == "S2_REF_1"
    assert forward[0]["discovery_method"] == "forward_citation"
    assert forward[0]["semantic_scholar_id"] == "S2_CIT_1"


def test_semantic_scholar_citation_expansion_unwraps_nested_reference_rows():
    from types import SimpleNamespace

    connector = connectors.SemanticScholarConnector()
    source = SimpleNamespace(id="src_s2", source_native_id="S2_PARENT", doi=None)

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        assert method == "GET"
        assert "/paper/S2_PARENT" in url
        assert "fields" in (params or {})
        assert json_body is None
        assert retry_multiplier == 1.0
        return {
            "references": [
                {
                    "paperId": "S2_REF_WRAPPER",
                    "citedPaper": {
                        "paperId": "S2_REF_2",
                        "title": "Nested reference paper",
                        "year": 2017,
                        "url": "https://example.org/ref2",
                        "abstract": "Nested reference abstract",
                        "externalIds": {"DOI": "10.1000/ref2"},
                    },
                }
            ],
            "citations": [],
        }

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        backward, forward = connector.expand_citations(source, per_direction_limit=10, iteration=1)
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert len(backward) == 1
    assert len(forward) == 0
    assert backward[0]["discovery_method"] == "backward_citation"
    assert backward[0]["semantic_scholar_id"] == "S2_REF_2"
    assert backward[0]["doi"] == "10.1000/ref2"


def test_openalex_citation_expansion_builds_forward_fallback_when_cited_by_api_url_missing():
    from types import SimpleNamespace

    connector = connectors.OpenAlexConnector()
    source = SimpleNamespace(
        id="src_oa",
        source_native_id="https://openalex.org/W123",
        doi="10.1000/oa-parent",
    )
    requested: list[tuple[str, str, dict | None]] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None):  # noqa: ANN001
        del headers, json_body
        requested.append((method, url, params))
        if url.endswith("/works/https://openalex.org/W123"):
            return {
                "id": "https://openalex.org/W123",
                "title": "Parent",
                "referenced_works": [],
            }
        if "filter=cites:W123" in url:
            return {
                "results": [
                    {
                        "id": "https://openalex.org/W456",
                        "title": "Forward citing paper",
                        "publication_year": 2024,
                        "doi": "https://doi.org/10.1000/forward",
                        "primary_location": {"landing_page_url": "https://example.org/forward"},
                    }
                ],
                "meta": {"next_cursor": None},
            }
        raise AssertionError(url)

    original = connectors._request_json  # noqa: SLF001
    try:
        connectors._request_json = fake_request_json  # noqa: SLF001
        backward, forward = connector.expand_citations(source, per_direction_limit=0, iteration=1)
    finally:
        connectors._request_json = original  # noqa: SLF001

    assert backward == []
    assert len(forward) == 1
    assert forward[0]["discovery_method"] == "forward_citation"
    assert forward[0]["openalex_id"] == "https://openalex.org/W456"
    assert any("filter=cites:W123" in url for _method, url, _params in requested)


def test_semantic_scholar_fetch_paper_prefers_doi_before_openalex_id():
    from types import SimpleNamespace

    source = SimpleNamespace(
        doi="10.1000/preferred-doi",
        semantic_scholar_id=None,
        source="openalex",
        source_native_id="https://openalex.org/W123",
        openalex_id="https://openalex.org/W123",
        url="https://example.org/article",
    )
    requested: list[str] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del method, params, headers
        assert json_body is None
        assert retry_multiplier == 1.0
        requested.append(url)
        if url.endswith("/paper/DOI:10.1000/preferred-doi"):
            return {"paperId": "S2_DOI_OK"}
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        paper = connectors._semantic_scholar_fetch_paper(source)  # noqa: SLF001
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert paper == {"paperId": "S2_DOI_OK"}
    assert requested[0].endswith("/paper/DOI:10.1000/preferred-doi")


def test_semantic_scholar_fetch_paper_falls_back_when_doi_misses():
    from types import SimpleNamespace

    source = SimpleNamespace(
        doi="10.1000/missing-doi",
        semantic_scholar_id=None,
        source="openalex",
        source_native_id="https://openalex.org/W123",
        openalex_id="https://openalex.org/W123",
        url="https://example.org/article",
    )
    requested: list[str] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del method, params, headers
        assert json_body is None
        assert retry_multiplier == 1.0
        requested.append(url)
        if url.endswith("/paper/https://openalex.org/W123"):
            return {"paperId": "S2_OPENALEX_OK"}
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        paper = connectors._semantic_scholar_fetch_paper(source)  # noqa: SLF001
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert paper == {"paperId": "S2_OPENALEX_OK"}
    assert requested[0].endswith("/paper/DOI:10.1000/missing-doi")
    assert any(url.endswith("/paper/https://openalex.org/W123") for url in requested)


def test_semantic_scholar_fetch_paper_retries_with_uppercase_doi():
    from types import SimpleNamespace

    source = SimpleNamespace(
        doi="10.1109/tsm.2002.804903",
        semantic_scholar_id=None,
        source="openalex",
        source_native_id="https://openalex.org/W2111221414",
        openalex_id="https://openalex.org/W2111221414",
        url="https://doi.org/10.1109/tsm.2002.804903",
    )
    requested: list[str] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del method, params, headers
        assert json_body is None
        assert retry_multiplier == 1.0
        requested.append(url)
        if url.endswith("/paper/DOI:10.1109/TSM.2002.804903"):
            return {"paperId": "S2_UPPERCASE_DOI_OK"}
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        paper = connectors._semantic_scholar_fetch_paper(source)  # noqa: SLF001
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert paper == {"paperId": "S2_UPPERCASE_DOI_OK"}
    assert requested[0].endswith("/paper/DOI:10.1109/tsm.2002.804903")
    assert requested[1].endswith("/paper/DOI:10.1109/TSM.2002.804903")


def test_semantic_scholar_fetch_paper_uses_minimal_citation_field_set():
    from types import SimpleNamespace

    source = SimpleNamespace(
        doi="10.1109/TSM.2002.804903",
        semantic_scholar_id=None,
        source="openalex",
        source_native_id="https://openalex.org/W2111221414",
        openalex_id="https://openalex.org/W2111221414",
        url="https://doi.org/10.1109/TSM.2002.804903",
    )
    captured_fields: list[str] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del method, url, headers
        assert json_body is None
        assert retry_multiplier == 1.0
        captured_fields.append((params or {}).get("fields", ""))
        return {"paperId": "S2_OK"}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        paper = connectors._semantic_scholar_fetch_paper(source)  # noqa: SLF001
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert paper == {"paperId": "S2_OK"}
    assert captured_fields == [connectors._semantic_scholar_citation_fields()]  # noqa: SLF001
    assert "references.abstract" not in captured_fields[0]
    assert "citations.abstract" not in captured_fields[0]
    assert "authors.name" not in captured_fields[0]


def test_semantic_scholar_fetch_paper_falls_back_to_title_search_after_identifier_misses():
    from types import SimpleNamespace

    source = SimpleNamespace(
        title="CMP wastewater management using the concepts of design for environment",
        doi="10.1000/missing-doi",
        semantic_scholar_id=None,
        source="openalex",
        source_native_id="https://openalex.org/W123",
        openalex_id="https://openalex.org/W123",
        url="https://example.org/article",
    )
    requests: list[tuple[str, dict | None]] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del method, headers
        assert json_body is None
        assert retry_multiplier == 1.0
        requests.append((url, params))
        if url.endswith("/paper/search"):
            return {"data": [{"paperId": "S2_TITLE_OK", "title": source.title}]}
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        paper = connectors._semantic_scholar_fetch_paper(source)  # noqa: SLF001
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert paper == {"paperId": "S2_TITLE_OK", "title": source.title}
    assert requests[0][0].endswith("/paper/DOI:10.1000/missing-doi")
    assert requests[-1][0].endswith("/paper/search")
    assert requests[-1][1]["query"] == source.title


def test_semantic_scholar_request_retries_429_then_succeeds(monkeypatch):
    _reset_semantic_scholar_state()
    original_retries = connectors.settings.semantic_scholar_max_retries
    original_interval = connectors.settings.semantic_scholar_min_interval_seconds
    original_cooldown = connectors.settings.semantic_scholar_cooldown_seconds
    original_timeout = connectors.settings.semantic_scholar_timeout_seconds
    sleeps: list[float] = []
    calls = {"count": 0}

    class DummyResponse:
        def __init__(self, status_code: int, payload: dict, headers: dict[str, str] | None = None) -> None:
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}

        def json(self):  # noqa: ANN201
            return self._payload

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, D401
            del args, kwargs

        def __enter__(self):  # noqa: ANN201
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            del exc_type, exc, tb

        def request(self, method, url, params=None, headers=None, json=None):  # noqa: ANN001, ANN201
            del method, url, params, headers, json
            calls["count"] += 1
            if calls["count"] == 1:
                return DummyResponse(429, {}, {"Retry-After": "0"})
            return DummyResponse(200, {"data": [{"paperId": "S2_OK", "title": "Recovered"}]})

    monkeypatch.setattr(connectors.httpx, "Client", DummyClient)
    monkeypatch.setattr(connectors.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(connectors.random, "uniform", lambda a, b: b)
    object.__setattr__(connectors.settings, "semantic_scholar_max_retries", 2)
    object.__setattr__(connectors.settings, "semantic_scholar_min_interval_seconds", 0.0)
    object.__setattr__(connectors.settings, "semantic_scholar_cooldown_seconds", 0.0)
    object.__setattr__(connectors.settings, "semantic_scholar_timeout_seconds", 1.0)
    try:
        payload = connectors._semantic_scholar_request_json(  # noqa: SLF001
            "GET",
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
            params={"query": "semiconductor wastewater", "limit": 10},
        )
    finally:
        object.__setattr__(connectors.settings, "semantic_scholar_max_retries", original_retries)
        object.__setattr__(connectors.settings, "semantic_scholar_min_interval_seconds", original_interval)
        object.__setattr__(connectors.settings, "semantic_scholar_cooldown_seconds", original_cooldown)
        object.__setattr__(connectors.settings, "semantic_scholar_timeout_seconds", original_timeout)

    assert payload["data"][0]["paperId"] == "S2_OK"
    assert calls["count"] == 2
    assert sleeps


def test_semantic_scholar_request_uses_cache(monkeypatch):
    _reset_semantic_scholar_state()
    original_ttl = connectors.settings.semantic_scholar_cache_ttl_seconds
    original_interval = connectors.settings.semantic_scholar_min_interval_seconds
    calls = {"count": 0}

    class DummyResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self):  # noqa: ANN201
            return {"data": [{"paperId": "S2_CACHE", "title": "Cached"}]}

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002
            del args, kwargs

        def __enter__(self):  # noqa: ANN201
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            del exc_type, exc, tb

        def request(self, method, url, params=None, headers=None, json=None):  # noqa: ANN001, ANN201
            del method, url, params, headers, json
            calls["count"] += 1
            return DummyResponse()

    monkeypatch.setattr(connectors.httpx, "Client", DummyClient)
    monkeypatch.setattr(connectors.time, "sleep", lambda seconds: None)
    object.__setattr__(connectors.settings, "semantic_scholar_cache_ttl_seconds", 3600.0)
    object.__setattr__(connectors.settings, "semantic_scholar_min_interval_seconds", 0.0)
    try:
        first = connectors._semantic_scholar_request_json(  # noqa: SLF001
            "GET",
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
            params={"query": "reuse", "limit": 5},
        )
        second = connectors._semantic_scholar_request_json(  # noqa: SLF001
            "GET",
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
            params={"query": "reuse", "limit": 5},
        )
    finally:
        object.__setattr__(connectors.settings, "semantic_scholar_cache_ttl_seconds", original_ttl)
        object.__setattr__(connectors.settings, "semantic_scholar_min_interval_seconds", original_interval)

    assert first == second
    assert calls["count"] == 1


def test_semantic_scholar_forward_citations_uses_direct_doi_endpoint_with_pagination():
    from types import SimpleNamespace

    connector = connectors.SemanticScholarConnector()
    source = SimpleNamespace(id="src_doi", doi="10.1000/direct", source_native_id="S2_PARENT", title="Parent")
    requests: list[tuple[str, dict | None, dict | None, float]] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del headers
        requests.append((f"{method} {url}", params, json_body, retry_multiplier))
        if method == "POST" and url.endswith("/paper/DOI:10.1000/direct/citations"):
            token = (json_body or {}).get("token")
            if token is None:
                return {
                    "data": [
                        {
                            "citingPaper": {
                                "paperId": "S2_A",
                                "title": "Citing A",
                                "year": 2021,
                                "url": "https://example.org/a",
                                "externalIds": {"DOI": "10.1000/a"},
                                "authors": [{"name": "A"}],
                            }
                        }
                    ],
                    "next": "token-2",
                }
            return {
                "data": [
                    {
                        "citingPaper": {
                            "paperId": "S2_B",
                            "title": "Citing B",
                            "year": 2022,
                            "url": "https://example.org/b",
                            "externalIds": {"DOI": "10.1000/b"},
                            "authors": [{"name": "B"}],
                        }
                    }
                ]
            }
        if method == "GET":
            return {"references": []}
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        backward, forward = connector.expand_citations(source, per_direction_limit=0, iteration=1)
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert backward == []
    assert [row["semantic_scholar_id"] for row in forward] == ["S2_A", "S2_B"]
    assert requests[0][0].endswith("/paper/DOI:10.1000/direct/citations")
    assert requests[0][2]["fields"] == connectors._semantic_scholar_forward_citation_fields()  # noqa: SLF001
    assert requests[0][3] == connectors.settings.semantic_scholar_citations_retry_multiplier
    assert requests[1][2]["token"] == "token-2"


def test_semantic_scholar_forward_citations_falls_back_to_get_citations_endpoint_after_post_modes_fail():
    from types import SimpleNamespace

    source = SimpleNamespace(id="src_doi", doi="10.1000/direct", title="Parent")
    requests: list[tuple[str, dict | None, dict | None]] = []

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del headers, retry_multiplier
        requests.append((f"{method} {url}", params, json_body))
        if method == "POST":
            return {}
        if method == "GET" and url.endswith("/paper/DOI:10.1000/direct/citations"):
            return {
                "data": [
                    {
                        "citingPaper": {
                            "paperId": "S2_GET",
                            "title": "GET Citing",
                            "year": 2024,
                            "url": "https://example.org/get",
                            "externalIds": {"DOI": "10.1000/get"},
                            "authors": [{"name": "G"}],
                        }
                    }
                ]
            }
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        rows = connectors._semantic_scholar_fetch_forward_citations_by_doi(source, per_direction_limit=0)  # noqa: SLF001
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert len(rows) == 1
    assert rows[0]["semantic_scholar_id"] == "S2_GET"
    assert requests[0][0].startswith("POST ")
    assert requests[1][0].startswith("POST ")
    assert requests[2][0].startswith("GET ")
    assert requests[2][1]["fields"] == connectors._semantic_scholar_forward_citation_fields()  # noqa: SLF001


def test_semantic_scholar_forward_citations_dedups_across_pages():
    from types import SimpleNamespace

    source = SimpleNamespace(id="src_doi", doi="10.1000/direct", title="Parent")

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del url, params, headers, retry_multiplier
        if method == "POST":
            token = (json_body or {}).get("token")
            paper = {
                "citingPaper": {
                    "paperId": "S2_DUP",
                    "title": "Dup",
                    "year": 2021,
                    "url": "https://example.org/dup",
                    "externalIds": {"DOI": "10.1000/dup"},
                    "authors": [{"name": "A"}],
                }
            }
            if token is None:
                return {"data": [paper], "next": "token-2"}
            return {"data": [paper]}
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        rows = connectors._semantic_scholar_fetch_forward_citations_by_doi(source, per_direction_limit=0)  # noqa: SLF001
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert len(rows) == 1
    assert rows[0]["semantic_scholar_id"] == "S2_DUP"


def test_semantic_scholar_forward_citations_falls_back_to_embedded_citations_when_direct_empty():
    from types import SimpleNamespace

    connector = connectors.SemanticScholarConnector()
    source = SimpleNamespace(id="src_doi", doi="10.1000/direct", source_native_id="S2_PARENT", title="Parent")

    def fake_request_json(method, url, *, params=None, headers=None, json_body=None, retry_multiplier=1.0):  # noqa: ANN001
        del params, headers, json_body, retry_multiplier
        if method == "POST" and url.endswith("/paper/DOI:10.1000/direct/citations"):
            return {}
        if method == "GET" and url.endswith("/paper/DOI:10.1000/direct"):
            return {
                "references": [],
                "citations": [
                    {
                        "paperId": "S2_FALLBACK",
                        "title": "Fallback Citing",
                        "year": 2023,
                        "url": "https://example.org/fallback",
                        "externalIds": {"DOI": "10.1000/fallback"},
                    }
                ],
            }
        return {}

    original = connectors._semantic_scholar_request_json  # noqa: SLF001
    try:
        _reset_semantic_scholar_state()
        connectors._semantic_scholar_request_json = fake_request_json  # noqa: SLF001
        backward, forward = connector.expand_citations(source, per_direction_limit=0, iteration=1)
    finally:
        connectors._semantic_scholar_request_json = original  # noqa: SLF001

    assert backward == []
    assert len(forward) == 1
    assert forward[0]["semantic_scholar_id"] == "S2_FALLBACK"


def test_semantic_scholar_request_cache_key_includes_json_body(monkeypatch):
    _reset_semantic_scholar_state()
    original_ttl = connectors.settings.semantic_scholar_cache_ttl_seconds
    original_interval = connectors.settings.semantic_scholar_min_interval_seconds
    calls: list[dict[str, object]] = []

    class DummyResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, marker: str) -> None:
            self.marker = marker

        def json(self):  # noqa: ANN201
            return {"marker": self.marker}

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002
            del args, kwargs

        def __enter__(self):  # noqa: ANN201
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            del exc_type, exc, tb

        def request(self, method, url, params=None, headers=None, json=None):  # noqa: ANN001, ANN201
            del method, url, params, headers
            calls.append(json or {})
            return DummyResponse(str((json or {}).get("token")))

    monkeypatch.setattr(connectors.httpx, "Client", DummyClient)
    monkeypatch.setattr(connectors.time, "sleep", lambda seconds: None)
    object.__setattr__(connectors.settings, "semantic_scholar_cache_ttl_seconds", 3600.0)
    object.__setattr__(connectors.settings, "semantic_scholar_min_interval_seconds", 0.0)
    try:
        first = connectors._semantic_scholar_request_json(  # noqa: SLF001
            "POST",
            "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1000/x/citations",
            json_body={"fields": "x", "token": "a"},
        )
        second = connectors._semantic_scholar_request_json(  # noqa: SLF001
            "POST",
            "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1000/x/citations",
            json_body={"fields": "x", "token": "b"},
        )
    finally:
        object.__setattr__(connectors.settings, "semantic_scholar_cache_ttl_seconds", original_ttl)
        object.__setattr__(connectors.settings, "semantic_scholar_min_interval_seconds", original_interval)

    assert first["marker"] == "a"
    assert second["marker"] == "b"
    assert len(calls) == 2


def test_brave_allowlist_filters_non_allowed_domains(tmp_path):
    connector = connectors.BraveConnector()
    allowlist_path = tmp_path / "domains_allowlist.txt"
    allowlist_path.write_text("ieee.org\nacm.org\n", encoding="utf-8")

    def fake_request_json(method, url, *, params=None, headers=None):  # noqa: ANN001
        del method, url, params, headers
        return {
            "web": {
                "results": [
                    {
                        "title": "Allowed IEEE result",
                        "url": "https://ieee.org/doc/1",
                        "description": "UPW cleaning",
                        "age": "Published 2024-01-01",
                    },
                    {
                        "title": "Denied random blog",
                        "url": "https://random-blog.example/post",
                        "description": "Not allowlisted",
                        "age": "Published 2024-01-01",
                    },
                ]
            }
        }

    original_request = connectors._request_json  # noqa: SLF001
    original_key = connectors.settings.brave_api_key
    original_path = connectors.settings.domains_allowlist_path
    try:
        connectors._request_json = fake_request_json  # noqa: SLF001
        object.__setattr__(connectors.settings, "brave_api_key", "x")
        object.__setattr__(connectors.settings, "domains_allowlist_path", str(allowlist_path))
        out = connector.search("upw", run_id="r1", iteration=1)
    finally:
        connectors._request_json = original_request  # noqa: SLF001
        object.__setattr__(connectors.settings, "brave_api_key", original_key)
        object.__setattr__(connectors.settings, "domains_allowlist_path", original_path)

    assert len(out) == 1
    assert out[0]["url"] == "https://ieee.org/doc/1"


def test_is_allowed_url_supports_subdomains():
    allowlist = frozenset({"ieee.org"})
    assert is_allowed_url("https://ieee.org/x", allowlist)
    assert is_allowed_url("https://conf.ieee.org/x", allowlist)
    assert not is_allowed_url("https://example.org/x", allowlist)
