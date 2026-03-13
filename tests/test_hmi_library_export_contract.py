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
    for action in ("Export ZIP with PDFs", "Export Metadata CSV", "Include selected", "Exclude selected"):
        assert action in library
    assert "Top 20" in library


def test_library_export_includes_preview_and_technical_details():
    library = _library_section()
    assert "<h3>Preview</h3>" in library
    assert "Parsed Document Detail" in library
    assert "Parsed Document Full Text" in library
    assert "Related Source Context" in library
