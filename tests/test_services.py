"""Tests for PDF extraction and the NLP screening engine (no web framework needed)."""
import pytest

from services.resume_parser import EmptyPDFError, InvalidPDFError, extract_text_from_pdf
from services.resume_screener import (
    build_reference_text,
    clean_text,
    compute_similarity,
    find_skill_matches,
    parse_skills,
    screen_resume,
)
from tests.helpers import (
    CHEF_RESUME,
    JOB_DESCRIPTION,
    JOB_SKILLS,
    PYTHON_RESUME,
    make_blank_pdf,
    make_pdf,
)


# ------------------------------------------------------------- PDF parsing
def test_extracts_text_from_valid_pdf():
    text = extract_text_from_pdf(make_pdf(PYTHON_RESUME))
    assert "Python" in text and "Flask" in text and "Ayesha Khan" in text


def test_empty_bytes_rejected():
    with pytest.raises(InvalidPDFError):
        extract_text_from_pdf(b"")


def test_non_pdf_bytes_rejected():
    with pytest.raises(InvalidPDFError):
        extract_text_from_pdf(b"This is just a text file pretending to be a resume.")


def test_corrupted_pdf_with_valid_header_rejected():
    with pytest.raises(InvalidPDFError):
        extract_text_from_pdf(b"%PDF-1.4\n1 0 obj << /garbage >> endobj\nnot a real pdf")


def test_truncated_pdf_rejected():
    data = make_pdf(PYTHON_RESUME)
    with pytest.raises((InvalidPDFError, EmptyPDFError)):
        extract_text_from_pdf(data[: len(data) // 3])


def test_pdf_without_text_is_reported_as_empty():
    with pytest.raises(EmptyPDFError) as info:
        extract_text_from_pdf(make_blank_pdf())
    assert "scanned" in str(info.value).lower()


def test_extract_from_path(tmp_path):
    path = tmp_path / "cv.pdf"
    path.write_bytes(make_pdf(PYTHON_RESUME))
    assert "Flask" in extract_text_from_pdf(str(path))


# ------------------------------------------------------------ text cleaning
def test_clean_text_lowercases_and_strips_punctuation():
    assert clean_text("Hello, WORLD!! (Python)") == "hello world python"


def test_clean_text_keeps_technical_tokens():
    cleaned = clean_text("Skills: C++, C#, Node.js and Python.")
    for token in ("c++", "c#", "node.js", "python"):
        assert token in cleaned.split()


def test_clean_text_removes_urls_and_emails():
    cleaned = clean_text("Contact me@example.com or https://example.com/me now")
    assert "example" not in cleaned and "https" not in cleaned
    assert cleaned == "contact or now"


def test_clean_text_handles_none_and_empty():
    assert clean_text(None) == ""
    assert clean_text("   \n ") == ""


def test_parse_skills_dedupes_and_trims():
    assert parse_skills(" Python,python , SQL,,  Git ") == ["Python", "SQL", "Git"]


def test_reference_text_combines_description_and_skills():
    ref = build_reference_text("Build APIs", "Python, SQL")
    assert "Build APIs" in ref and "Python" in ref and "SQL" in ref


# ------------------------------------------------------------- similarity
def reference():
    return build_reference_text(JOB_DESCRIPTION, JOB_SKILLS)


def test_relevant_resume_scores_higher_than_irrelevant():
    relevant = compute_similarity(" ".join(PYTHON_RESUME), reference())
    irrelevant = compute_similarity(" ".join(CHEF_RESUME), reference())
    assert relevant > irrelevant
    assert relevant > 15
    assert irrelevant < relevant / 2


def test_score_is_percentage_in_range():
    for resume in (PYTHON_RESUME, CHEF_RESUME):
        score = compute_similarity(" ".join(resume), reference())
        assert 0.0 <= score <= 100.0


def test_identical_text_scores_near_100():
    assert compute_similarity(JOB_DESCRIPTION, JOB_DESCRIPTION) == pytest.approx(100.0, abs=0.1)


def test_empty_inputs_score_zero():
    assert compute_similarity("", reference()) == 0.0
    assert compute_similarity("python", "") == 0.0
    assert compute_similarity("the and of", "the and of") == 0.0  # only stop words


def test_scores_are_deterministic_and_not_hardcoded():
    a = compute_similarity(" ".join(PYTHON_RESUME), reference())
    b = compute_similarity(" ".join(PYTHON_RESUME), reference())
    c = compute_similarity(" ".join(PYTHON_RESUME[:2]), reference())
    assert a == b
    assert a != c


def test_more_matching_content_increases_score():
    short = compute_similarity("python developer", reference())
    long = compute_similarity(" ".join(PYTHON_RESUME), reference())
    assert long > short


# ---------------------------------------------------------- skill matching
def test_skill_matching_respects_word_boundaries():
    matched, missing = find_skill_matches("Strong JavaScript and C++ experience", "Java, JavaScript, C++, Go")
    assert matched == ["JavaScript", "C++"]
    assert missing == ["Java", "Go"]


def test_skill_matching_is_case_insensitive():
    matched, _ = find_skill_matches("worked with PYTHON and flask", "Python, Flask")
    assert matched == ["Python", "Flask"]


# ------------------------------------------------------------- end to end
def test_screen_resume_from_real_pdf_end_to_end():
    text = extract_text_from_pdf(make_pdf(PYTHON_RESUME))
    result = screen_resume(text, JOB_DESCRIPTION, JOB_SKILLS)
    assert result.score > 15
    assert {"Python", "Flask", "SQL"} <= set(result.matched_skills)
    assert "Cooking" not in result.matched_skills

    chef_text = extract_text_from_pdf(make_pdf(CHEF_RESUME))
    chef = screen_resume(chef_text, JOB_DESCRIPTION, JOB_SKILLS)
    assert chef.score < result.score
    assert chef.matched_skills == []
