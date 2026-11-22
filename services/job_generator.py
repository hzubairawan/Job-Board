"""Offline generator of realistic demo jobs, for presentations without internet access."""
import random
from datetime import timedelta

from models import Job, utcnow

ROLES = [
    ("Python Backend Developer", "Mid level", ["Python", "Flask", "Django", "SQL", "REST", "Git", "Docker"], "APIs and services"),
    ("Frontend Developer", "Entry level", ["JavaScript", "React", "HTML", "CSS", "Bootstrap", "Git"], "responsive web interfaces"),
    ("Full Stack Engineer", "Senior level", ["JavaScript", "Node.js", "React", "SQL", "AWS", "Docker"], "end-to-end product features"),
    ("Data Analyst", "Entry level", ["SQL", "Excel", "Python", "Pandas", "Power BI", "Statistics"], "dashboards and business insights"),
    ("Data Scientist", "Mid level", ["Python", "Machine Learning", "Pandas", "scikit-learn", "Statistics", "SQL"], "predictive models"),
    ("NLP Engineer", "Mid level", ["Python", "NLP", "TF-IDF", "scikit-learn", "PyTorch", "Text Mining"], "language-processing pipelines"),
    ("Machine Learning Engineer", "Senior level", ["Python", "TensorFlow", "PyTorch", "MLOps", "Docker", "AWS"], "production ML systems"),
    ("DevOps Engineer", "Mid level", ["Linux", "Docker", "Kubernetes", "AWS", "Terraform", "CI/CD"], "deployment pipelines"),
    ("Cybersecurity Analyst", "Mid level", ["Network Security", "SIEM", "Linux", "Incident Response", "Python"], "threat monitoring"),
    ("QA Automation Engineer", "Mid level", ["Selenium", "Python", "Testing", "Jira", "CI/CD", "API Testing"], "automated test suites"),
    ("Mobile App Developer", "Mid level", ["Flutter", "Dart", "Firebase", "REST", "Git"], "cross-platform mobile apps"),
    ("Android Developer", "Mid level", ["Kotlin", "Android", "Jetpack", "REST", "Git"], "native Android applications"),
    ("UI/UX Designer", "Mid level", ["Figma", "UI/UX", "Prototyping", "User Research", "Design Systems"], "intuitive product design"),
    ("Product Manager", "Senior level", ["Product Management", "Agile", "Roadmapping", "Analytics", "Communication"], "product strategy"),
    ("Project Coordinator", "Entry level", ["Project Management", "Scrum", "Jira", "Communication", "Excel"], "delivery coordination"),
    ("Digital Marketing Executive", "Entry level", ["SEO", "Content Marketing", "Google Analytics", "Social Media", "Copywriting"], "growth campaigns"),
    ("Content Writer", "Entry level", ["Content Writing", "SEO", "Editing", "Research", "Copywriting"], "articles and web copy"),
    ("Accountant", "Mid level", ["Accounting", "Excel", "Taxation", "QuickBooks", "Financial Reporting"], "bookkeeping and reporting"),
    ("Customer Support Specialist", "Entry level", ["Customer Support", "Communication", "CRM", "Problem Solving"], "customer success"),
    ("Sales Executive", "Mid level", ["Sales", "CRM", "Negotiation", "Communication", "Lead Generation"], "new business growth"),
    ("HR Officer", "Mid level", ["Recruitment", "Communication", "HR Policies", "Onboarding", "Excel"], "talent acquisition"),
    ("Cloud Architect", "Senior level", ["AWS", "Azure", "Terraform", "Networking", "Security", "Kubernetes"], "cloud infrastructure"),
    ("Database Administrator", "Mid level", ["SQL", "PostgreSQL", "MySQL", "Backup", "Performance Tuning"], "reliable data platforms"),
    ("Software Engineering Intern", "Entry level", ["Python", "Git", "Problem Solving", "SQL", "Communication"], "learning on real projects"),
    ("Graphic Designer", "Entry level", ["Photoshop", "Illustrator", "Branding", "Typography", "Figma"], "brand visuals"),
]
COMPANIES = ["Northwind Tech", "Lumen Analytics", "BlueOrbit Systems", "Crescent Software", "Nexa Labs", "Pioneer Digital",
             "Apex Cloudworks", "Zenith Solutions", "Orchid Health", "Mosaic Finance", "Falcon Logistics", "Indus Commerce",
             "Quantum Leap AI", "Harbor Retail", "Skyline Media", "Evergreen Energy", "Atlas Consulting", "Vertex Mobile"]
CITIES = ["Lahore", "Karachi", "Islamabad", "Rawalpindi", "Faisalabad", "Peshawar", "Multan", "Remote", "Hybrid - Lahore",
          "Dubai", "Riyadh", "London"]
TYPES = ["Full-time"] * 6 + ["Contract", "Part-time", "Remote", "Internship"]
SALARIES = {"Entry level": ["PKR 60k-90k / month", "PKR 70k-110k / month"],
            "Mid level": ["PKR 120k-220k / month", "PKR 150k-250k / month"],
            "Senior level": ["PKR 300k-500k / month", "PKR 350k-600k / month"]}


def _description(role, company, level, skills, focus):
    return (
        f"{company} is hiring a {role} to work on {focus}. You will join a collaborative team and take ownership "
        f"of meaningful work from planning through delivery.\n\n"
        f"Responsibilities:\n- Design, build and maintain {focus}\n- Work closely with teammates and stakeholders\n"
        f"- Write clear documentation and review others' work\n- Suggest and apply improvements to our process\n\n"
        f"Requirements:\n" + "\n".join(f"- Practical experience with {s}" for s in skills[:5]) +
        f"\n- {level} experience or equivalent projects\n- Good communication and a willingness to learn\n\n"
        "What we offer:\n- Competitive pay, flexible hours and a supportive, growth-minded culture."
    )


def generate_demo_jobs(count, employer_id, rng=None):
    """Return `count` unsaved Job objects (all owned by `employer_id`)."""
    rng = rng or random.Random()
    jobs, seen = [], set()
    attempts = 0
    while len(jobs) < count and attempts < count * 20:
        attempts += 1
        role, level, skills, focus = rng.choice(ROLES)
        company, city = rng.choice(COMPANIES), rng.choice(CITIES)
        key = (role, company, city)
        if key in seen:
            continue
        seen.add(key)
        job_type = "Internship" if "Intern" in role else rng.choice(TYPES)
        posted = utcnow() - timedelta(days=rng.randint(0, 45), hours=rng.randint(0, 23))
        deadline = (utcnow() + timedelta(days=rng.randint(10, 60))).date() if rng.random() < 0.7 else None
        chosen = list(skills)
        rng.shuffle(chosen)
        jobs.append(Job(
            employer_id=employer_id, title=role, company_label=company, location=city, job_type=job_type,
            experience_level=level, salary_range=rng.choice(SALARIES[level]) if rng.random() < 0.8 else None,
            skills=", ".join(chosen[: rng.randint(4, len(skills))]), deadline=deadline, source="internal",
            description=_description(role, company, level, skills, focus), created_at=posted, updated_at=posted,
        ))
    return jobs
