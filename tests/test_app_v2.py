"""Web-layer tests for the v2 features (skipped automatically if Flask extensions are missing)."""
import os
import sqlite3
from datetime import timedelta

import pytest

from tests.conftest import login, post, upload
from tests.helpers import CHEF_RESUME, JOB_DESCRIPTION, PYTHON_RESUME, make_pdf


def scalar(app, stmt):
    from extensions import db

    with app.app_context():
        return db.session.scalar(stmt)


def scalars(app, stmt):
    from extensions import db

    with app.app_context():
        return list(db.session.scalars(stmt))


def make_job(app, employer_id, **kw):
    from extensions import db
    from models import Job

    data = dict(employer_id=employer_id, title="Flask API Engineer", description=JOB_DESCRIPTION,
                skills="Python, Flask, SQL, REST", location="Karachi", job_type="Full-time")
    data.update(kw)
    with app.app_context():
        j = Job(**data)
        db.session.add(j)
        db.session.commit()
        return j.id


def apply_as(client, user, job_id, resume=PYTHON_RESUME, name="cv.pdf"):
    login(client, user["email"])
    r = upload(client, f"/applicant/jobs/{job_id}/apply", make_pdf(resume), name)
    return r


# --------------------------------------------------------------- notifications
def test_notifications_for_apply_and_status_change(client, job, make_user, app):
    from models import Application, Notification
    from sqlalchemy import select

    cand = make_user("applicant", "n1@example.com", name="Nadia Notify")
    apply_as(client, cand, job["id"])
    post(client, "/logout")

    login(client, job["employer"]["email"])
    notes = scalars(app, select(Notification).where(Notification.user_id == job["employer"]["id"]))
    assert any("Nadia Notify applied" in n.message for n in notes)
    assert b"Nadia Notify applied" in client.get("/employer/dashboard").data  # shown in the bell menu

    note = next(n for n in notes if "applied" in n.message)
    r = post(client, f"/account/notifications/{note.id}/open")
    assert r.status_code == 302 and "/employer/jobs/" in r.headers["Location"]
    assert scalar(app, select(Notification.is_read).where(Notification.id == note.id)) is True

    app_id = scalar(app, select(Application.id))
    post(client, f"/employer/applications/{app_id}/status", {"status": "shortlisted"})
    post(client, "/logout")

    login(client, cand["email"])
    page = client.get("/account/notifications").data
    assert b"shortlisted" in page
    r = post(client, "/account/notifications/read-all")
    assert r.status_code == 302
    unread = scalars(app, select(Notification).where(Notification.user_id == cand["id"], Notification.is_read.is_(False)))
    assert unread == []


def test_cannot_open_someone_elses_notification(client, job, make_user, app):
    from models import Notification
    from sqlalchemy import select

    cand = make_user("applicant", "n2@example.com")
    apply_as(client, cand, job["id"])
    post(client, "/logout")
    note_id = scalar(app, select(Notification.id).where(Notification.user_id == job["employer"]["id"]))
    login(client, cand["email"])
    post(client, f"/account/notifications/{note_id}/open")
    assert scalar(app, select(Notification.is_read).where(Notification.id == note_id)) is False


# ------------------------------------------------------------- saved jobs / withdraw
def test_save_and_unsave_job(client, job, make_user, app):
    from models import SavedJob
    from sqlalchemy import func, select

    cand = make_user("applicant", "s1@example.com")
    login(client, cand["email"])
    post(client, f"/applicant/jobs/{job['id']}/save")
    assert scalar(app, select(func.count()).select_from(SavedJob)) == 1
    assert b"Python Backend Developer" in client.get("/applicant/saved").data
    post(client, f"/applicant/jobs/{job['id']}/save")
    assert scalar(app, select(func.count()).select_from(SavedJob)) == 0
    assert b"No saved jobs yet" in client.get("/applicant/saved").data


def test_withdraw_application_removes_file_and_allows_reapply(client, job, make_user, app):
    from models import Application
    from sqlalchemy import func, select

    cand = make_user("applicant", "w1@example.com")
    apply_as(client, cand, job["id"])
    app_id = scalar(app, select(Application.id))
    assert len(os.listdir(app.config["UPLOAD_FOLDER"])) == 1

    other = make_user("applicant", "w2@example.com")
    post(client, "/logout")
    login(client, other["email"])
    assert post(client, f"/applicant/applications/{app_id}/withdraw").status_code == 404
    post(client, "/logout")

    login(client, cand["email"])
    post(client, f"/applicant/applications/{app_id}/withdraw")
    assert scalar(app, select(func.count()).select_from(Application)) == 0
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []
    upload(client, f"/applicant/jobs/{job['id']}/apply", make_pdf(PYTHON_RESUME))
    assert scalar(app, select(func.count()).select_from(Application)) == 1


