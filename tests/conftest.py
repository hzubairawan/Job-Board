"""Fixtures for web-layer tests. Skipped automatically if Flask extensions are missing."""
import io
import itertools

import pytest

_counter = itertools.count(1)


@pytest.fixture()
def app(tmp_path):
    pytest.importorskip("flask_sqlalchemy")
    pytest.importorskip("flask_login")
    from app import create_app
    from config import TestingConfig
    from extensions import db

    class Cfg(TestingConfig):
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    application = create_app(Cfg)
    import utils
    from services.ai import reset_ai_limits

    utils.reset_login_throttle()
    reset_ai_limits()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_user(app):
    from extensions import db
    from models import User

    def _make(role="applicant", email=None, name=None, password="Passw0rd!x", company=None, suspended=False):
        email = email or f"{role}{next(_counter)}@example.com"
        with app.app_context():
            user = User(full_name=name or role.title() + " User", email=email, role=role,
                        company_name=company, is_suspended=suspended)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            return {"id": user.id, "email": email, "password": password, "name": user.full_name}

    return _make


def post(client, url, data=None, **kwargs):
    """POST with a valid CSRF token."""
    with client.session_transaction() as sess:
        sess["_csrf"] = "test-token"
    payload = dict(data or {})
    payload["csrf_token"] = "test-token"
    return client.post(url, data=payload, **kwargs)


def login(client, email, password="Passw0rd!x"):
    return post(client, "/login", {"email": email, "password": password})


def upload(client, url, pdf_bytes, filename="cv.pdf"):
    return post(client, url, {"resume": (io.BytesIO(pdf_bytes), filename)}, content_type="multipart/form-data")


@pytest.fixture()
def job(app, make_user):
    """An open job owned by a fresh employer."""
    from extensions import db
    from models import Job
    from tests.helpers import JOB_DESCRIPTION, JOB_SKILLS

    employer = make_user("employer", "boss@example.com", company="Acme")
    with app.app_context():
        j = Job(employer_id=employer["id"], title="Python Backend Developer",
                description=JOB_DESCRIPTION, skills=JOB_SKILLS, location="Lahore", job_type="Full-time")
        db.session.add(j)
        db.session.commit()
        return {"id": j.id, "employer": employer}
