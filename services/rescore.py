"""Re-scoring of stored applications (e.g. after an employer edits a job)."""
from pathlib import Path

from services.resume_parser import ResumeParseError, extract_text_from_pdf
from services.resume_screener import screen_resume
from services.storage import upload_dir


def load_resume_text(application):
    """Stored text if present, otherwise re-extract it from the stored PDF ('' on failure)."""
    if application.resume_text:
        return application.resume_text
    try:
        return extract_text_from_pdf(str(upload_dir() / Path(application.resume_filename).name))
    except (ResumeParseError, OSError):
        return ""


def rescore_job_applications(job):
    """Recompute score and matched skills for every application of `job`.

    Returns how many applications were updated. The caller commits.
    """
    updated = 0
    for application in job.applications:
        text = load_resume_text(application)
        if not text:
            continue
        result = screen_resume(text, job.description, job.skills)
        application.resume_text = text
        application.match_score = result.score
        application.matched_skills = ", ".join(result.matched_skills)
        updated += 1
    return updated
