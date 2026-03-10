import knowledge_miner.connectors as connectors
from knowledge_miner.domain_allowlist import is_allowed_url


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

    def fake_request_json(method, url, *, params=None, headers=None):  # noqa: ANN001
        assert method == "GET"
        assert "/paper/S2_PARENT" in url
        assert "fields" in (params or {})
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

    original = connectors._request_json  # noqa: SLF001
    try:
        connectors._request_json = fake_request_json  # noqa: SLF001
        backward, forward = connector.expand_citations(source, per_direction_limit=10, iteration=1)
    finally:
        connectors._request_json = original  # noqa: SLF001

    assert len(backward) == 1
    assert len(forward) == 1
    assert backward[0]["discovery_method"] == "backward_citation"
    assert backward[0]["semantic_scholar_id"] == "S2_REF_1"
    assert forward[0]["discovery_method"] == "forward_citation"
    assert forward[0]["semantic_scholar_id"] == "S2_CIT_1"


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
