from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


def _library_section() -> str:
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    body = response.text
    start = body.index('<section id="library"')
    end = body.index('<section id="advanced"')
    return body[start:end]


def test_library_export_heading_and_description_present():
    library = _library_section()
    assert "<h2>Library Export</h2>" in library
    assert "Ranked export workspace for final paper package generation." in library


def test_library_export_ranked_table_and_controls_present():
    library = _library_section()
    for label in ("Rank", "AI Score", "Year", "Citations", "Title"):
        assert f"<th>{label}</th>" in library
    for action in ("Export ZIP with PDFs", "Export Metadata CSV", "Add to export list", "Remove from export list"):
        assert action in library
    assert "Top 20" in library


def test_library_export_uses_two_pane_operator_details_without_technical_blocks():
    library = _library_section()
    assert "library-layout" in library
    assert "Paper Details" in library
    assert "Matching papers:" in library
    assert "Highest AI relevance:" in library
    assert "Lowest AI relevance:" in library
    assert "Parsed Document Detail" not in library
    assert "Parsed Document Full Text" not in library
    assert "Related Source Context" not in library
