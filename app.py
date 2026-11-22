"""Application factory, security hooks, error pages and CLI commands."""
import hmac
import logging
import os
import secrets

import click
from flask import Flask, abort, render_template, request, session
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from config import get_config
from extensions import db, login_manager

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

ERROR_INFO = {
    400: ("Request could not be verified", "Something about that request was invalid or your session expired."),
    403: ("Access denied", "You do not have permission to view this page."),
    404: ("Page not found", "The page you are looking for does not exist or has been moved."),
    405: ("Method not allowed", "That action is not allowed on this page."),
    413: ("File too large", "The uploaded file exceeds the maximum allowed size."),
    500: ("Something went wrong", "An unexpected error occurred. Our team has been notified."),
}



def create_app(config_object=None):
    # Vercel writable temporary directory
    instance_path = "/tmp/smart_resume_instance"
    os.makedirs(instance_path, exist_ok=True)

    # Initialize Flask application
    app = Flask(
        __name__,
        instance_path=instance_path,
        instance_relative_config=True
    )

    # Load configuration
    app.config.from_object(config_object or get_config())

    # Configure secret key
    if not app.config.get("SECRET_KEY"):
        if os.environ.get("APP_ENV") == "production":
            raise RuntimeError(
                "SECRET_KEY must be set in production."
            )
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        app.logger.warning(
            "No SECRET_KEY set - using a temporary key."
        )

    # Configure upload directory
    app.config["UPLOAD_FOLDER"] = os.path.join(
        instance_path, "uploads"
    )
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    logging.basicConfig(level=logging.INFO)

    # Initialize database

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "basic"

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            user = db.session.get(User, int(user_id))
            return None if user is None or user.is_suspended else user
        except (ValueError, SQLAlchemyError):
            return None

    from routes import account, admin, applicant, auth, employer, main

    for module in (main, auth, account, applicant, employer, admin):
        app.register_blueprint(module.bp)

    register_security(app)
    register_template_helpers(app)
    register_error_handlers(app)
    register_cli(app)

    with app.app_context():
        from services.schema import ensure_schema

        ensure_schema(db)
    return app


# ----------------------------------------------------------------- security
def register_security(app):
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def csrf_protect():
        if not app.config.get("CSRF_ENABLED", True):
            return
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
            expected = session.get("_csrf", "")
            if not expected or not sent or not hmac.compare_digest(sent, expected):
                abort(400)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if current_user.is_authenticated and not request.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"  # no private pages via Back button after logout
        return response