# --------------------------------------------------------------- account security
def test_profile_update_and_password_change(client, make_user, app):
    from models import User
    from sqlalchemy import select

    user = make_user("applicant", "p1@example.com", name="Old Name")
    login(client, user["email"])
    post(client, "/account/profile", {"full_name": "New Name"})
    assert scalar(app, select(User.full_name).where(User.id == user["id"])) == "New Name"
    assert b"between 2 and 120" in post(client, "/account/profile", {"full_name": "x"}, follow_redirects=True).data

    post(client, "/account/password", {"current_password": "wrong", "new_password": "Brand123new", "confirm_password": "Brand123new"})
    post(client, "/logout")
    assert "/applicant/dashboard" in login(client, user["email"]).headers["Location"]  # old password still valid

    post(client, "/account/password", {"current_password": user["password"], "new_password": "Brand123new", "confirm_password": "Brand123new"})
    post(client, "/logout")
    assert b"Invalid email or password" in login(client, user["email"]).data
    assert "/applicant/dashboard" in login(client, user["email"], "Brand123new").headers["Location"]


def test_login_throttling_after_repeated_failures(client, make_user):
    user = make_user("applicant", "t1@example.com")
    for _ in range(5):
        assert b"Invalid email or password" in login(client, user["email"], "bad-password1").data
    blocked = login(client, user["email"])  # even the correct password is refused now
    assert b"Too many failed attempts" in blocked.data
    assert client.get("/applicant/dashboard").status_code == 302


def test_suspended_user_loses_existing_session(client, make_user):
    admin = make_user("admin", "root@example.com")
    victim = make_user("applicant", "v1@example.com")
    login(client, victim["email"])
    assert client.get("/applicant/dashboard").status_code == 200

    admin_client = client.application.test_client()
    login(admin_client, admin["email"])
    post(admin_client, f"/admin/users/{victim['id']}/suspend")
    assert client.get("/applicant/dashboard").status_code == 302  # kicked to login


# ------------------------------------------------------------------ job fields
def test_job_deadline_validation_and_expiry(client, make_user, app):
    from models import Job
    from sqlalchemy import select
    from models import utcnow

    emp = make_user("employer", "d1@example.com")
    login(client, emp["email"])
    today = utcnow().date()
    form = {"title": "Deadline Job", "description": JOB_DESCRIPTION, "skills": "Python", "job_type": "Full-time",
            "status": "open", "experience_level": "Mid level", "salary_range": "PKR 100k"}
    assert b"cannot be in the past" in post(client, "/employer/jobs/new", {**form, "deadline": str(today - timedelta(days=1))}).data
    assert b"valid date" in post(client, "/employer/jobs/new", {**form, "deadline": "31/12/2030"}).data
    assert post(client, "/employer/jobs/new", {**form, "deadline": str(today + timedelta(days=5))}).status_code == 302
    job = scalar(app, select(Job).where(Job.title == "Deadline Job"))
    assert job.deadline == today + timedelta(days=5) and job.experience_level == "Mid level" and job.salary_range == "PKR 100k"


def test_expired_job_hidden_and_not_applyable(client, job, make_user, app):
    from models import Application, utcnow
    from sqlalchemy import func, select

    old = make_job(app, job["employer"]["id"], title="Expired Role", deadline=utcnow().date() - timedelta(days=2))
    cand = make_user("applicant", "e1@example.com")
    login(client, cand["email"])
    assert b"Expired Role" not in client.get("/applicant/dashboard").data
    assert client.get(f"/applicant/jobs/{old}").status_code == 404
    upload(client, f"/applicant/jobs/{old}/apply", make_pdf(PYTHON_RESUME))
    assert scalar(app, select(func.count()).select_from(Application)) == 0


