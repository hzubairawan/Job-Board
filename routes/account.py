"""Account features shared by every role: profile, password change, notifications."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import ROLE_EMPLOYER, ROLES, Notification
from utils import log_activity, role_guard, validate_password

bp = Blueprint("account", __name__, url_prefix="/account")
bp.before_request(role_guard(*ROLES))


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        company = request.form.get("company_name", "").strip()
        errors = []
        if not 2 <= len(name) <= 120:
            errors.append("Full name must be between 2 and 120 characters.")
        if len(company) > 150:
            errors.append("Company name must be 150 characters or fewer.")
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            current_user.full_name = name
            if current_user.role == ROLE_EMPLOYER:
                current_user.company_name = company or None
            try:
                db.session.commit()
                flash("Profile updated.", "success")
            except SQLAlchemyError:
                db.session.rollback()
                flash("Could not update your profile.", "danger")
        return redirect(url_for("account.profile"))
    return render_template("account/profile.html")


@bp.post("/password")
def change_password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    errors = []
    if not current_user.check_password(current):
        errors.append("Your current password is incorrect.")
    errors.extend(validate_password(new))
    if new != confirm:
        errors.append("The new passwords do not match.")
    if not errors and current == new:
        errors.append("Choose a password different from your current one.")
    if errors:
        for message in errors:
            flash(message, "danger")
    else:
        current_user.set_password(new)
        log_activity("password_changed", current_user.email)
        try:
            db.session.commit()
            flash("Password changed successfully.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not change your password.", "danger")
    return redirect(url_for("account.profile"))


@bp.route("/notifications")
def notifications():
    page = request.args.get("page", 1, type=int)
    stmt = (
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    pagination = db.paginate(stmt, page=page, per_page=15, error_out=False)
    return render_template("account/notifications.html", pagination=pagination, items=pagination.items)


@bp.post("/notifications/read-all")
def notifications_read_all():
    db.session.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.session.commit()
    return redirect(request.referrer if _internal(request.referrer) else url_for("account.notifications"))


@bp.post("/notifications/<int:notification_id>/open")
def notification_open(notification_id):
    note = db.get_or_404(Notification, notification_id)
    if note.user_id != current_user.id:
        return redirect(url_for("account.notifications"))
    note.is_read = True
    db.session.commit()
    link = note.link or ""
    if link.startswith("/") and not link.startswith("//"):
        return redirect(link)
    return redirect(url_for("account.notifications"))


def _internal(url):
    from utils import is_safe_redirect

    return is_safe_redirect(url)
