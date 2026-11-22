"""Employer features: dashboard, job management, ranked applicants, AI briefs, exports."""
import csv
import io
import json
import logging
import re
from collections import Counter
from datetime import timedelta

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from extensions import db
from models import (
    APPLICATION_STATUSES,
    EXPERIENCE_LEVELS,
    JOB_CLOSED,
    JOB_OPEN,
    JOB_STATUSES,
    JOB_TYPES,
    ROLE_EMPLOYER,
    Application,
    Job,
    User,
    utcnow,
)
from services.ai import AIError, ai_allowed, ai_enabled, candidate_brief, draft_job
from services.rescore import load_resume_text, rescore_job_applications
from services.resume_insights import extract_profile_signals
from services.resume_screener import parse_skills
from services.storage import delete_resume_files, send_resume
from utils import csv_safe, escape_like, log_activity, notify, parse_date, role_guard

log = logging.getLogger(__name__)
bp = Blueprint("employer", __name__, url_prefix="/employer")
bp.before_request(role_guard(ROLE_EMPLOYER))

BUCKETS = [(0, 10, "0-10%"), (10, 20, "10-20%"), (20, 30, "20-30%"), (30, 40, "30-40%"), (40, 101, "40%+")]


def score_distribution(scores):
    counts = [0] * len(BUCKETS)
    for score in scores:
        for i, (low, high, _label) in enumerate(BUCKETS):
            if low <= (score or 0) < high:
                counts[i] += 1
                break
    return {"type": "bar", "label": "Applicants", "labels": [b[2] for b in BUCKETS], "data": counts}


def _owned_job_or_404(job_id):
    job = db.get_or_404(Job, job_id)
    if job.employer_id != current_user.id:
        abort(404)  # do not reveal that other employers' jobs exist
    return job


def _owned_application_or_404(application_id):
    application = db.get_or_404(Application, application_id)
    if application.job.employer_id != current_user.id:
        abort(404)
    return application


def _parse_job_form(form, existing=None):
    data = {
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "skills": ", ".join(parse_skills(form.get("skills", ""))),
        "location": form.get("location", "").strip(),
        "job_type": form.get("job_type", ""),
        "experience_level": form.get("experience_level", "").strip(),
        "salary_range": form.get("salary_range", "").strip(),
        "deadline": None,
        "status": form.get("status", JOB_OPEN),
    }
    errors = []
    if not 3 <= len(data["title"]) <= 150:
        errors.append("Job title must be between 3 and 150 characters.")
    if len(data["description"]) < 30:
        errors.append("Please provide a detailed description (at least 30 characters).")
    if len(data["description"]) > 10000:
        errors.append("The description is too long (maximum 10,000 characters).")
    if not data["skills"]:
        errors.append("Add at least one required skill (comma-separated).")
    if len(data["skills"]) > 1000:
        errors.append("The skills list is too long.")
    if len(data["location"]) > 100:
        errors.append("Location must be 100 characters or fewer.")
    if data["job_type"] not in JOB_TYPES:
        errors.append("Please choose a valid job type.")
    if data["experience_level"] and data["experience_level"] not in EXPERIENCE_LEVELS:
        errors.append("Please choose a valid experience level.")
    if len(data["salary_range"]) > 60:
        errors.append("Salary range must be 60 characters or fewer.")
    if data["status"] not in JOB_STATUSES:
        errors.append("Please choose a valid status.")
    raw_deadline = form.get("deadline", "").strip()
    if raw_deadline:
        deadline = parse_date(raw_deadline)
        if deadline is None:
            errors.append("Deadline must be a valid date.")
        elif deadline < utcnow().date() and not (existing and existing.deadline == deadline):
            errors.append("Deadline cannot be in the past.")
        else:
            data["deadline"] = deadline
    data["experience_level"] = data["experience_level"] or None
    data["salary_range"] = data["salary_range"] or None
    return data, errors


