"""Applicant features: job board, recommendations, saved jobs, applying, application history."""
import logging

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    EXPERIENCE_LEVELS,
    JOB_TYPES,
    ROLE_APPLICANT,
    Application,
    Job,
    SavedJob,
    open_jobs_clause,
)
from services.ai import ai_allowed, ai_enabled, analyze_match
from services.recommender import recommend_jobs
from services.resume_parser import ResumeParseError, extract_text_from_pdf
from services.resume_screener import screen_resume
from services.storage import (
    UploadError,
    delete_resume_files,
    save_resume,
    send_resume,
    validate_pdf_upload,
)
from utils import (
    escape_like,
    is_safe_redirect,
    log_activity,
    notify,
    query_args_without_page,
    role_guard,
)

log = logging.getLogger(__name__)
bp = Blueprint("applicant", __name__, url_prefix="/applicant")
bp.before_request(role_guard(ROLE_APPLICANT))

SORTS = {
    "newest": Job.created_at.desc(),
    "oldest": Job.created_at.asc(),
    "title": Job.title.asc(),
    "deadline": Job.deadline.asc(),
}


@bp.route("/dashboard")
def dashboard():
    q = request.args.get("q", "").strip()[:100]
    job_type = request.args.get("type", "")
    experience = request.args.get("exp", "")
    location = request.args.get("location", "").strip()[:100]
    sort = request.args.get("sort", "newest")
    hide_applied = request.args.get("hide_applied") == "1"
    page = request.args.get("page", 1, type=int)

    stmt = select(Job).where(open_jobs_clause()).options(joinedload(Job.employer))
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Job.title.ilike(like, escape="\\"), Job.skills.ilike(like, escape="\\")))
    if job_type in JOB_TYPES:
        stmt = stmt.where(Job.job_type == job_type)
    if experience in EXPERIENCE_LEVELS:
        stmt = stmt.where(Job.experience_level == experience)
    if location:
        stmt = stmt.where(Job.location.ilike(f"%{escape_like(location)}%", escape="\\"))

    applied_ids = set(
        db.session.scalars(select(Application.job_id).where(Application.applicant_id == current_user.id))
    )
    saved_ids = set(db.session.scalars(select(SavedJob.job_id).where(SavedJob.user_id == current_user.id)))
    if hide_applied and applied_ids:
        stmt = stmt.where(Job.id.not_in(list(applied_ids)))
    order = SORTS.get(sort, SORTS["newest"])
    if sort == "deadline":
        stmt = stmt.where(Job.deadline.is_not(None))
    stmt = stmt.order_by(order, Job.id.desc())

    pagination = db.paginate(stmt, page=page, per_page=current_app.config["PER_PAGE"], error_out=False)

    filtered = bool(q or job_type or experience or location or hide_applied or sort != "newest")
    recommended = []
    if page == 1 and not filtered:
        try:
            recommended = recommend_jobs(current_user)
        except Exception:  # recommendations must never break the job board
            log.exception("Recommendation failure")

    open_jobs = db.session.scalar(select(func.count()).select_from(Job).where(open_jobs_clause()))
    avg_score = db.session.scalar(
        select(func.avg(Application.match_score)).where(Application.applicant_id == current_user.id)
    )
    stats = {
        "open_jobs": open_jobs or 0,
        "applications": len(applied_ids),
        "saved": len(saved_ids),
        "avg_score": round(avg_score or 0, 1),
    }
    return render_template(
        "applicant/dashboard.html",
        pagination=pagination,
        jobs=pagination.items,
        applied_ids=applied_ids,
        saved_ids=saved_ids,
        recommended=recommended,
        stats=stats,
        job_types=JOB_TYPES,
        experience_levels=EXPERIENCE_LEVELS,
        filters={"q": q, "type": job_type, "exp": experience, "location": location,
                 "sort": sort, "hide_applied": hide_applied},
        qargs=query_args_without_page(),
    )


@bp.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = db.get_or_404(Job, job_id)
    application = db.session.scalar(
        select(Application).where(Application.job_id == job_id, Application.applicant_id == current_user.id)
    )
    saved = db.session.scalar(
        select(SavedJob.id).where(SavedJob.job_id == job_id, SavedJob.user_id == current_user.id)
    ) is not None
    if not job.is_open and application is None and not saved:
        abort(404)  # closed/expired jobs stay visible only to people who applied or saved them
    return render_template("applicant/job_detail.html", job=job, application=application, saved=saved,
                           check=None, ai_on=ai_enabled())


@bp.post("/jobs/<int:job_id>/check")
def check_resume(job_id):
    """Score a resume against a job WITHOUT applying or storing the file."""
    job = db.get_or_404(Job, job_id)
    application = db.session.scalar(
        select(Application).where(Application.job_id == job_id, Application.applicant_id == current_user.id)
    )
    saved = db.session.scalar(
        select(SavedJob.id).where(SavedJob.job_id == job_id, SavedJob.user_id == current_user.id)
    ) is not None
    if not job.is_open and application is None and not saved:
        abort(404)
    try:
        data, _name = validate_pdf_upload(request.files.get("resume"))
        text = extract_text_from_pdf(data)
    except (UploadError, ResumeParseError) as exc:
        flash(str(exc), "danger")
        return redirect(url_for("applicant.job_detail", job_id=job_id))

    result = screen_resume(text, job.description, job.skills)
    if ai_enabled() and not ai_allowed(current_user.id):
        flash("You have reached the hourly AI limit; showing the basic analysis instead.", "warning")
        from services.ai import _local_match
        feedback = _local_match(text, job)
    else:
        feedback = analyze_match(text, job)
    log_activity("resume_check", f"{current_user.email} -> {job.title}")
    db.session.commit()
    check = {"score": result.score, "matched": result.matched_skills, "missing": result.missing_skills,
             "feedback": feedback}
    return render_template("applicant/job_detail.html", job=job, application=application, saved=saved,
                           check=check, ai_on=ai_enabled())


