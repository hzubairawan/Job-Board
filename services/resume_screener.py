"""NLP screening engine: text cleaning, TF-IDF vectorisation and cosine similarity.

The score is a TEXT SIMILARITY indicator between a resume and a job's
description + required skills. It is not a measure of candidate suitability.
"""
import re
from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Keep letters, digits and the symbols that matter in technical terms (c++, c#, node.js).
_DISALLOWED = re.compile(r"[^a-z0-9+#.\s]")
_URL = re.compile(r"https?://\S+|www\.\S+")
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_STRAY_DOTS = re.compile(r"(?<![a-z0-9])\.+|\.+(?![a-z0-9])")
_SPACES = re.compile(r"\s+")
_TOKEN = r"[a-z0-9][a-z0-9+#.]*"


@dataclass(frozen=True)
class ScreeningResult:
    score: float  # 0.0 - 100.0
    matched_skills: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)


def clean_text(text):
    """Lowercase, strip URLs/emails/punctuation and collapse whitespace."""
    text = (text or "").lower().replace(" ", " ")
    text = _URL.sub(" ", text)
    text = _EMAIL.sub(" ", text)
    text = _DISALLOWED.sub(" ", text)
    text = _STRAY_DOTS.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def parse_skills(raw):
    """Split a comma-separated skills string into a de-duplicated list."""
    seen, skills = set(), []
    for part in (raw or "").replace("\n", ",").split(","):
        skill = part.strip()
        if skill and skill.lower() not in seen:
            seen.add(skill.lower())
            skills.append(skill)
    return skills


def build_reference_text(description, skills):
    """Combine the job description and required skills into one reference document."""
    return f"{description or ''} {' '.join(parse_skills(skills))}".strip()


def compute_similarity(resume_text, reference_text):
    """TF-IDF + cosine similarity, returned as a percentage in [0, 100]."""
    resume_clean = clean_text(resume_text)
    reference_clean = clean_text(reference_text)
    if not resume_clean or not reference_clean:
        return 0.0
    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        token_pattern=_TOKEN,
        sublinear_tf=True,
    )
    try:
        matrix = vectorizer.fit_transform([resume_clean, reference_clean])
    except ValueError:  # empty vocabulary (e.g. only stop words)
        return 0.0
    similarity = float(cosine_similarity(matrix[0], matrix[1])[0][0])
    return round(max(0.0, min(1.0, similarity)) * 100, 1)


def find_skill_matches(resume_text, skills):
    """Return (matched, missing) required skills found in the resume text."""
    cleaned = clean_text(resume_text)
    matched, missing = [], []
    for skill in parse_skills(skills):
        needle = clean_text(skill)
        pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
        (matched if needle and re.search(pattern, cleaned) else missing).append(skill)
    return matched, missing


def screen_resume(resume_text, job_description, job_skills):
    """Full screening of one resume against one job."""
    reference = build_reference_text(job_description, job_skills)
    score = compute_similarity(resume_text, reference)
    matched, missing = find_skill_matches(resume_text, job_skills)
    return ScreeningResult(score=score, matched_skills=matched, missing_skills=missing)
