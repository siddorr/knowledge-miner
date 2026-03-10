from knowledge_miner.dedup import canonical_id, is_fuzzy_duplicate
from knowledge_miner.scoring import decision_from_score, score_text


def test_scoring_threshold_decisions():
    assert decision_from_score(5.0) == (True, "auto_accept")
    assert decision_from_score(4.99) == (False, "needs_review")
    assert decision_from_score(3.0) == (False, "needs_review")
    assert decision_from_score(2.99) == (False, "auto_reject")


def test_canonical_id_precedence():
    by_doi = canonical_id(
        doi="10.1000/ABC",
        url="https://example.org/a",
        title="T",
        year=2021,
        openalex_id="W123",
        semantic_scholar_id="S2X",
    )
    assert by_doi == "doi:10.1000/abc"

    by_openalex = canonical_id(
        doi=None,
        url="https://example.org/a",
        title="T",
        year=2021,
        openalex_id="W123",
        semantic_scholar_id="S2X",
    )
    assert by_openalex == "openalex:W123"

    by_s2 = canonical_id(
        doi=None,
        url="https://example.org/a",
        title="T",
        year=2021,
        semantic_scholar_id="S2X",
    )
    assert by_s2 == "s2:S2X"


def test_fuzzy_duplicate_title_year():
    assert is_fuzzy_duplicate(
        title_a="Ultrapure Water Process Optimization",
        year_a=2021,
        title_b="Ultrapure Water Process Optimisation",
        year_b=2021,
    )
    assert not is_fuzzy_duplicate(
        title_a="Ultrapure Water Process Optimization",
        year_a=2021,
        title_b="Agriculture Irrigation Design",
        year_b=2021,
    )


def test_upw_production_signal_boosts_borderline_to_review():
    title = "The production of ultrapure water by membrane capacitive deionization (MCDI) technology"
    score = score_text(title, None)
    accepted, decision = decision_from_score(score)
    assert score >= 3.0
    assert accepted is False
    assert decision == "needs_review"


def test_generic_upw_acronym_without_production_stays_reject():
    title = "When Does UPW Model Become Invalid for XL-MIMO With Directional Array Elements?"
    score = score_text(title, None)
    accepted, decision = decision_from_score(score)
    assert score < 3.0
    assert accepted is False
    assert decision == "auto_reject"
