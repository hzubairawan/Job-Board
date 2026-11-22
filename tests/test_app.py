"""End-to-end tests of the Flask application (auth, roles, jobs, screening, downloads)."""
import os

import pytest

from tests.conftest import login, post, upload
from tests.helpers import CHEF_RESUME, PYTHON_RESUME, make_blank_pdf, make_pdf


def db_scalar(app, stmt):
    from extensions import db

    with app.app_context():
        return db.session.scalar(stmt)


# ------------------------------------------------------------ authentication
def test_applicant_registration_login_logout(client, app):
    from models import User
    from sqlalchemy import select

    r = post(client, "/register", {"full_name": "Sara Malik", "email": "Sara@Example.com", "role": "applicant",
                                   "password": "Secret123", "confirm_password": "Secret123"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    user = db_scalar(app, select(User).where(User.email == "sara@example.com"))
    assert user is not None and user.role == "applicant"
    assert user.password_hash != "Secret123"  # hashed

    r = login(client, "sara@example.com", "Secret123")
    assert r.status_code == 302 and "/applicant/dashboard" in r.headers["Location"]
    assert client.get("/applicant/dashboard").status_code == 200

    r = post(client, "/logout")
    assert r.status_code == 302
    assert client.get("/applicant/dashboard").status_code == 302  # back to login


def test_employer_registration_and_login(client, app):
    post(client, "/register", {"full_name": "Hina Boss", "email": "hina@corp.com", "role": "employer",
                               "company_name": "Corp Ltd", "password": "Secret123", "confirm_password": "Secret123"})
    r = login(client, "hina@corp.com", "Secret123")
    assert "/employer/dashboard" in r.headers["Location"]
    assert b"Corp Ltd" in client.get("/employer/dashboard").data


def test_admin_cannot_register_publicly(client, app):
    from models import User
    from sqlalchemy import func, select

    r = post(client, "/register", {"full_name": "Evil", "email": "evil@example.com", "role": "admin",
                                   "password": "Secret123", "confirm_password": "Secret123"})
    assert r.status_code == 200  # form re-rendered with an error
    assert db_scalar(app, select(func.count()).select_from(User)) == 0


def test_admin_login_and_dashboard(client, make_user):
    admin = make_user("admin", "root@example.com")
    r = login(client, admin["email"])
    assert "/admin/dashboard" in r.headers["Location"]
    page = client.get("/admin/dashboard")
    assert page.status_code == 200 and b"Registered users" in page.data


def test_registration_validation(client, make_user):
    make_user("applicant", "taken@example.com")
    base = {"full_name": "X Y", "role": "applicant", "password": "Secret123", "confirm_password": "Secret123"}
    assert b"already exists" in post(client, "/register", {**base, "email": "taken@example.com"}).data
    assert b"valid email" in post(client, "/register", {**base, "email": "not-an-email"}).data
    assert b"at least 8" in post(client, "/register", {**base, "email": "a@b.com", "password": "short1", "confirm_password": "short1"}).data
    assert b"do not match" in post(client, "/register", {**base, "email": "a@b.com", "confirm_password": "Different1"}).data


def test_wrong_password_and_suspended_login(client, make_user):
    user = make_user("applicant", "u@example.com")
    assert b"Invalid email or password" in login(client, user["email"], "wrong-password").data
    suspended = make_user("applicant", "s@example.com", suspended=True)
    assert b"suspended" in login(client, suspended["email"]).data
    assert client.get("/applicant/dashboard").status_code == 302


def test_open_redirect_is_blocked(client, make_user):
    user = make_user("applicant", "r@example.com")
    with client.session_transaction() as s:
        s["_csrf"] = "test-token"
    r = client.post("/login?next=https://evil.example.com/", data={"email": user["email"],
                    "password": user["password"], "csrf_token": "test-token"})
    assert "evil.example.com" not in r.headers["Location"]


def test_csrf_required(client, make_user):
    user = make_user("applicant", "c@example.com")
    r = client.post("/login", data={"email": user["email"], "password": user["password"]})
    assert r.status_code == 400


# ------------------------------------------------------- role-based access
def test_anonymous_redirected_to_login(client):
    for url in ("/applicant/dashboard", "/employer/dashboard", "/admin/dashboard"):
        r = client.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_roles_cannot_access_other_areas(client, make_user):
    a = make_user("applicant", "a@example.com")
    login(client, a["email"])
    assert client.get("/employer/dashboard").status_code == 403
    assert client.get("/employer/jobs/new").status_code == 403
    assert client.get("/admin/users").status_code == 403
    post(client, "/logout")

    e = make_user("employer", "e@example.com")
    login(client, e["email"])
    assert client.get("/applicant/dashboard").status_code == 403
    assert client.get("/admin/dashboard").status_code == 403


def test_security_headers_and_error_pages(client):
    r = client.get("/definitely-missing")
    assert r.status_code == 404 and b"Page not found" in r.data
    home = client.get("/")
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in home.headers["Content-Security-Policy"]


# --------------------------------------------------------------- job management
def test_employer_job_lifecycle(client, make_user, app):
    from models import Job
    from sqlalchemy import select

    e = make_user("employer", "emp@example.com")
    login(client, e["email"])

    bad = post(client, "/employer/jobs/new", {"title": "x", "description": "short", "skills": "", "job_type": "Full-time", "status": "open"})
    assert b"between 3 and 150" in bad.data and b"at least one required skill" in bad.data

    form = {"title": "Data Analyst", "description": "Analyse data, build dashboards and report insights to the leadership team.",
            "skills": "SQL, Python, SQL, Excel", "location": "Karachi", "job_type": "Full-time", "status": "open"}
    assert post(client, "/employer/jobs/new", form).status_code == 302
    job = db_scalar(app, select(Job).where(Job.title == "Data Analyst"))
    assert job.skills == "SQL, Python, Excel"  # de-duplicated

    form["title"] = "Senior Data Analyst"
    assert post(client, f"/employer/jobs/{job.id}/edit", form).status_code == 302
    assert db_scalar(app, select(Job.title).where(Job.id == job.id)) == "Senior Data Analyst"

    post(client, f"/employer/jobs/{job.id}/toggle")
    assert db_scalar(app, select(Job.status).where(Job.id == job.id)) == "closed"
    post(client, f"/employer/jobs/{job.id}/toggle")
    assert db_scalar(app, select(Job.status).where(Job.id == job.id)) == "open"

    assert client.get("/employer/dashboard").status_code == 200
    post(client, f"/employer/jobs/{job.id}/delete")
    assert db_scalar(app, select(Job.id).where(Job.id == job.id)) is None


def test_employer_cannot_touch_other_employers_jobs(client, job, make_user):
    other = make_user("employer", "other@example.com")
    login(client, other["email"])
    assert client.get(f"/employer/jobs/{job['id']}/edit").status_code == 404
    assert client.get(f"/employer/jobs/{job['id']}/applicants").status_code == 404
    assert post(client, f"/employer/jobs/{job['id']}/delete").status_code == 404
    assert post(client, f"/employer/jobs/{job['id']}/toggle").status_code == 404


def test_applicant_search_and_closed_jobs_hidden(client, job, make_user, app):
    from extensions import db
    from models import Job

    with app.app_context():
        db.session.add(Job(employer_id=job["employer"]["id"], title="Closed Chef Role", description="x" * 40,
                           skills="Cooking", status="closed"))
        db.session.commit()
    a = make_user("applicant", "seek@example.com")
    login(client, a["email"])
    page = client.get("/applicant/dashboard").data
    assert b"Python Backend Developer" in page and b"Closed Chef Role" not in page
    assert b"Python Backend Developer" in client.get("/applicant/dashboard?q=python").data
    assert b"No jobs found" in client.get("/applicant/dashboard?q=zzzz-nothing").data
    assert client.get("/applicant/dashboard?q=100%25").status_code == 200  # LIKE wildcard is escaped, no crash
    assert client.get(f"/applicant/jobs/{job['id']}").status_code == 200


# ------------------------------------------------------- resume screening flow
def test_apply_scores_and_stores_application(client, job, make_user, app):
    from models import Application
    from sqlalchemy import select

    a = make_user("applicant", "cand@example.com")
    login(client, a["email"])
    r = upload(client, f"/applicant/jobs/{job['id']}/apply", make_pdf(PYTHON_RESUME), "My CV.pdf")
    assert r.status_code == 302 and "/applicant/applications" in r.headers["Location"]

    application = db_scalar(app, select(Application))
    assert application.match_score > 15
    assert "Python" in application.matched_skill_list
    assert application.status == "pending"
    assert application.resume_filename != "My_CV.pdf" and application.resume_filename.endswith(".pdf")
    assert os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], application.resume_filename))

    page = client.get("/applicant/applications").data
    assert b"Python Backend Developer" in page and b"text-similarity indicator" in page