@bp.post("/jobs/<int:job_id>/save")
def toggle_save(job_id):
    job = db.get_or_404(Job, job_id)
    existing = db.session.scalar(
        select(SavedJob).where(SavedJob.job_id == job.id, SavedJob.user_id == current_user.id)
    )
    try:
        if existing:
            db.session.delete(existing)
            flash("Removed from saved jobs.", "info")
        else:
            db.session.add(SavedJob(user_id=current_user.id, job_id=job.id))
            flash("Job saved. Find it under “Saved jobs”.", "success")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not update your saved jobs.", "danger")
    target = request.referrer
    return redirect(target if is_safe_redirect(target) else url_for("applicant.dashboard"))


@bp.route("/saved")
def saved():
    rows = db.session.scalars(
        select(SavedJob)
        .where(SavedJob.user_id == current_user.id)
        .options(joinedload(SavedJob.job).joinedload(Job.employer))
        .order_by(SavedJob.created_at.desc())
    ).all()
    applied_ids = set(
        db.session.scalars(select(Application.job_id).where(Application.applicant_id == current_user.id))
    )
    return render_template("applicant/saved.html", rows=rows, applied_ids=applied_ids)


@bp.post("/jobs/<int:job_id>/apply")
def apply(job_id):
    job = db.get_or_404(Job, job_id)
    detail_url = url_for("applicant.job_detail", job_id=job_id)

    if job.is_external:
        flash("This is an external listing. Please apply on the employer's website.", "info")
        return redirect(detail_url)
    if not job.is_open:
        flash("This job is closed and no longer accepts applications.", "warning")
        return redirect(url_for("applicant.dashboard"))

    already = db.session.scalar(
        select(Application.id).where(Application.job_id == job_id, Application.applicant_id == current_user.id)
    )
    if already:
        flash("You have already applied to this job.", "warning")
        return redirect(detail_url)

    try:
        data, original_name = validate_pdf_upload(request.files.get("resume"))
        text = extract_text_from_pdf(data)
    except (UploadError, ResumeParseError) as exc:
        flash(str(exc), "danger")
        return redirect(detail_url)

    result = screen_resume(text, job.description, job.skills)
    stored_name = save_resume(data)
    application = Application(
        job_id=job.id,
        applicant_id=current_user.id,
        resume_filename=stored_name,
        original_filename=original_name,
        resume_text=text,
        match_score=result.score,
        matched_skills=", ".join(result.matched_skills),
    )
    db.session.add(application)
    notify(
        job.employer_id,
        f"{current_user.full_name} applied to “{job.title}” ({result.score:.1f}% match).",
        url_for("employer.applicants", job_id=job.id),
    )
    log_activity("application", f"{current_user.email} -> {job.title}")
    try:
        db.session.commit()
    except IntegrityError:  # double-submit race against the unique constraint
        db.session.rollback()
        delete_resume_files([stored_name])
        flash("You have already applied to this job.", "warning")
    except SQLAlchemyError:
        db.session.rollback()
        delete_resume_files([stored_name])
        log.exception("Failed to save application")
        flash("We could not save your application. Please try again.", "danger")
    else:
        flash("Application submitted! Your resume has been analysed.", "success")
        return redirect(url_for("applicant.applications"))
    return redirect(detail_url)


@bp.route("/applications")
def applications():
    page = request.args.get("page", 1, type=int)
    stmt = (
        select(Application)
        .where(Application.applicant_id == current_user.id)
        .options(joinedload(Application.job).joinedload(Job.employer))
        .order_by(Application.applied_at.desc())
    )
    pagination = db.paginate(stmt, page=page, per_page=10, error_out=False)
    return render_template(
        "applicant/applications.html",
        pagination=pagination,
        applications=pagination.items,
        qargs=query_args_without_page(),
    )


@bp.post("/applications/<int:application_id>/withdraw")
def withdraw(application_id):
    application = db.get_or_404(Application, application_id)
    if application.applicant_id != current_user.id:
        abort(404)
    job = application.job
    stored_name = application.resume_filename
    notify(job.employer_id, f"{current_user.full_name} withdrew their application for “{job.title}”.",
           url_for("employer.applicants", job_id=job.id))
    log_activity("application_withdrawn", f"{current_user.email} -> {job.title}")
    db.session.delete(application)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        log.exception("Failed to withdraw application")
        flash("Could not withdraw your application.", "danger")
    else:
        delete_resume_files([stored_name])
        flash("Application withdrawn. You can apply again while the job is open.", "success")
    return redirect(url_for("applicant.applications"))


@bp.get("/applications/<int:application_id>/resume")
def download_resume(application_id):
    application = db.get_or_404(Application, application_id)
    if application.applicant_id != current_user.id:
        abort(404)
    return send_resume(application)
