"""Tiny auto-migration: adds columns introduced after the first release.

`db.create_all()` creates missing tables but never alters existing ones, so
anyone upgrading with an existing SQLite file would hit "no such column".
"""
from sqlalchemy import inspect, text

NEW_COLUMNS = {
    "jobs": {
        "salary_range": "VARCHAR(60)",
        "experience_level": "VARCHAR(30)",
        "deadline": "DATE",
        "source": "VARCHAR(30) NOT NULL DEFAULT 'internal'",
        "external_url": "VARCHAR(500)",
        "company_label": "VARCHAR(150)",
    },
    "applications": {
        "resume_text": "TEXT",
        "employer_notes": "TEXT",
        "ai_summary": "TEXT",
    },
}


def ensure_schema(db):
    db.create_all()
    inspector = inspect(db.engine)
    with db.engine.begin() as connection:
        for table, columns in NEW_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
