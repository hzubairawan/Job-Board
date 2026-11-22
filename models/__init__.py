"""SQLAlchemy models: User, Job, Application, SavedJob, Notification, ActivityLog."""
import sqlite3
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, and_, event, or_
from sqlalchemy.engine import Engine
from sqlalchemy.orm import deferred
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db

ROLE_APPLICANT = "applicant"
ROLE_EMPLOYER = "employer"
ROLE_ADMIN = "admin"
ROLES = (ROLE_APPLICANT, ROLE_EMPLOYER, ROLE_ADMIN)

JOB_OPEN = "open"
JOB_CLOSED = "closed"
JOB_STATUSES = (JOB_OPEN, JOB_CLOSED)
JOB_TYPES = ("Full-time", "Part-time", "Contract", "Internship", "Remote")
EXPERIENCE_LEVELS = ("Entry level", "Mid level", "Senior level")

APPLICATION_STATUSES = ("pending", "reviewed", "shortlisted", "rejected")


def utcnow():
    """Naive UTC timestamp (SQLite stores naive datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('applicant','employer','admin')", name="ck_users_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_APPLICANT, index=True)
    company_name = db.Column(db.String(150))
    is_suspended = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    jobs = db.relationship(
        "Job", back_populates="employer", cascade="all, delete-orphan", passive_deletes=True
    )
    applications = db.relationship(
        "Application", back_populates="applicant", cascade="all, delete-orphan", passive_deletes=True
    )
    saved_jobs = db.relationship(
        "SavedJob", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    notifications = db.relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    activity_logs = db.relationship("ActivityLog", back_populates="user", passive_deletes=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):  # Flask-Login: suspended accounts cannot stay logged in
        return not self.is_suspended

    @property
    def display_company(self):
        return self.company_name or self.full_name

    @property
    def initials(self):
        parts = [p for p in (self.full_name or "?").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def __repr__(self):
        return f"<User {self.id} {self.role}>"


class Job(db.Model):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('open','closed')", name="ck_jobs_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    employer_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = db.Column(db.String(150), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    skills = db.Column(db.Text, nullable=False)  # comma-separated
    location = db.Column(db.String(100))
    job_type = db.Column(db.String(30), nullable=False, default="Full-time")
    salary_range = db.Column(db.String(60))
    experience_level = db.Column(db.String(30))
    deadline = db.Column(db.Date)
    source = db.Column(db.String(30), nullable=False, default="internal")  # internal / remotive / arbeitnow
    external_url = db.Column(db.String(500), index=True)
    company_label = db.Column(db.String(150))  # company name shown for imported / demo jobs
    status = db.Column(db.String(20), nullable=False, default=JOB_OPEN, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    employer = db.relationship("User", back_populates="jobs")
    applications = db.relationship(
        "Application", back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )
    saved_by = db.relationship(
        "SavedJob", back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def skill_list(self):
        return [s.strip() for s in (self.skills or "").split(",") if s.strip()]

    @property
    def company(self):
        return self.company_label or self.employer.display_company

    @property
    def is_external(self):
        return bool(self.external_url)

    @property
    def source_label(self):
        return {"remotive": "Remotive", "arbeitnow": "Arbeitnow"}.get(self.source, "")

    @property
    def is_expired(self):
        return self.deadline is not None and self.deadline < utcnow().date()

    @property
    def is_open(self):
        return self.status == JOB_OPEN and not self.is_expired

    @property
    def display_status(self):
        if self.status == JOB_OPEN and self.is_expired:
            return "expired"
        return self.status

    @property
    def days_left(self):
        if self.deadline is None:
            return None
        return (self.deadline - utcnow().date()).days

    @property
    def applicant_count(self):
        return len(self.applications)

    def __repr__(self):
        return f"<Job {self.id} {self.title!r}>"


def open_jobs_clause():
    """SQL condition for jobs that currently accept applications."""
    return and_(Job.status == JOB_OPEN, or_(Job.deadline.is_(None), Job.deadline >= utcnow().date()))


class Application(db.Model):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("job_id", "applicant_id", name="uq_application_job_applicant"),
        CheckConstraint(
            "status IN ('pending','reviewed','shortlisted','rejected')",
            name="ck_applications_status",
        ),
        Index("ix_application_job_score", "job_id", "match_score"),
    )

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(
        db.Integer, db.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    applicant_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_filename = db.Column(db.String(64), nullable=False)  # random server-side name
    original_filename = db.Column(db.String(255), nullable=False)
    resume_text = deferred(db.Column(db.Text))  # extracted text, enables re-scoring & recommendations
    match_score = db.Column(db.Float, nullable=False, default=0.0)
    matched_skills = db.Column(db.Text, nullable=False, default="")
    employer_notes = db.Column(db.Text)
    ai_summary = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    applied_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    job = db.relationship("Job", back_populates="applications")
    applicant = db.relationship("User", back_populates="applications")

    @property
    def matched_skill_list(self):
        return [s.strip() for s in (self.matched_skills or "").split(",") if s.strip()]

    @property
    def matched_for_job(self):
        """Required skills (as currently defined on the job) found in the resume."""
        found = {s.lower() for s in self.matched_skill_list}
        return [s for s in self.job.skill_list if s.lower() in found]

    @property
    def missing_for_job(self):
        found = {s.lower() for s in self.matched_skill_list}
        return [s for s in self.job.skill_list if s.lower() not in found]

    @property
    def skill_coverage(self):
        required = len(self.job.skill_list)
        return round(len(self.matched_for_job) / required * 100) if required else None

    def __repr__(self):
        return f"<Application {self.id} job={self.job_id} user={self.applicant_id}>"


class SavedJob(db.Model):
    __tablename__ = "saved_jobs"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_saved_user_job"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User", back_populates="saved_jobs")
    job = db.relationship("Job", back_populates="saved_by")


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notification_user_read", "user_id", "is_read"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User", back_populates="notifications")


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    detail = db.Column(db.String(255), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User", back_populates="activity_logs")
