"""Secure resume storage. Files live outside /static under random names."""
import logging
import uuid
from pathlib import Path

from flask import abort, current_app, send_from_directory
from werkzeug.utils import secure_filename

log = logging.getLogger(__name__)


class UploadError(Exception):
    """Validation problem with an uploaded file. Message is safe to display."""


def upload_dir():
    path = Path(current_app.config["UPLOAD_FOLDER"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_pdf_upload(file_storage):
    """Validate an uploaded file; return (bytes, safe_original_filename)."""
    if file_storage is None or not file_storage.filename:
        raise UploadError("Please choose a PDF resume to upload.")
    original = secure_filename(file_storage.filename)
    if not original or not original.lower().endswith(".pdf"):
        raise UploadError("Only PDF files (.pdf) are accepted.")

    max_bytes = current_app.config["MAX_RESUME_BYTES"]
    data = file_storage.stream.read(max_bytes + 1)
    if not data:
        raise UploadError("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise UploadError(f"The file is too large. The limit is {max_bytes // (1024 * 1024)} MB.")
    if b"%PDF-" not in data[:1024]:
        raise UploadError("The file is not a valid PDF document.")
    return data, original


def save_resume(data):
    """Write bytes under a random filename and return that filename."""
    name = f"{uuid.uuid4().hex}.pdf"
    (upload_dir() / name).write_bytes(data)
    return name


def delete_resume_files(names):
    for name in names:
        try:
            (upload_dir() / Path(name).name).unlink(missing_ok=True)
        except OSError:
            log.warning("Could not delete resume file %s", name)


def send_resume(application):
    safe_name = Path(application.resume_filename).name
    if not (upload_dir() / safe_name).is_file():
        abort(404)
    return send_from_directory(
        upload_dir(),
        safe_name,
        as_attachment=True,
        download_name=application.original_filename or "resume.pdf",
        mimetype="application/pdf",
    )
