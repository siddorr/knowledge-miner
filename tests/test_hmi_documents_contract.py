from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


def _documents_section() -> str:
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    body = response.text
    start = body.index('<section id="documents"')
    end = body.index('<section id="library"')
    return body[start:end]


def test_documents_has_summary_row_and_primary_actions():
    documents = _documents_section()
    for label in ("Downloaded:", "Failed:", "Manual uploads:", "Pending:"):
        assert label in documents
    for action in ("Download missing", "Retry failed", "Export CSV"):
        assert action in documents


def test_documents_table_columns_match_contract():
    documents = _documents_section()
    for label in ("Rank", "Score", "Year", "Cit", "Title", "Status"):
        assert f"<th>{label}</th>" in documents


def test_documents_has_batch_upload_and_secondary_controls():
    documents = _documents_section()
    assert 'id="batchUploadForm"' in documents
    assert "Upload PDF Batch" in documents
    assert "Select All" in documents
    assert "Deselect All" in documents
    assert "Copy Selected DOI/URL" in documents
