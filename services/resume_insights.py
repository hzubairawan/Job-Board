"""Lightweight heuristics that pull profile signals out of resume text.

These are best-effort hints for a human reviewer, not verified facts.
"""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(\+?\d[\d\s\-().]{8,16}\d)(?!\d)")
_LINKEDIN = re.compile(r"linkedin\.com/(?:in|pub)/[\w\-%]+", re.I)
_GITHUB = re.compile(r"github\.com/[\w\-]+", re.I)
_YEARS_BEFORE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b[^.\n]{0,40}?experience", re.I)
_YEARS_AFTER = re.compile(r"experience[^.\n]{0,30}?(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)

_EDUCATION = (
    ("PhD / Doctorate", re.compile(r"\b(?:ph\.?d|doctorate|doctor of philosophy)\b", re.I)),
    ("Master's degree", re.compile(r"\b(?:masters?|master'?s|m\.?sc|mba|m\.?phil|m\.?tech|m\.?eng)\b", re.I)),
    ("Bachelor's degree", re.compile(r"\b(?:bachelors?|bachelor'?s|b\.?sc|b\.?tech|b\.?eng|bscs|bs)\b", re.I)),
    ("Diploma / Associate", re.compile(r"\b(?:diploma|associate degree|dae)\b", re.I)),
)


def extract_profile_signals(text):
    """Return a dict of heuristic signals found in `text`."""
    text = text or ""
    years = [int(m) for pattern in (_YEARS_BEFORE, _YEARS_AFTER) for m in pattern.findall(text)]
    years = [y for y in years if 0 < y <= 45]
    education = next((label for label, pattern in _EDUCATION if pattern.search(text)), None)

    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    linkedin = _LINKEDIN.search(text)
    github = _GITHUB.search(text)
    return {
        "years_experience": max(years) if years else None,
        "education": education,
        "email": email.group(0) if email else None,
        "phone": re.sub(r"\s+", " ", phone.group(1)).strip() if phone else None,
        "linkedin": linkedin.group(0) if linkedin else None,
        "github": github.group(0) if github else None,
        "word_count": len(text.split()),
    }
