# TalentMatch: Smart Resume Screener & Job Board (v2)

A Flask recruitment platform. Employers post jobs, applicants apply with PDF resumes, and an NLP engine
(TF-IDF + cosine similarity) scores every resume against the job and ranks applicants automatically.
Optional Claude AI features add resume coaching, candidate briefs and a job-description writer.

> **About the match score.** It is a *text-similarity indicator* between a resume and the job description plus
> required skills. It is not a guaranteed measure of a candidate's ability or suitability and is meant to support,
> not replace, human review. Typical scores are modest (a strong resume often lands at 25-60%); use them to
> *compare* applicants for the same job rather than as absolute grades.

## Features

| Role | Capabilities |
|---|---|
| **Applicant** | Job board with search, filters (type, experience, location, sort, deadline), **recommended jobs** from your resume, **save jobs**, **"Check my resume"** (score + skill gap + AI feedback without applying), apply with PDF, one application per job, **withdraw**, history with match %, skill coverage and status, notifications, profile and password change |
| **Employer** | Dashboard with charts (weekly trend, per-job, status, **score distribution**, **top candidates**), job create/edit/delete/open/close with **salary, experience level and deadline**, **AI job-description writer**, ranked applicants with **search, min-score and status filters**, **candidate profile page** (detected experience/education/contact signals, resume text preview), **AI candidate brief**, **private notes**, status updates that **notify the applicant**, **CSV export**, **re-score** after editing a job, **common skill gaps** chart, PDF download |
| **Admin** | Platform totals and charts, activity feed, **audit/activity log**, user search/suspend/delete (suspended users are signed out immediately), job moderation, **one-click import of real jobs** |
| **Everyone** | In-app notification bell, profile and security page |

**Security:** hashed passwords, role-based route guards, CSRF on every POST, **login throttling (5 failures / 15 min)**,
server-side validation, PDF magic-byte + extension + size checks, random server-side filenames, resumes stored outside
`/static` and served only after ownership checks, security headers + CSP, ORM-only queries, open-redirect protection,
CSV formula-injection protection, secrets only from environment variables.

## Project structure

```
smart_resume_screener/
├── app.py              # app factory, CSRF, error pages, CLI (init-db, create-admin, seed-demo, import-jobs)
├── wsgi.py             # production entry point (gunicorn wsgi:app)
├── config.py, extensions.py, utils.py
├── models/__init__.py  # User, Job, Application, SavedJob, Notification, ActivityLog
├── routes/             # main, auth, account, applicant, employer, admin blueprints
├── services/
│   ├── resume_parser.py     # PDF text extraction (pdfplumber) + error handling
│   ├── resume_screener.py   # cleaning, TF-IDF, cosine similarity, skill matching
│   ├── resume_insights.py   # experience / education / contact signals
│   ├── recommender.py       # job recommendations from a resume
│   ├── rescore.py           # re-scoring after a job edit
│   ├── ai.py                # optional Claude features + offline fallback
│   ├── job_feeds.py         # Remotive / Arbeitnow importer
│   ├── job_generator.py     # offline demo-job generator
│   ├── schema.py            # auto-adds new columns to an existing database
│   └── storage.py           # secure upload validation, storage, download
├── templates/  static/  tests/  uploads/  instance/
```

## Local installation

Requires Python 3.10+.

```bash
cd smart_resume_screener
python -m venv venv
venv\Scripts\activate               # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env              # Mac/Linux: cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY in .env
```

> If Windows blocks `flask.exe`, use `python -m flask ...` instead of `flask ...` everywhere below.

### Upgrading from v1

Just replace the project files and start the app. Missing tables and columns are added to your existing
`instance/app.db` automatically on startup; no data is lost.

### Create the administrator

```bash
python -m flask --app app create-admin --email admin@example.com --name "Admin"
```

### Add lots of jobs

```bash
# 1) Realistic offline demo jobs (no internet needed). Run again with a bigger number any time.
python -m flask --app app seed-demo --jobs 200

# 2) Real, current job listings from public job-board APIs (needs internet)
python -m flask --app app import-jobs --source all --limit 100
```