def test_duplicate_application_prevented(client, job, make_user, app):
    from models import Application
    from sqlalchemy import func, select

    a = make_user("applicant", "dup@example.com")
    login(client, a["email"])
    url = f"/applicant/jobs/{job['id']}/apply"
    upload(client, url, make_pdf(PYTHON_RESUME))
    r = upload(client, url, make_pdf(PYTHON_RESUME))
    assert r.status_code == 302
    assert db_scalar(app, select(func.count()).select_from(Application)) == 1
    assert len(os.listdir(app.config["UPLOAD_FOLDER"])) == 1  # second file was not kept


def test_invalid_uploads_rejected(client, job, make_user, app):
    from models import Application
    from sqlalchemy import func, select

    a = make_user("applicant", "bad@example.com")
    login(client, a["email"])
    url = f"/applicant/jobs/{job['id']}/apply"

    def count():
        return db_scalar(app, select(func.count()).select_from(Application))

    # wrong extension, fake PDF, corrupt PDF, scanned/blank PDF, oversize file, no file at all
    assert upload(client, url, b"hello", "notes.txt").status_code == 302
    assert upload(client, url, b"not really a pdf", "cv.pdf").status_code == 302
    assert upload(client, url, b"%PDF-1.4 corrupted content here", "cv.pdf").status_code == 302
    assert upload(client, url, make_blank_pdf(), "scan.pdf").status_code == 302
    assert upload(client, url, b"%PDF-" + b"0" * (5 * 1024 * 1024 + 10), "big.pdf").status_code in (302, 413)
    assert post(client, url, {}).status_code == 302
    assert count() == 0
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []

    page = client.get(f"/applicant/jobs/{job['id']}")
    assert page.status_code == 200