# ------------------------------------------------------------ template aids
def register_template_helpers(app):
    from models import JOB_TYPES
    from utils import dashboard_url_for

    @app.template_filter("score_band")
    def score_band(score):
        score = score or 0
        if score >= 35:
            return {"cls": "high", "label": "Strong"}
        if score >= 15:
            return {"cls": "mid", "label": "Moderate"}
        return {"cls": "low", "label": "Low"}

    @app.template_filter("dt")
    def fmt_date(value):
        return value.strftime("%d %b %Y") if value else "-"

    @app.template_filter("ago")
    def time_ago(value):
        from models import utcnow

        if not value:
            return "-"
        seconds = int((utcnow() - value).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        if seconds < 86400 * 30:
            return f"{seconds // 86400}d ago"
        return value.strftime("%d %b %Y")

    app.jinja_env.globals["dashboard_url_for"] = dashboard_url_for
    app.jinja_env.globals["JOB_TYPES"] = JOB_TYPES

    @app.context_processor
    def inject_notifications():
        from sqlalchemy import func

        from models import Notification

        empty = {"unread_count": 0, "recent_notifications": []}
        if not current_user.is_authenticated:
            return empty
        try:
            unread = db.session.scalar(
                select(func.count()).select_from(Notification)
                .where(Notification.user_id == current_user.id, Notification.is_read.is_(False))
            ) or 0
            recent = db.session.scalars(
                select(Notification).where(Notification.user_id == current_user.id)
                .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(5)
            ).all()
        except SQLAlchemyError:
            db.session.rollback()
            return empty
        return {"unread_count": unread, "recent_notifications": recent}


# ----------------------------------------------------------- error handling
def register_error_handlers(app):
    def render_error(code, message=None):
        title, default_message = ERROR_INFO.get(code, ("Error", "An error occurred."))
        return render_template("errors/error.html", code=code, title=title, message=message or default_message), code

    @app.errorhandler(HTTPException)
    def handle_http(exc):
        return render_error(exc.code or 500)

    @app.errorhandler(SQLAlchemyError)
    def handle_db(exc):
        db.session.rollback()
        app.logger.exception("Database error")
        return render_error(500)

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        db.session.rollback()
        app.logger.exception("Unhandled exception")
        return render_error(500)


# --------------------------------------------------------------------- CLI
def register_cli(app):
    from models import ROLE_ADMIN, ROLE_APPLICANT, ROLE_EMPLOYER, Job, User

    @app.cli.command("init-db")
    def init_db():
        """Create all database tables."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("create-admin")
    @click.option("--name", default=lambda: os.environ.get("ADMIN_NAME", "Platform Administrator"))
    @click.option("--email", default=lambda: os.environ.get("ADMIN_EMAIL"))
    @click.option("--password", default=lambda: os.environ.get("ADMIN_PASSWORD"), hide_input=True)
    def create_admin(name, email, password):
        """Create (or promote) an administrator. Admins cannot self-register."""
        from utils import validate_email, validate_password

        email = (email or click.prompt("Admin email")).strip().lower()
        if not validate_email(email):
            raise click.ClickException("Invalid email address.")
        password = password or click.prompt("Admin password", hide_input=True, confirmation_prompt=True)
        problems = validate_password(password)
        if problems:
            raise click.ClickException(" ".join(problems))
        user = db.session.scalar(select(User).where(User.email == email))
        if user:
            user.role = ROLE_ADMIN
            user.is_suspended = False
            user.set_password(password)
            click.echo(f"Existing user {email} promoted to administrator.")
        else:
            user = User(full_name=name, email=email, role=ROLE_ADMIN)
            user.set_password(password)
            db.session.add(user)
            click.echo(f"Administrator {email} created.")
        db.session.commit()

    @app.cli.command("seed-demo")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True,
                  help="Password for the demo employer and applicant accounts.")
    @click.option("--jobs", default=60, show_default=True, help="How many generated demo jobs to create.")
    def seed_demo(password, jobs):
        """Create demo accounts plus many realistic sample jobs (works offline)."""
        from services.job_generator import generate_demo_jobs

        employer = db.session.scalar(select(User).where(User.email == "employer@demo.test"))
        if employer is None:
            employer = User(full_name="Demo Employer", email="employer@demo.test",
                            role=ROLE_EMPLOYER, company_name="Northwind Tech")
            employer.set_password(password)
            db.session.add(employer)
        if db.session.scalar(select(User.id).where(User.email == "applicant@demo.test")) is None:
            applicant = User(full_name="Demo Applicant", email="applicant@demo.test", role=ROLE_APPLICANT)
            applicant.set_password(password)
            db.session.add(applicant)
        db.session.flush()
        existing = {(t, c) for t, c in db.session.execute(
            select(Job.title, Job.company_label).where(Job.employer_id == employer.id))}
        created = 0
        for job in generate_demo_jobs(max(0, jobs), employer.id):
            if (job.title, job.company_label) not in existing:
                db.session.add(job)
                existing.add((job.title, job.company_label))
                created += 1
        db.session.commit()
        click.echo(f"Demo data ready: employer@demo.test and applicant@demo.test; {created} new demo jobs.")

    @app.cli.command("import-jobs")
    @click.option("--source", type=click.Choice(["all", "remotive", "arbeitnow"]), default="all", show_default=True)
    @click.option("--limit", default=100, show_default=True, help="Maximum jobs to fetch per source.")
    def import_jobs_command(source, limit):
        """Import real job listings from free public job-board APIs (needs internet)."""
        from services.job_feeds import import_jobs

        result = import_jobs(source, limit)
        click.echo(f"Imported {result['created']} new jobs ({result['skipped']} skipped).")
        for error in result["errors"]:
            click.echo(f"Problem: {error}", err=True)


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("APP_ENV", "development") == "development")
