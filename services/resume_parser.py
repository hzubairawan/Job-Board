"""PDF text extraction with explicit handling of empty, scanned and corrupt files."""
import io
import re

import pdfplumber

MAX_PAGES = 30
MAX_CHARS = 200_000
MIN_READABLE_CHARS = 30


class ResumeParseError(Exception):
    """Base error. The message is safe to show to end users."""


class InvalidPDFError(ResumeParseError):
    """The file is not a readable PDF (corrupt, encrypted, or not a PDF at all)."""


class EmptyPDFError(ResumeParseError):
    """The PDF opened fine but contains no usable text (e.g. scanned images)."""


def extract_text_from_pdf(source):
    """Return the readable text of a PDF.

    `source` may be raw bytes or a filesystem path. Raises InvalidPDFError or
    EmptyPDFError with a user-friendly message on failure.
    """
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        if not data:
            raise InvalidPDFError("The uploaded file is empty.")
        if b"%PDF-" not in data[:1024]:
            raise InvalidPDFError("The file is not a valid PDF document.")
        stream = io.BytesIO(data)
    else:
        stream = source

    try:
        with pdfplumber.open(stream) as pdf:
            if not pdf.pages:
                raise EmptyPDFError("The PDF has no pages.")
            chunks = []
            for page in pdf.pages[:MAX_PAGES]:
                chunks.append(page.extract_text() or "")
    except ResumeParseError:
        raise
    except Exception as exc:  # pdfminer raises many exception types for bad files
        raise InvalidPDFError(
            "This PDF could not be read. It may be corrupted or password-protected."
        ) from exc

    text = re.sub(r"[ \t]+", " ", "\n".join(chunks))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_CHARS]

    if len(re.sub(r"\s+", "", text)) < MIN_READABLE_CHARS:
        raise EmptyPDFError(
            "No readable text was found in this PDF. Scanned or image-only resumes "
            "are not supported - please upload a text-based PDF."
        )
    return text