# ---------------------------------------------------------------- dashboard
@bp.route("/dashboard")
def dashboard():
    uid = current_user.id

    def count(*conds):
        return db.session.scalar(select(func.count()).select_from(Job).where(Job.employer_id == uid, *conds)) or 0

    total_jobs = count()
    open_jobs = count(Job.status == JOB_OPEN)
    closed_jobs = count(Job.status == JOB_CLOSED)

    apps_q = select(Application).join(Job).where(Job.employer_id == uid)
    total_applicants = db.session.scalar(select(func.count()).select_from(apps_q.subquery())) or 0
    scores = db.session.scalars(select(Application.match_score).join(Job).where(Job.employer_id == uid)).all()
    recent = db.session.scalars(
        apps_q.options(joinedload(Application.applicant), joinedload(Application.job))
        .order_by(Application.applied_at.desc()).limit(6)
    ).all()
    top = db.session.scalars(
        apps_q.options(joinedload(Application.applicant), joinedload(Application.job))
        .order_by(Application.match_score.desc(), Application.applied_at.asc()).limit(5)
    ).all()

    status_rows = db.session.execute(
        select(Application.status, func.count()).join(Job).where(Job.employer_id == uid).group_by(Application.status)
    ).all()
    status_counts = {s: 0 for s in APPLICATION_STATUSES}
    status_counts.update({s: c for s, c in status_rows})

    per_job = db.session.execute(
        select(Job.title, func.count(Application.id).label("n"))
        .select_from(Job).outerjoin(Application, Application.job_id == Job.id)
        .where(Job.employer_id == uid).group_by(Job.id)
        .order_by(func.count(Application.id).desc()).limit(6)
    ).all()

    today = utcnow().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    since = utcnow() - timedelta(days=7)
    per_day = {d: 0 for d in days}
    for ts in db.session.scalars(
        select(Application.applied_at).join(Job).where(Job.employer_id == uid, Application.applied_at >= since)
    ):
        if ts.date() in per_day:
            per_day[ts.date()] += 1

    charts = {
        "status": {"type": "doughnut", "labels": [s.capitalize() for s in status_counts],
                   "data": list(status_counts.values())},
        "per_job": {"type": "bar", "label": "Applicants",
                    "labels": [(t[:22] + "…") if len(t) > 22 else t for t, _ in per_job],
                    "data": [n for _, n in per_job]},
        "weekly": {"type": "line", "label": "Applications", "labels": [d.strftime("%d %b") for d in days],
                   "data": list(per_day.values())},
        "distribution": score_distribution(scores),
    }
    stats = {
        "total_jobs": total_jobs, "open_jobs": open_jobs, "closed_jobs": closed_jobs,
        "total_applicants": total_applicants,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "shortlisted": status_counts["shortlisted"],
    }
    return render_template("employer/dashboard.html", stats=stats, recent=recent, top=top, charts=charts)


# --------------------------------------------------------------- job CRUD
@bp.route("/jobs")
def jobs():
    jobs_list = db.session.scalars(
        select(Job).where(Job.employer_id == current_user.id)
        .options(selectinload(Job.applications)).order_by(Job.created_at.desc())
    ).all()
    return render_template("employer/jobs.html", jobs=jobs_list)


def _blank_job():
    return {"title": "", "description": "", "skills": "", "location": "", "job_type": "Full-time",
            "experience_level": "", "salary_range": "", "deadline": None, "status": JOB_OPEN}


def _job_form_context(job, data):
    return {"job": job, "data": data, "job_types": JOB_TYPES, "levels": EXPERIENCE_LEVELS, "ai_on": ai_enabled()}


@bp.route("/jobs/new", methods=["GET", "POST"])
def job_new():
    data = _blank_job()
    if request.method == "POST":
        data, errors = _parse_job_form(request.form)
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            job = Job(employer_id=current_user.id, **data)
            db.session.add(job)
            log_activity("job_created", data["title"])
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                log.exception("Failed to create job")
                flash("We could not save the job. Please try again.", "danger")
            else:
                flash("Job posted successfully.", "success")
                return redirect(url_for("employer.jobs"))
    return render_template("employer/job_form.html", **_job_form_context(None, data))


