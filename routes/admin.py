"""Administrator features: platform stats, user management, job moderation."""
import logging
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from extensions import db
from models import (
    ActivityLog,
    JOB_CLOSED,
    JOB_OPEN,
    ROLE_ADMIN,
    ROLE_APPLICANT,
    ROLE_EMPLOYER,
    ROLES,
    Application,
    Job,
    User,
    utcnow,
)
from services.job_feeds import FEED_EMAIL, SOURCES, import_jobs
from services.storage import delete_resume_files
from utils import escape_like, log_activity, notify, query_args_without_page, role_guard

log = logging.getLogger(__name__)
bp = Blueprint("admin", __name__, url_prefix="/admin")
bp.before_request(role_guard(ROLE_ADMIN))


def _count(model, *conds):
    return db.session.scalar(select(func.count()).select_from(model).where(*conds)) or 0


@bp.route("/dashboard")
def dashboard():
    stats = {
        "users": _count(User, User.role != ROLE_ADMIN, User.email != FEED_EMAIL),
        "employers": _count(User, User.role == ROLE_EMPLOYER, User.email != FEED_EMAIL),
        "applicants": _count(User, User.role == ROLE_APPLICANT),
        "suspended": _count(User, User.is_suspended.is_(True)),
        "jobs": _count(Job),
        "external_jobs": _count(Job, Job.external_url.is_not(None)),
        "open_jobs": _count(Job, Job.status == JOB_OPEN),
        "closed_jobs": _count(Job, Job.status == JOB_CLOSED),
        "resumes": _count(Application),
    }
    avg = db.session.scalar(select(func.avg(Application.match_score)))
    stats["avg_score"] = round(avg or 0, 1)

    today = utcnow().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    since = utcnow() - timedelta(days=7)
    per_day = {d: 0 for d in days}
    for ts in db.session.scalars(select(Application.applied_at).where(Application.applied_at >= since)):
        if ts.date() in per_day:
            per_day[ts.date()] += 1

    charts = {
        "roles": {
            "type": "doughnut",
            "labels": ["Applicants", "Employers"],
            "data": [stats["applicants"], stats["employers"]],
        },
        "jobs": {
            "type": "doughnut",
            "labels": ["Open", "Closed"],
            "data": [stats["open_jobs"], stats["closed_jobs"]],
        },
        "weekly": {
            "type": "bar",
            "label": "Resumes processed",
            "labels": [d.strftime("%d %b") for d in days],
            "data": list(per_day.values()),
        },
    }

    # Merged activity feed
    events = []
    for u in db.session.scalars(select(User).where(User.role != ROLE_ADMIN, User.email != FEED_EMAIL).order_by(User.created_at.desc()).limit(6)):
        events.append({"icon": "person-plus", "when": u.created_at,
                       "text": f"{u.full_name} registered as {u.role}"})
    for j in db.session.scalars(
        select(Job).options(joinedload(Job.employer)).order_by(Job.created_at.desc()).limit(6)
    ):
        events.append({"icon": "briefcase", "when": j.created_at,
                       "text": f"{j.company} posted “{j.title}”"})
    for a in db.session.scalars(
        select(Application)
        .options(joinedload(Application.applicant), joinedload(Application.job))
        .order_by(Application.applied_at.desc())
        .limit(6)
    ):
        events.append({"icon": "file-earmark-text", "when": a.applied_at,
                       "text": f"{a.applicant.full_name} applied to “{a.job.title}” ({a.match_score:.1f}% match)"})
    events.sort(key=lambda e: e["when"], reverse=True)

    return render_template("admin/dashboard.html", stats=stats, charts=charts, events=events[:10])


@bp.route("/users")
def users():
    q = request.args.get("q", "").strip()[:100]
    role = request.args.get("role", "")
    page = request.args.get("page", 1, type=int)

    stmt = select(User).where(User.email != FEED_EMAIL)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(User.full_name.ilike(like, escape="\\"), User.email.ilike(like, escape="\\")))
    if role in ROLES:
        stmt = stmt.where(User.role == role)
    stmt = stmt.order_by(User.created_at.desc())
    pagination = db.paginate(stmt, page=page, per_page=current_app.config["ADMIN_PER_PAGE"], error_out=False)
    return render_template(
        "admin/users.html",
        pagination=pagination,
        users=pagination.items,
        q=q,
        role=role,
        qargs=query_args_without_page(),
    )


