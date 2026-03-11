from __future__ import annotations

from pathlib import Path

from knowledge_miner import parse


def test_extract_html_text_prefers_main_content_block():
    html = """
    <html>
      <body>
        <nav>Home Menu Links Links Links</nav>
        <main>
          <h1>UPW Main Content</h1>
          <p>Ultrapure water is critical in semiconductor fabs.</p>
          <p>TOC and particle control are key factors for yield.</p>
        </main>
        <footer>copyright</footer>
      </body>
    </html>
    """
    text, sections = parse._extract_html_text(html)  # noqa: SLF001
    assert "UPW Main Content" in text
    assert "semiconductor fabs" in text
    assert sections >= 1


def test_extract_pdf_text_falls_back_to_naive(tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"UPW PDF fallback test document body")
    text, parser_used = parse._extract_pdf_text(pdf_path)  # noqa: SLF001
    assert "UPW PDF fallback test" in text
    assert parser_used in {"pdf_naive", "pdf_naive_latin1"}


def test_deterministic_chunk_id_is_stable():
    c1 = parse._deterministic_chunk_id(  # noqa: SLF001
        parsed_document_id="doc_1",
        chunk_index=0,
        chunk_content_hash="abc",
    )
    c2 = parse._deterministic_chunk_id(  # noqa: SLF001
        parsed_document_id="doc_1",
        chunk_index=0,
        chunk_content_hash="abc",
    )
    c3 = parse._deterministic_chunk_id(  # noqa: SLF001
        parsed_document_id="doc_1",
        chunk_index=1,
        chunk_content_hash="abc",
    )
    assert c1 == c2
    assert c1 != c3
