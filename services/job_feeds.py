"""Import real job listings from free, public job-board APIs.

Sources (both publish open APIs intended for this use):
  * Remotive   https://remotive.com/api/remote-jobs    (please do not poll more than a few times per day)
  * Arbeitnow  https://www.arbeitnow.com/api/job-board-api

Imported jobs keep a link to the original posting and the source name, and applicants
apply on the original site. LinkedIn is deliberately NOT used: scraping it violates its terms.
"""
import json
import logging
import re
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode


log = logging.getLogger(__name__)
FEED_EMAIL = "jobfeed@talentmatch.local"
MAX_DESCRIPTION = 8000
SOURCES = ("remotive", "arbeitnow")

SKILL_VOCAB = [
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Rust", "PHP", "Ruby", "Swift", "Kotlin",
    "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "React", "Angular", "Vue", "Node.js", "Django", "Flask",
    "Spring", "Laravel", ".NET", "HTML", "CSS", "Tailwind", "Docker", "Kubernetes", "AWS", "Azure", "GCP",
    "Terraform", "Linux", "Git", "CI/CD", "REST", "GraphQL", "Machine Learning", "Deep Learning", "NLP",
    "TensorFlow", "PyTorch", "Pandas", "Data Analysis", "Tableau", "Power BI", "Excel", "Figma", "UI/UX",
    "Agile", "Scrum", "Jira", "SEO", "Content Writing", "Marketing", "Sales", "Customer Support", "Accounting",
    "Project Management", "Product Management", "Cybersecurity", "DevOps", "QA", "Selenium", "Android", "iOS",
    "Flutter", "React Native", "Salesforce", "SAP", "Communication", "Leadership",
]


class FeedError(Exception):
    pass


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:  # malformed markup: fall back to a crude tag strip
        return re.sub(r"<[^>]+>", " ", html or "")
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract_skills(text, tags=()):
    found, seen = [], set()
    for tag in tags:
        tag = str(tag).strip()
        if 1 < len(tag) <= 30 and tag.lower() not in seen:
            seen.add(tag.lower())
            found.append(tag)
    lowered = f" {text.lower()} "
    for skill in SKILL_VOCAB:
        pattern = r"(?<![a-z0-9+#.])" + re.escape(skill.lower()) + r"(?![a-z0-9+#])"
        if skill.lower() not in seen and re.search(pattern, lowered):
            seen.add(skill.lower())
            found.append(skill)
    return found[:12]


def _http_json(url, params=None):
    if params:
        url = f"{url}?{urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "TalentMatch-FYP/1.0 (job aggregator demo)", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise FeedError(f"could not fetch {url.split('?')[0]}: {exc}") from exc


def _map_type(raw, remote=False):
    raw = (raw or "").lower().replace("_", "-")
    for key, label in (("intern", "Internship"), ("part", "Part-time"), ("contract", "Contract"),
                       ("freelance", "Contract")):
        if key in raw:
            return label
    if remote:
        return "Remote"
    return "Full-time"


def _parse_date(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, OSError, OverflowError):
        return None


def fetch_remotive(limit):
    data = _http_json("https://remotive.com/api/remote-jobs", {"limit": max(1, min(limit, 500))})
    for item in (data.get("jobs") or [])[:limit]:
        yield {
            "source": "remotive", "title": item.get("title"), "company": item.get("company_name"),
            "html": item.get("description"), "url": item.get("url"),
            "location": item.get("candidate_required_location") or "Remote",
            "job_type": _map_type(item.get("job_type"), remote=True), "tags": item.get("tags") or [],
            "salary": item.get("salary"), "posted": _parse_date(item.get("publication_date")),
        }


def fetch_arbeitnow(limit):
    produced, page = 0, 1
    while produced < limit and page <= 6:
        data = _http_json("https://www.arbeitnow.com/api/job-board-api", {"page": page})
        rows = data.get("data") or []
        if not rows:
            return
        for item in rows:
            if produced >= limit:
                return
            produced += 1
            types = item.get("job_types") or []
            yield {
                "source": "arbeitnow", "title": item.get("title"), "company": item.get("company_name"),
                "html": item.get("description"), "url": item.get("url"), "location": item.get("location"),
                "job_type": _map_type(" ".join(map(str, types)), remote=bool(item.get("remote"))),
                "tags": item.get("tags") or [], "salary": None, "posted": _parse_date(item.get("created_at")),
            }
        page += 1


FETCHERS = {"remotive": fetch_remotive, "arbeitnow": fetch_arbeitnow}


def get_feed_user():
    """System employer account that owns imported jobs (cannot log in)."""
    from sqlalchemy import select

    from extensions import db
    from models import ROLE_EMPLOYER, User

    user = db.session.scalar(select(User).where(User.email == FEED_EMAIL))
    if user is None:
        user = User(full_name="Job Feed", email=FEED_EMAIL, role=ROLE_EMPLOYER, company_name="TalentMatch Job Feed")
        user.set_password(secrets.token_urlsafe(40))
        db.session.add(user)
        db.session.flush()
    return user


def import_jobs(source="all", limit=100):
    """Import up to `limit` jobs per source. Returns {'created': n, 'skipped': n, 'errors': [...]}."""
    from sqlalchemy import select

    from extensions import db
    from models import JOB_TYPES, Job, utcnow

    sources = SOURCES if source == "all" else (source,)
    summary = {"created": 0, "skipped": 0, "errors": []}
    feed_user = None
    for name in sources:
        if name not in FETCHERS:
            summary["errors"].append(f"unknown source {name!r}")
            continue
        try:
            records = list(FETCHERS[name](limit))
        except FeedError as exc:
            summary["errors"].append(str(exc))
            continue
        urls = [r["url"] for r in records if r.get("url")]
        existing = set()
        for i in range(0, len(urls), 400):
            existing.update(db.session.scalars(select(Job.external_url).where(Job.external_url.in_(urls[i:i + 400]))))
        for rec in records:
            title = (rec.get("title") or "").strip()
            url = (rec.get("url") or "").strip()
            description = html_to_text(rec.get("html"))[:MAX_DESCRIPTION]
            if not title or not url.startswith("http") or url in existing or len(description) < 30:
                summary["skipped"] += 1
                continue
            skills = extract_skills(f"{title}\n{description}", rec.get("tags") or [])
            job_type = rec["job_type"] if rec["job_type"] in JOB_TYPES else "Full-time"
            feed_user = feed_user or get_feed_user()
            created = rec.get("posted") or utcnow()
            db.session.add(Job(
                employer_id=feed_user.id, title=title[:150], description=description,
                skills=", ".join(skills) or "General", location=(rec.get("location") or "")[:100] or None,
                job_type=job_type, salary_range=(str(rec["salary"])[:60] if rec.get("salary") else None),
                source=name, external_url=url[:500], company_label=(rec.get("company") or "")[:150] or None,
                created_at=created, updated_at=created,
            ))
            existing.add(url)
            summary["created"] += 1
    db.session.commit()
    return summary