def _manageable_user_or_flash(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id or user.role == ROLE_ADMIN:
        flash("Administrator accounts cannot be suspended or deleted here.", "warning")
        return None
    return user


@bp.post("/users/<int:user_id>/suspend")
def user_suspend(user_id):
    user = _manageable_user_or_flash(user_id)
    if user:
        user.is_suspended = not user.is_suspended
        log_activity("user_suspended" if user.is_suspended else "user_reinstated", user.email)
        try:
            db.session.commit()
            flash(f"{user.full_name} has been {'suspended' if user.is_suspended else 'reinstated'}.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the user.", "danger")
    return redirect(request.referrer if _same_site(request.referrer) else url_for("admin.users"))


@bp.post("/users/<int:user_id>/delete")
def user_delete(user_id):
    user = _manageable_user_or_flash(user_id)
    if user:
        files = [a.resume_filename for a in user.applications]
        for job in user.jobs:
            files.extend(a.resume_filename for a in job.applications)
        name = user.full_name
        log_activity("user_deleted", user.email)
        db.session.delete(user)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            log.exception("Failed to delete user")
            flash("Could not delete the user.", "danger")
        else:
            delete_resume_files(files)
            flash(f"{name} and all related data were deleted.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/jobs")
def jobs():
    q = request.args.get("q", "").strip()[:100]
    status = request.args.get("status", "")
    page = request.args.get("page", 1, type=int)

    stmt = select(Job).options(joinedload(Job.employer), selectinload(Job.applications))
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Job.title.ilike(like, escape="\\"), Job.description.ilike(like, escape="\\")))
    if status in (JOB_OPEN, JOB_CLOSED):
        stmt = stmt.where(Job.status == status)
    source = request.args.get("source", "")
    if source == "external":
        stmt = stmt.where(Job.external_url.is_not(None))
    elif source == "internal":
        stmt = stmt.where(Job.external_url.is_(None))
    stmt = stmt.order_by(Job.created_at.desc())
    pagination = db.paginate(stmt, page=page, per_page=current_app.config["ADMIN_PER_PAGE"], error_out=False)
    return render_template(
        "admin/jobs.html",
        pagination=pagination,
        jobs=pagination.items,
        q=q,
        status=status,
        source=source,
        qargs=query_args_without_page(),
    )


@bp.post("/jobs/import")
def jobs_import():
    source = request.form.get("source", "all")
    limit = request.form.get("limit", 50, type=int)
    limit = max(1, min(limit or 50, 200))
    if source != "all" and source not in SOURCES:
        flash("Unknown job source.", "danger")
        return redirect(url_for("admin.jobs"))
    try:
        result = import_jobs(source, limit)
    except SQLAlchemyError:
        db.session.rollback()
        log.exception("Job import failed")
        flash("The import failed while saving jobs.", "danger")
        return redirect(url_for("admin.jobs"))
    log_activity("jobs_imported", f"{source}: {result['created']} new, {result['skipped']} skipped")
    db.session.commit()
    if result["created"]:
        flash(f"Imported {result['created']} new job(s) ({result['skipped']} skipped as duplicates or incomplete).", "success")
    elif not result["errors"]:
        flash("No new jobs were found: everything available is already imported.", "info")
    for error in result["errors"]:
        flash(f"Import problem: {error}", "warning")
    return redirect(url_for("admin.jobs"))


@bp.post("/jobs/<int:job_id>/delete")
def job_delete(job_id):
    job = db.get_or_404(Job, job_id)
    files = [a.resume_filename for a in job.applications]
    title = job.title
    if job.employer.email != FEED_EMAIL:
        notify(job.employer_id, f"Your job “{title}” was removed by an administrator.")
    log_activity("job_removed", title)
    db.session.delete(job)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        log.exception("Failed to delete job")
        flash("Could not delete the job.", "danger")
    else:
        delete_resume_files(files)
        flash(f"Job “{title}” was removed.", "success")
    return redirect(url_for("admin.jobs"))


def _same_site(url):
    from utils import is_safe_redirect

    return is_safe_redirect(url)


@bp.route("/activity")
def activity():
    action = request.args.get("action", "").strip()[:50]
    page = request.args.get("page", 1, type=int)
    stmt = select(ActivityLog).options(joinedload(ActivityLog.user))
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    stmt = stmt.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
    pagination = db.paginate(stmt, page=page, per_page=20, error_out=False)
    actions = db.session.scalars(select(ActivityLog.action).distinct().order_by(ActivityLog.action)).all()
    return render_template(
        "admin/activity.html", pagination=pagination, logs=pagination.items, actions=actions,
        action=action, qargs=query_args_without_page(),
    )