# ------------------------------------------------------------ employer insights
def test_csv_export_ranked_and_formula_safe(client, job, make_user):
    evil = make_user("applicant", "c1@example.com", name="=HYPERLINK(\"http://evil\")")
    good = make_user("applicant", "c2@example.com", name="Good Candidate")
    for user, resume in ((evil, CHEF_RESUME), (good, PYTHON_RESUME)):
        apply_as(client, user, job["id"], resume)
        post(client, "/logout")
    login(client, job["employer"]["email"])
    r = client.get(f"/employer/jobs/{job['id']}/export.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv" and "attachment" in r.headers["Content-Disposition"]
    rows = r.data.decode("utf-8-sig").splitlines()
    assert rows[0].startswith("Rank,Name,Email")
    assert "Good Candidate" in rows[1]  # best match ranked first
    assert "'=HYPERLINK" in rows[2] and ",=HYPERLINK" not in rows[2]  # formula neutralised


def test_applicants_filters(client, job, make_user):
    good = make_user("applicant", "f1@example.com", name="Zed Goodmatch")
    poor = make_user("applicant", "f2@example.com", name="Amy Poormatch")
    for user, resume in ((good, PYTHON_RESUME), (poor, CHEF_RESUME)):
        apply_as(client, user, job["id"], resume)
        post(client, "/logout")
    login(client, job["employer"]["email"])
    base = f"/employer/jobs/{job['id']}/applicants"
    only_good = client.get(base + "?min_score=15").data
    assert b"Zed Goodmatch" in only_good and b"Amy Poormatch" not in only_good
    searched = client.get(base + "?q=poormatch").data
    assert b"Amy Poormatch" in searched and b"Zed Goodmatch" not in searched
    assert client.get(base + "?min_score=abc").status_code == 200


def test_rescore_after_changing_required_skills(client, job, make_user, app):
    from models import Application
    from sqlalchemy import select

    cand = make_user("applicant", "r1@example.com")
    apply_as(client, cand, job["id"])
    post(client, "/logout")
    assert "Python" in scalar(app, select(Application.matched_skills))

    login(client, job["employer"]["email"])
    form = {"title": "Python Backend Developer", "description": JOB_DESCRIPTION, "skills": "Cooking, Baking",
            "job_type": "Full-time", "status": "open", "location": "Lahore"}
    post(client, f"/employer/jobs/{job['id']}/edit", form)  # no rescore flag
    assert "Python" in scalar(app, select(Application.matched_skills))
    post(client, f"/employer/jobs/{job['id']}/edit", {**form, "rescore": "1"})
    assert scalar(app, select(Application.matched_skills)) == ""

    post(client, f"/employer/jobs/{job['id']}/rescore")  # standalone endpoint also works
    assert 0 <= scalar(app, select(Application.match_score)) <= 100


def test_candidate_detail_notes_and_brief(client, job, make_user, app):
    from models import Application
    from sqlalchemy import select

    cand = make_user("applicant", "dd1@example.com", name="Detail Person")
    apply_as(client, cand, job["id"])
    post(client, "/logout")
    app_id = scalar(app, select(Application.id))

    rival = make_user("employer", "rival2@example.com")
    login(client, rival["email"])
    assert client.get(f"/employer/applications/{app_id}").status_code == 404
    assert post(client, f"/employer/applications/{app_id}/notes", {"notes": "x"}).status_code == 404
    assert post(client, f"/employer/applications/{app_id}/ai-summary").status_code == 404
    post(client, "/logout")

    login(client, job["employer"]["email"])
    page = client.get(f"/employer/applications/{app_id}")
    assert page.status_code == 200 and b"Detail Person" in page.data and b"Profile signals" in page.data
    post(client, f"/employer/applications/{app_id}/notes", {"notes": "Strong backend skills"})
    assert scalar(app, select(Application.employer_notes)) == "Strong backend skills"
    post(client, f"/employer/applications/{app_id}/ai-summary")  # no API key: local fallback
    assert scalar(app, select(Application.ai_summary)) is not None
    page = client.get(f"/employer/applications/{app_id}").data
    assert b"Strong backend skills" in page and b"Basic analysis" in page and b"Interview questions" in page
    # applicants never see employer notes
    post(client, "/logout")
    login(client, cand["email"])
    assert b"Strong backend skills" not in client.get("/applicant/applications").data


def test_ai_draft_endpoint(client, make_user):
    emp = make_user("employer", "ai1@example.com")
    cand = make_user("applicant", "ai2@example.com")
    login(client, emp["email"])
    ok = post(client, "/employer/jobs/ai-draft", {"title": "Data Analyst", "skills": "SQL, Excel"})
    assert ok.status_code == 200 and "Data Analyst" in ok.get_json()["description"]
    assert post(client, "/employer/jobs/ai-draft", {"title": "x"}).status_code == 400
    post(client, "/logout")
    login(client, cand["email"])
    assert post(client, "/employer/jobs/ai-draft", {"title": "Data Analyst"}).status_code == 403


# ------------------------------------------------------- applicant AI / recommendations
def test_check_resume_does_not_apply_or_store(client, job, make_user, app):
    from models import Application
    from sqlalchemy import func, select

    cand = make_user("applicant", "ck1@example.com")
    login(client, cand["email"])
    r = upload(client, f"/applicant/jobs/{job['id']}/check", make_pdf(PYTHON_RESUME))
    assert r.status_code == 200 and b"Your resume check" in r.data and b"nothing saved" in r.data
    assert scalar(app, select(func.count()).select_from(Application)) == 0
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []
    bad = upload(client, f"/applicant/jobs/{job['id']}/check", b"not a pdf", "cv.pdf")
    assert bad.status_code == 302


def test_recommendations_use_previous_resume(client, job, make_user, app):
    cand = make_user("applicant", "rec1@example.com")
    similar = make_job(app, job["employer"]["id"], title="Flask API Engineer")
    unrelated = make_job(app, job["employer"]["id"], title="Pastry Chef", description="Bake bread, cakes and pastries daily in our kitchen.",
                         skills="Baking, Cooking, Hygiene")
    login(client, cand["email"])
    assert b"Recommended for you" not in client.get("/applicant/dashboard").data  # nothing to base it on yet
    upload(client, f"/applicant/jobs/{job['id']}/apply", make_pdf(PYTHON_RESUME))
    page = client.get("/applicant/dashboard").data.decode()
    assert "Recommended for you" in page
    recommended = page.split("Recommended for you")[1].split('role="search"')[0]
    assert "Flask API Engineer" in recommended and "Pastry Chef" not in recommended


# ----------------------------------------------------------- external / imported jobs
def test_external_job_flow(client, job, make_user, app):
    from models import Application
    from sqlalchemy import func, select

    ext = make_job(app, job["employer"]["id"], title="Remote Python Dev", external_url="https://remotive.com/jobs/1",
                   source="remotive", company_label="Globex Remote")
    cand = make_user("applicant", "x1@example.com")
    login(client, cand["email"])
    board = client.get("/applicant/dashboard").data
    assert b"Remote Python Dev" in board and b"Globex Remote" in board
    detail = client.get(f"/applicant/jobs/{ext}").data
    assert b"Apply on original site" in detail and b"https://remotive.com/jobs/1" in detail
    upload(client, f"/applicant/jobs/{ext}/apply", make_pdf(PYTHON_RESUME))
    assert scalar(app, select(func.count()).select_from(Application)) == 0  # applying happens off-site


def test_import_jobs_dedupes_and_admin_can_trigger(client, make_user, app, monkeypatch):
    from models import Job, User
    from services import job_feeds
    from sqlalchemy import func, select

    def fake_fetch(limit):
        for i in range(3):
            yield {"source": "remotive", "title": f"Imported Job {i}", "company": "Globex", "html": "<p>Build Python and SQL systems for our remote team.</p>",
                   "url": f"https://remotive.com/jobs/{i}", "location": "Remote", "job_type": "Remote", "tags": ["python"],
                   "salary": None, "posted": None}
        yield {"source": "remotive", "title": "", "company": "x", "html": "short", "url": "ftp://bad", "location": None,
               "job_type": "Full-time", "tags": [], "salary": None, "posted": None}

    monkeypatch.setitem(job_feeds.FETCHERS, "remotive", fake_fetch)
    admin = make_user("admin", "imp@example.com")
    login(client, admin["email"])
    post(client, "/admin/jobs/import", {"source": "remotive", "limit": "10"})
    assert scalar(app, select(func.count()).select_from(Job).where(Job.external_url.is_not(None))) == 3
    post(client, "/admin/jobs/import", {"source": "remotive", "limit": "10"})  # second run: duplicates skipped
    assert scalar(app, select(func.count()).select_from(Job)) == 3
    job_row = scalar(app, select(Job).where(Job.title == "Imported Job 0"))
    assert job_row.source == "remotive" and job_row.company_label == "Globex" and "Python" in job_row.skills
    assert b"Imported Job 0" in client.get("/admin/jobs?source=external").data
    assert scalar(app, select(func.count()).select_from(User).where(User.email == job_feeds.FEED_EMAIL)) == 1
    assert job_feeds.FEED_EMAIL.encode() not in client.get("/admin/users").data  # system account is hidden


def test_import_requires_admin_and_validates_source(client, make_user):
    emp = make_user("employer", "imp2@example.com")
    login(client, emp["email"])
    assert post(client, "/admin/jobs/import", {"source": "all"}).status_code == 403
    post(client, "/logout")
    admin = make_user("admin", "imp3@example.com")
    login(client, admin["email"])
    r = post(client, "/admin/jobs/import", {"source": "linkedin"})
    assert r.status_code == 302  # rejected with a flash, nothing fetched


# ------------------------------------------------------------------- admin activity
def test_admin_activity_log(client, job, make_user):
    admin = make_user("admin", "log@example.com")
    cand = make_user("applicant", "log2@example.com")
    apply_as(client, cand, job["id"])
    post(client, "/logout")
    login(client, admin["email"])
    page = client.get("/admin/activity").data
    assert b"application" in page and b"login" in page
    assert client.get("/admin/activity?action=application").status_code == 200
    post(client, "/logout")
    login(client, cand["email"])
    assert client.get("/admin/activity").status_code == 403


# ------------------------------------------------------------------- CLI & migration
def test_seed_demo_and_create_admin_cli(app, client):
    from models import Job
    from sqlalchemy import func, select

    runner = app.test_cli_runner()
    result = runner.invoke(args=["seed-demo", "--password", "Demo12345", "--jobs", "15"])
    assert result.exit_code == 0, result.output
    assert scalar(app, select(func.count()).select_from(Job)) >= 10
    again = runner.invoke(args=["seed-demo", "--password", "Demo12345", "--jobs", "15"])
    assert again.exit_code == 0

    result = runner.invoke(args=["create-admin", "--email", "boss@example.com", "--password", "Adm1nPass99", "--name", "Boss"])
    assert result.exit_code == 0, result.output
    assert "/admin/dashboard" in login(client, "boss@example.com", "Adm1nPass99").headers["Location"]


def test_schema_upgrade_adds_columns_to_existing_database(tmp_path):
    pytest.importorskip("flask_sqlalchemy")
    from app import create_app
    from config import TestingConfig
    from extensions import db
    from sqlalchemy import inspect

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, full_name VARCHAR(120) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(256) NOT NULL, role VARCHAR(20) NOT NULL, company_name VARCHAR(150),
            is_suspended BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, last_login_at DATETIME);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, employer_id INTEGER NOT NULL, title VARCHAR(150) NOT NULL, description TEXT NOT NULL,
            skills TEXT NOT NULL, location VARCHAR(100), job_type VARCHAR(30) NOT NULL, status VARCHAR(20) NOT NULL,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, applicant_id INTEGER NOT NULL,
            resume_filename VARCHAR(64) NOT NULL, original_filename VARCHAR(255) NOT NULL, match_score FLOAT NOT NULL,
            matched_skills TEXT NOT NULL, status VARCHAR(20) NOT NULL, applied_at DATETIME NOT NULL);
        INSERT INTO users VALUES (1,'Old Boss','old@x.com','h','employer',NULL,0,'2026-01-01','2026-01-01');
        INSERT INTO jobs VALUES (1,1,'Legacy Job','Some long description for the legacy job posting.','Python','Lahore','Full-time','open','2026-01-01','2026-01-01');
    """)
    con.commit()
    con.close()

    class Cfg(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
        UPLOAD_FOLDER = str(tmp_path / "up")

    app = create_app(Cfg)
    with app.app_context():
        inspector = inspect(db.engine)
        job_cols = {c["name"] for c in inspector.get_columns("jobs")}
        app_cols = {c["name"] for c in inspector.get_columns("applications")}
        assert {"salary_range", "experience_level", "deadline", "source", "external_url", "company_label"} <= job_cols
        assert {"resume_text", "employer_notes", "ai_summary"} <= app_cols
        assert inspector.has_table("saved_jobs") and inspector.has_table("notifications") and inspector.has_table("activity_logs")
        from models import Job

        legacy = db.session.scalar(db.select(Job))
        assert legacy.title == "Legacy Job" and legacy.source == "internal" and legacy.is_open
        db.session.remove()