def test_cannot_apply_to_closed_job(client, job, make_user, app):
    from models import Application, Job
    from extensions import db
    from sqlalchemy import func, select

    with app.app_context():
        db.session.get(Job, job["id"]).status = "closed"
        db.session.commit()
    a = make_user("applicant", "late@example.com")
    login(client, a["email"])
    upload(client, f"/applicant/jobs/{job['id']}/apply", make_pdf(PYTHON_RESUME))
    assert db_scalar(app, select(func.count()).select_from(Application)) == 0


def test_applicants_ranked_by_match_score(client, job, make_user, app):
    good = make_user("applicant", "good@example.com", name="Zed Goodmatch")
    poor = make_user("applicant", "poor@example.com", name="Amy Poormatch")
    for user, resume in ((poor, CHEF_RESUME), (good, PYTHON_RESUME)):  # poor applies first
        login(client, user["email"])
        upload(client, f"/applicant/jobs/{job['id']}/apply", make_pdf(resume))
        post(client, "/logout")

    login(client, job["employer"]["email"])
    page = client.get(f"/employer/jobs/{job['id']}/applicants").data.decode()
    assert page.index("Zed Goodmatch") < page.index("Amy Poormatch")  # best first, despite alphabetical/chronological order
    assert "good@example.com" in page and "poor@example.com" in page

    newest = client.get(f"/employer/jobs/{job['id']}/applicants?sort=newest").data.decode()
    assert newest.index("Zed Goodmatch") < newest.index("Amy Poormatch")  # good applied last -> newest first

    from models import Application
    from sqlalchemy import select
    with app.app_context():
        from extensions import db
        scores = {a.applicant.full_name: a.match_score for a in db.session.scalars(select(Application))}
    assert scores["Zed Goodmatch"] > scores["Amy Poormatch"]