@bp.post("/jobs/ai-draft")
def job_ai_draft():
    if not ai_allowed(current_user.id):
        return jsonify(error="You have reached the hourly AI limit. Please try again later."), 429
    title = request.form.get("title", "").strip()
    if len(title) < 3:
        return jsonify(error="Enter a job title first."), 400
    result = draft_job(title, request.form.get("experience_level", ""), request.form.get("skills", ""))
    return jsonify(result)


@bp.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
def job_edit(job_id):
    job = _owned_job_or_404(job_id)
    data = {
        "title": job.title, "description": job.description, "skills": job.skills, "location": job.location or "",
        "job_type": job.job_type, "experience_level": job.experience_level or "",
        "salary_range": job.salary_range or "", "deadline": job.deadline, "status": job.status,
    }
    if request.method == "POST":
        data, errors = _parse_job_form(request.form, existing=job)
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            for key, value in data.items():
                setattr(job, key, value)
            rescored = 0
            if request.form.get("rescore") == "1":
                rescored = rescore_job_applications(job)
            log_activity("job_updated", data["title"])
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                log.exception("Failed to update job")
                flash("We could not update the job. Please try again.", "danger")
            else:
                flash(f"Job updated. {rescored} applicant(s) re-scored." if rescored
                      else "Job updated. Existing applicants keep their original scores.", "success")
                return redirect(url_for("employer.jobs"))
    return render_template("employer/job_form.html", **_job_form_context(job, data))


@bp.post("/jobs/<int:job_id>/toggle")
def job_toggle(job_id):
    job = _owned_job_or_404(job_id)
    job.status = JOB_CLOSED if job.status == JOB_OPEN else JOB_OPEN
    log_activity("job_" + job.status, job.title)
    try:
        db.session.commit()
        flash(f"Job is now {job.status}.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not change the job status.", "danger")
    return redirect(url_for("employer.jobs"))


@bp.post("/jobs/<int:job_id>/delete")
def job_delete(job_id):
    job = _owned_job_or_404(job_id)
    files = [a.resume_filename for a in job.applications]
    log_activity("job_deleted", job.title)
    db.session.delete(job)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        log.exception("Failed to delete job")
        flash("Could not delete the job.", "danger")
    else:
        delete_resume_files(files)
        flash("Job and its applications were deleted.", "success")
    return redirect(url_for("employer.jobs"))


@bp.post("/jobs/<int:job_id>/rescore")
def job_rescore(job_id):
    job = _owned_job_or_404(job_id)
    updated = rescore_job_applications(job)
    try:
        db.session.commit()
        flash(f"Re-scored {updated} applicant(s) against the current description and skills.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not re-score applicants.", "danger")
    return redirect(url_for("employer.applicants", job_id=job.id))


# --------------------------------------------------------------- applicants
@bp.route("/jobs/<int:job_id>/applicants")
def applicants(job_id):
    job = _owned_job_or_404(job_id)
    sort = request.args.get("sort", "score")
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()[:100]
    min_score = request.args.get("min_score", type=float)

    stmt = (
        select(Application).join(User, Application.applicant_id == User.id)
        .where(Application.job_id == job.id).options(joinedload(Application.applicant))
    )
    if status in APPLICATION_STATUSES:
        stmt = stmt.where(Application.status == status)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(User.full_name.ilike(like, escape="\\"), User.email.ilike(like, escape="\\")))
    if min_score is not None:
        stmt = stmt.where(Application.match_score >= max(0.0, min(min_score, 100.0)))
    if sort == "newest":
        stmt = stmt.order_by(Application.applied_at.desc())
    else:
        sort = "score"
        stmt = stmt.order_by(Application.match_score.desc(), Application.applied_at.asc())
    apps = db.session.scalars(stmt).all()

    all_apps = job.applications
    scores = [a.match_score for a in all_apps]
    gap_counts = Counter(skill for a in all_apps for skill in a.missing_for_job)
    summary = {
        "total": len(scores),
        "average": round(sum(scores) / len(scores), 1) if scores else 0,
        "top": max(scores) if scores else 0,
        "gaps": gap_counts.most_common(5),
    }
    return render_template(
        "employer/applicants.html", job=job, applications=apps, summary=summary, sort=sort, status=status,
        q=q, min_score=min_score, statuses=APPLICATION_STATUSES, distribution=score_distribution(scores),
        filtered=bool(status or q or min_score is not None),
    )


