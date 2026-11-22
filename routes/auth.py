"""Registration, login and logout."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash

from extensions import db
from models import ROLE_APPLICANT, ROLE_EMPLOYER, User, utcnow
from utils import (
    clear_login_failures,
    dashboard_url_for,
    is_safe_redirect,
    log_activity,
    login_wait_seconds,
    minutes_ceil,
    notify,
    record_login_failure,
    validate_email,
    validate_password,
)

bp = Blueprint("auth", __name__)

# Public sign-up may only create these roles. Admins are created via the CLI.
PUBLIC_ROLES = (ROLE_APPLICANT, ROLE_EMPLOYER)
_DUMMY_HASH = "scrypt:32768:8:1$dummy$0000000000000000000000000000000000000000000000000000000000000000"


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))

    form = {"full_name": "", "email": "", "role": ROLE_APPLICANT, "company_name": ""}
    if request.method == "POST":
        form["full_name"] = request.form.get("full_name", "").strip()
        form["email"] = request.form.get("email", "").strip().lower()
        form["role"] = request.form.get("role", "")
        form["company_name"] = request.form.get("company_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        errors = []
        if not 2 <= len(form["full_name"]) <= 120:
            errors.append("Full name must be between 2 and 120 characters.")
        if not validate_email(form["email"]):
            errors.append("Please enter a valid email address.")
        if form["role"] not in PUBLIC_ROLES:  # blocks role=admin tampering
            errors.append("Please choose a valid account type.")
        if form["role"] == ROLE_EMPLOYER and len(form["company_name"]) > 150:
            errors.append("Company name must be 150 characters or fewer.")
        errors.extend(validate_password(password))
        if password != confirm:
            errors.append("Passwords do not match.")
        if not errors and db.session.scalar(select(User.id).where(User.email == form["email"])):
            errors.append("An account with this email already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            user = User(
                full_name=form["full_name"],
                email=form["email"],
                role=form["role"],
                company_name=form["company_name"] or None if form["role"] == ROLE_EMPLOYER else None,
            )
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.flush()
                notify(user.id, "Welcome to TalentMatch! Complete your profile to get started.",
                       url_for("account.profile"))
                log_activity("register", f"{user.role}: {user.email}", user_id=user.id)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("An account with this email already exists.", "danger")
            except SQLAlchemyError:
                db.session.rollback()
                flash("We could not create your account. Please try again.", "danger")
            else:
                flash("Account created successfully. Please sign in.", "success")
                return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))

    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        wait = login_wait_seconds(email)
        user = None if wait else db.session.scalar(select(User).where(User.email == email))

        if wait:
            flash(f"Too many failed attempts. Please try again in {minutes_ceil(wait)} minute(s).", "danger")
        elif user is None:
            check_password_hash(_DUMMY_HASH, password)  # keep timing similar
            _login_failed(email)
        elif not user.check_password(password):
            _login_failed(email)
        elif user.is_suspended:
            flash("This account has been suspended. Please contact the administrator.", "danger")
        else:
            next_url = request.args.get("next")
            clear_login_failures(email)
            session.clear()  # new session on login (prevents fixation)
            login_user(user)
            session.permanent = True
            user.last_login_at = utcnow()
            log_activity("login", user.email, user_id=user.id)
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
            flash(f"Welcome back, {user.full_name.split()[0]}!", "success")
            if is_safe_redirect(next_url):
                return redirect(next_url)
            return redirect(dashboard_url_for(user))

    return render_template("auth/login.html", email=email)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_activity("logout", current_user.email)
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


def _login_failed(email):
    record_login_failure(email)
    log_activity("login_failed", email[:100], user_id=None)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
    flash("Invalid email or password.", "danger")
