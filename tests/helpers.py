"""Test helpers: generate small real PDFs with reportlab."""
import io

from reportlab.pdfgen import canvas

PYTHON_RESUME = [
    "Ayesha Khan - Backend Developer",
    "Experienced Python developer building REST APIs with Flask and SQLAlchemy.",
    "Designed SQL database schemas, wrote automated tests and used Git daily.",
    "Skills: Python, Flask, SQLAlchemy, SQL, REST, Git, Testing",
]

CHEF_RESUME = [
    "Bilal Ahmed - Head Chef",
    "Ten years of experience running restaurant kitchens and pastry sections.",
    "Menu planning, food safety, catering events and staff training.",
    "Skills: Cooking, Baking, Catering, Inventory management, Hygiene",
]

JOB_DESCRIPTION = (
    "We need a Python backend developer to build and maintain REST APIs with Flask and "
    "SQLAlchemy, design SQL database schemas, write automated tests and collaborate using Git."
)
JOB_SKILLS = "Python, Flask, SQLAlchemy, SQL, REST, Git, Testing"


def make_pdf(lines):
    """Return bytes of a text-based single-page PDF."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    y = 800
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 20
    pdf.save()
    return buf.getvalue()


def make_blank_pdf():
    """Valid PDF with no text at all (like a scanned document)."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.rect(50, 50, 200, 200)  # graphics only
    pdf.save()
    return buf.getvalue()
