from flask import Blueprint, redirect, render_template
from flask_login import current_user

from utils import dashboard_url_for

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))
    return render_template("index.html")


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}
