"""Shared helpers: role guards, validation, notifications, audit log, login throttling."""
import math
import re
import threading
import time
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

from flask import abort, current_app, request, url_for
from flask_login import current_user

from extensions import db
from models import ROLE_ADMIN, ROLE_APPLICANT, ROLE_EMPLOYER, ActivityLog, Notification

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def dashboard_url_for(user):
    if not user or not user.is_authenticated:
        return url_for("main.index")
    return {
        ROLE_APPLICANT: url_for("applicant.dashboard"),
        ROLE_EMPLOYER: url_for("employer.dashboard"),
        ROLE_ADMIN: url_for("admin.dashboard"),
    }.get(user.role, url_for("main.index"))


def role_guard(*roles):
    """Return a before_request function that enforces login + role membership."""

    def guard():
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if current_user.role not in roles:
            abort(403)

    return guard


def is_safe_redirect(target):
    if not target:
        return False
    host = urlparse(request.host_url)
    dest = urlparse(urljoin(request.host_url, target))
    return dest.scheme in ("http", "https") and host.netloc == dest.netloc


def validate_email(email):
    return bool(email) and len(email) <= 255 and bool(EMAIL_RE.match(email))


def validate_password(password):
    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    if len(password) > 128:
        errors.append("Password must be 128 characters or fewer.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        errors.append("Password must contain at least one letter and one number.")
    return errors


def escape_like(value):
    """Escape SQL LIKE wildcards so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def query_args_without_page():
    """Current non-empty query args minus 'page', for building pagination links."""
    return {k: v for k, v in request.args.items() if k != "page" and v}


def parse_date(value):
    """Parse YYYY-MM-DD into a date, or None."""
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def csv_safe(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


# --------------------------------------------------- notifications & audit log
def notify(user_id, message, link=None):
    """Queue an in-app notification. The caller commits."""
    db.session.add(Notification(user_id=user_id, message=message[:255], link=link))


def log_activity(action, detail="", user_id=None):
    """Queue an audit-log entry. The caller commits."""
    if user_id is None and current_user and current_user.is_authenticated:
        user_id = current_user.id
    db.session.add(ActivityLog(user_id=user_id, action=action[:50], detail=(detail or "")[:255]))


# ------------------------------------------------------------- login throttling
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_failures = {}
_failures_lock = threading.Lock()


def _throttle_key(email):
    return f"{request.remote_addr}|{(email or '').lower()}"


def login_wait_seconds(email):
    """Seconds the caller must wait before another login attempt (0 = allowed)."""
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(_throttle_key(email), []) if now - t < LOGIN_WINDOW_SECONDS]
        if recent:
            _failures[_throttle_key(email)] = recent
        else:
            _failures.pop(_throttle_key(email), None)
        if len(recent) >= LOGIN_MAX_FAILURES:
            return max(1, int(LOGIN_WINDOW_SECONDS - (now - recent[0])))
    return 0


def record_login_failure(email):
    with _failures_lock:
        _failures.setdefault(_throttle_key(email), []).append(time.time())


def clear_login_failures(email):
    with _failures_lock:
        _failures.pop(_throttle_key(email), None)


def reset_login_throttle():
    with _failures_lock:
        _failures.clear()


def minutes_ceil(seconds):
    return max(1, math.ceil(seconds / 60))