def test_employer_can_update_application_status(client, job, make_user, app):
    from models import Application
    from sqlalchemy import select

    a = make_user("applicant", "st@example.com")
    login(client, a["email"])
    upload(client, f"/applicant/jobs/{job['id']}/apply", make_pdf(PYTHON_RESUME))
    post(client, "/logout")
    login(client, job["employer"]["email"])
    app_id = db_scalar(app, select(Application.id))
    post(client, f"/employer/applications/{app_id}/status", {"status": "shortlisted"})
    assert db_scalar(app, select(Application.status)) == "shortlisted"
    post(client, f"/employer/applications/{app_id}/status", {"status": "hacked"})
    assert db_scalar(app, select(Application.status)) == "shortlisted"


# ----------------------------------------------------------- resume downloads
def test_resume_download_permissions(client, job, make_user, app):
    from models import Application
    from sqlalchemy import select

    owner = make_user("applicant", "owner@example.com")
    stranger = make_user("applicant", "stranger@example.com")
    other_employer = make_user("employer", "rival@example.com")
    pdf = make_pdf(PYTHON_RESUME)

    login(client, owner["email"])
    upload(client, f"/applicant/jobs/{job['id']}/apply", pdf)
    app_id = db_scalar(app, select(Application.id))
    own = client.get(f"/applicant/applications/{app_id}/resume")
    assert own.status_code == 200 and own.mimetype == "application/pdf" and own.data == pdf
    post(client, "/logout")

    login(client, job["employer"]["email"])
    r = client.get(f"/employer/applications/{app_id}/resume")
    assert r.status_code == 200 and r.data == pdf
    assert "attachment" in r.headers["Content-Disposition"]
    post(client, "/logout")

    login(client, stranger["email"])
    assert client.get(f"/applicant/applications/{app_id}/resume").status_code == 404
    assert client.get(f"/employer/applications/{app_id}/resume").status_code == 403
    post(client, "/logout")

    login(client, other_employer["email"])
    assert client.get(f"/employer/applications/{app_id}/resume").status_code == 404
    post(client, "/logout")

    assert client.get(f"/employer/applications/{app_id}/resume").status_code == 302  # anonymous
    stored = db_scalar(app, select(Application.resume_filename))
    assert client.get(f"/static/../uploads/{stored}").status_code == 404  # never served statically


# ------------------------------------------------------------- admin features
def test_admin_suspend_and_delete_user(client, job, make_user, app):
    from models import Application, User
    from sqlalchemy import func, select

    admin = make_user("admin", "root@example.com")
    victim = make_user("applicant", "victim@example.com")
    login(client, victim["email"])
    upload(client, f"/applicant/jobs/{job['id']}/apply", make_pdf(PYTHON_RESUME))
    post(client, "/logout")
    assert len(os.listdir(app.config["UPLOAD_FOLDER"])) == 1

    login(client, admin["email"])
    assert b"victim@example.com" in client.get("/admin/users").data
    post(client, f"/admin/users/{victim['id']}/suspend")
    assert db_scalar(app, select(User.is_suspended).where(User.id == victim["id"])) is True
    post(client, "/logout")
    assert b"suspended" in login(client, victim["email"]).data

    login(client, admin["email"])
    post(client, f"/admin/users/{admin['id']}/delete")  # admins are protected
    assert db_scalar(app, select(User.id).where(User.id == admin["id"])) is not None
    post(client, f"/admin/users/{victim['id']}/delete")
    assert db_scalar(app, select(User.id).where(User.id == victim["id"])) is None
    assert db_scalar(app, select(func.count()).select_from(Application)) == 0
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []  # resume file removed too


def test_admin_job_moderation_and_stats(client, job, make_user, app):
    from models import Job
    from sqlalchemy import select

    admin = make_user("admin", "root@example.com")
    login(client, admin["email"])
    assert b"Python Backend Developer" in client.get("/admin/jobs").data
    assert client.get("/admin/dashboard").status_code == 200
    post(client, f"/admin/jobs/{job['id']}/delete")
    assert db_scalar(app, select(Job.id).where(Job.id == job["id"])) is None
