from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


def _review_section() -> str:
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    body = response.text
    start = body.index('<section id="review"')
    end = body.index('<section id="documents"')
    return body[start:end]


def test_review_has_two_pane_structure():
    review = _review_section()
    assert 'class="review-layout"' in review
    assert 'id="reviewRows"' in review
    assert 'id="reviewDetailPanel"' in review


def test_review_table_columns_match_contract():
    review = _review_section()
    for label in ("Year", "Cit", "Score", "Title"):
        assert f"<th>{label}</th>" in review


def test_review_details_metadata_and_actions_match_contract():
    review = _review_section()
    assert "Year: - | Journal: - | Citations: - | Authors: - | Link: -" in review
    for action in ("Accept", "Reject", "Later"):
        assert f">{action}</button>" in review
    for hint in ("[A] Accept", "[R] Reject", "[L] Later"):
        assert hint in review


def test_review_has_no_manual_run_id_selector():
    review = _review_section()
    assert "reviewRunChooser" not in review
    assert "Discovery Run ID" not in review
    assert "Acquisition Run ID" not in review