The admin can also import from **Admin > Job postings > Import**. Imported jobs keep a link to the original posting
and applicants apply there; applicants can still use *Check my resume* on them. Duplicates are skipped, so you can
import repeatedly. Sources: [Remotive](https://remotive.com/api/remote-jobs) (please import only a few times a day,
as their terms ask) and [Arbeitnow](https://www.arbeitnow.com/api/job-board-api).
**LinkedIn is deliberately not used:** scraping it violates its terms of service and is blocked technically.

### Run

```bash
python -m flask --app app --debug run     # http://127.0.0.1:5000
```

## AI features (optional)

Set `ANTHROPIC_API_KEY` in `.env` (and optionally `ANTHROPIC_MODEL`). Then:

* applicants get AI resume feedback (strengths, gaps, how to improve) in **Check my resume**;
* employers get an AI **candidate brief** (summary, highlights, gaps, interview questions) and the **job description writer**.

Without a key every feature still works using a built-in local fallback, so demos never depend on the internet.
Design notes: resume text is treated as untrusted data in the prompts (prompt-injection defence); AI output is
advisory and never changes a match score; calls are rate-limited per user (`AI_CALLS_PER_HOUR`); and **resume text is
sent to Anthropic's API only when a key is configured**, so tell your users if you enable it.

## Running the tests

```bash
pytest -v
```

Covers PDF extraction, text cleaning, TF-IDF similarity, skill matching, AI fallback and prompt safety, job-feed
parsing, profile signals, and the web flows: registration/login/roles, CSRF, throttling, jobs, uploads, duplicate
prevention, ranking, downloads, notifications, saved jobs, withdrawal, filters, CSV export, re-scoring, candidate
pages, recommendations, imports, admin moderation, CLI commands and the database upgrade path.

## How the screening engine works

1. The applicant uploads a PDF; it is validated (extension, `%PDF-` header, <= 5 MB).
2. `pdfplumber` extracts the text. Corrupt, encrypted, empty or scanned (image-only) PDFs produce a clear message.
3. The job description and required skills are combined into one reference text.
4. Both texts are lowercased; URLs, e-mails and punctuation are removed (`c++`, `c#`, `node.js` are preserved).
5. `TfidfVectorizer` (English stop words, 1-2-grams, sublinear TF) vectorises the pair.
6. `cosine_similarity` gives a value in [0, 1], stored as a percentage (0-100, one decimal).
7. Required skills found in the resume are stored; employers see them as chips and as skill coverage.
8. Employers see applicants sorted by score. Editing a job re-scores nobody unless the employer asks.

**Limitations (worth stating in your viva):** no OCR, so scanned resumes are rejected; TF-IDF measures vocabulary
overlap, not meaning; with two documents per comparison the IDF weighting is weak. The AI layer is advisory only.

## Deployment

### PythonAnywhere (simplest for SQLite)
1. Upload the project, create a virtualenv, `pip install -r requirements.txt`.
2. Web tab > Manual configuration. WSGI file: `from wsgi import app as application`.
3. Put `APP_ENV=production` and `SECRET_KEY=...` in `.env`; run `create-admin` in a Bash console; Reload.

### Render / any PaaS
- Build: `pip install -r requirements.txt`. Start: `gunicorn wsgi:app --workers 2 --timeout 60`.
- Env: `APP_ENV=production`, `SECRET_KEY=<random>`, optionally `ANTHROPIC_API_KEY`.
- SQLite and uploads need a **persistent disk**: set `DATABASE_URL=sqlite:////var/data/app.db` and
  `UPLOAD_FOLDER=/var/data/uploads`. Login throttling and AI limits are kept in memory per worker, so for several
  workers or servers move them to Redis.

`APP_ENV=production` turns on secure cookies and refuses to start without `SECRET_KEY`.

## GitHub checklist

`.gitignore` excludes `.env`, `instance/`, `uploads/` and `*.db`. Never commit real resumes, passwords or keys.
The front end loads Bootstrap, Bootstrap Icons and Chart.js from the jsDelivr CDN.