@bp.route("/applications/<int:application_id>")
def application_detail(application_id):
    application = _owned_application_or_404(application_id)
    text = load_resume_text(application)
    brief = None
    if application.ai_summary:
        try:
            brief = json.loads(application.ai_summary)
        except ValueError:
            brief = None
    return render_template(
        "employer/application_detail.html", application=application, job=application.job,
        signals=extract_profile_signals(text), preview=text[:2500], brief=brief,
        statuses=APPLICATION_STATUSES, ai_on=ai_enabled(),
    )


@bp.post("/applications/<int:application_id>/ai-summary")
def application_ai_summary(application_id):
    application = _owned_application_or_404(application_id)
    if not ai_allowed(current_user.id):
        flash("You have reached the hourly AI limit. Please try again later.", "warning")
    else:
        text = load_resume_text(application)
        if not text:
            flash("No readable resume text is available for this application.", "danger")
        else:
            application.ai_summary = json.dumps(candidate_brief(text, application.job))
            log_activity("ai_summary", f"application {application.id}")
            try:
                db.session.commit()
                flash("Candidate summary generated.", "success")
            except SQLAlchemyError:
                db.session.rollback()
                flash("Could not save the summary.", "danger")
    return redirect(url_for("employer.application_detail", application_id=application.id))


@bp.post("/applications/<int:application_id>/notes")
def application_notes(application_id):
    application = _owned_application_or_404(application_id)
    application.employer_notes = request.form.get("notes", "").strip()[:2000] or None
    try:
        db.session.commit()
        flash("Private notes saved.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not save notes.", "danger")
    return redirect(url_for("employer.application_detail", application_id=application.id))


@bp.post("/applications/<int:application_id>/status")
def application_status(application_id):
    application = _owned_application_or_404(application_id)
    new_status = request.form.get("status", "")
    if new_status not in APPLICATION_STATUSES:
        flash("Invalid status.", "danger")
    else:
        changed = application.status != new_status
        application.status = new_status
        if changed:
            notify(application.applicant_id,
                   f"Your application for “{application.job.title}” is now: {new_status}.",
                   url_for("applicant.job_detail", job_id=application.job_id))
        try:
            db.session.commit()
            flash(f"{application.applicant.full_name} marked as {new_status}.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the application status.", "danger")
    if request.form.get("return") == "detail":
        return redirect(url_for("employer.application_detail", application_id=application.id))
    return redirect(url_for("employer.applicants", job_id=application.job_id))


@bp.get("/applications/<int:application_id>/resume")
def download_resume(application_id):
    return send_resume(_owned_application_or_404(application_id))


@bp.get("/jobs/<int:job_id>/export.csv")
def export_csv(job_id):
    job = _owned_job_or_404(job_id)
    apps = db.session.scalars(
        select(Application).where(Application.job_id == job.id)
        .options(joinedload(Application.applicant))
        .order_by(Application.match_score.desc(), Application.applied_at.asc())
    ).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Rank", "Name", "Email", "Match %", "Skill coverage %", "Matched skills",
                     "Missing skills", "Status", "Applied"])
    for rank, a in enumerate(apps, 1):
        writer.writerow([rank, csv_safe(a.applicant.full_name), csv_safe(a.applicant.email), a.match_score,
                         a.skill_coverage if a.skill_coverage is not None else "",
                         csv_safe("; ".join(a.matched_for_job)), csv_safe("; ".join(a.missing_for_job)),
                         a.status, a.applied_at.strftime("%Y-%m-%d %H:%M")])
    slug = re.sub(r"[^a-z0-9]+", "-", job.title.lower()).strip("-") or "job"
    log_activity("export_csv", job.title)
    db.session.commit()
    return Response(
        "﻿" + buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{slug}-applicants.csv"'},
    )
