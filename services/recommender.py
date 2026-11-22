"""Job recommendations: rank open jobs against an applicant's most recent resume."""
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from extensions import db
from models import Application, Job, open_jobs_clause
from services.rescore import load_resume_text
from services.resume_screener import screen_resume

MIN_SCORE = 5.0


def recommend_jobs(user, limit=3):
    """Return up to `limit` dicts {job, score, matched} for jobs the user has not applied to."""
    recent = db.session.scalars(
        select(Application)
        .where(Application.applicant_id == user.id)
        .order_by(Application.applied_at.desc())
        .limit(2)
    ).all()
    text = "\n".join(t for t in (load_resume_text(a) for a in recent) if t)
    if not text:
        return []

    applied = {a.job_id for a in db.session.scalars(
        select(Application).where(Application.applicant_id == user.id))}
    stmt = select(Job).where(open_jobs_clause()).options(joinedload(Job.employer)).limit(200)
    if applied:
        stmt = stmt.where(Job.id.not_in(list(applied)))

    ranked = []
    for job in db.session.scalars(stmt):
        result = screen_resume(text, job.description, job.skills)
        if result.score >= MIN_SCORE:
            ranked.append({"job": job, "score": result.score, "matched": result.matched_skills})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]
