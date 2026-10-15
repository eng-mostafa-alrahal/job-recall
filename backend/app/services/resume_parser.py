import hashlib
import io
import json
import re

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models.entities import UserProfile, utcnow
from app.schemas.resumes_profile import ParsedResumeMetadata

KNOWN_SKILLS = {
    "python": "Python",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "java": "Java",
    "c++": "C++",
    "c#": "C#",
    "golang": "Go",
    "rust": "Rust",
    "sql": "SQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "react": "React",
    "next.js": "Next.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    "terraform": "Terraform",
    "graphql": "GraphQL",
    "rest": "REST APIs",
    "grpc": "gRPC",
    "kafka": "Kafka",
    "langchain": "LangChain",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "spark": "Apache Spark",
    "linux": "Linux",
    "git": "Git",
    "ci/cd": "CI/CD",
}

KNOWN_DOMAINS = {
    "backend": "Backend Development",
    "back-end": "Backend Development",
    "frontend": "Frontend Development",
    "front-end": "Frontend Development",
    "full stack": "Full-Stack Development",
    "full-stack": "Full-Stack Development",
    "distributed systems": "Distributed Systems",
    "machine learning": "Machine Learning",
    "deep learning": "Deep Learning",
    "data engineering": "Data Engineering",
    "cloud": "Cloud Infrastructure",
    "devops": "DevOps",
    "security": "Cybersecurity",
    "microservices": "Microservices",
    "mobile": "Mobile Development",
}

DEGREE_RANK = {
    "none": 0,
    "high_school": 1,
    "associate": 2,
    "bachelor": 3,
    "master": 4,
    "phd": 5,
}


def compute_resume_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def parse_resume_text(content_text: str) -> ParsedResumeMetadata:
    """Deterministically extracts skills, domains, highest degree, graduation date, and years of experience."""
    text_lower = (content_text or "").lower()

    found_skills: list[str] = []
    for key, label in KNOWN_SKILLS.items():
        pattern = r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])"
        if re.search(pattern, text_lower) and label not in found_skills:
            found_skills.append(label)

    found_domains: list[str] = []
    for key, label in KNOWN_DOMAINS.items():
        if key in text_lower and label not in found_domains:
            found_domains.append(label)

    degrees: list[str] = []
    highest_degree: str | None = None

    if re.search(r"\b(?:ph\.?d\.?|doctor of philosophy|doctorate)\b", text_lower):
        degrees.append("PhD")
        highest_degree = "phd"
    if re.search(r"\b(?:m\.?s\.?|m\.?eng\.?|master(?:'s)? of|mba)\b", text_lower):
        degrees.append("Master's")
        if not highest_degree:
            highest_degree = "master"
    if re.search(r"\b(?:b\.?s\.?|b\.?a\.?|b\.?eng\.?|bachelor(?:'s)? of)\b", text_lower):
        degrees.append("Bachelor's")
        if not highest_degree:
            highest_degree = "bachelor"
    if re.search(r"\b(?:associate(?:'s)? degree|a\.?s\.?)\b", text_lower):
        degrees.append("Associate's")
        if not highest_degree:
            highest_degree = "associate"

    # Graduation date detection (e.g., "Expected May 2027", "Graduated Dec 2025", "Class of 2026")
    grad_date: str | None = None
    month_map = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    grad_match = re.search(
        r"(?:graduat(?:ed|ion|ing)|expected|class of)\s*[:,-]?\s*(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+)?(20\d{2})",
        text_lower,
    )
    if grad_match:
        m_str, yr_str = grad_match.group(1), grad_match.group(2)
        if m_str and m_str[:3] in month_map:
            grad_date = f"{yr_str}-{month_map[m_str[:3]]}"
        else:
            grad_date = f"{yr_str}-05"

    # Explicit years of experience statement (e.g. "5+ years of experience")
    years_exp: float | None = None
    exp_match = re.search(r"(\d+(?:\.\d+)?)\+?\s*years?\s+of\s+(?:\w+\s+)?experience", text_lower)
    if exp_match:
        years_exp = float(exp_match.group(1))

    certifications: list[str] = []
    for cert in ("AWS Certified", "CKA", "CISSP", "Security+", "PMP", "GCP Certified"):
        if cert.lower() in text_lower:
            certifications.append(cert)

    return ParsedResumeMetadata(
        skills=found_skills,
        domains=found_domains,
        degrees=degrees,
        highest_degree_detected=highest_degree,
        graduation_date_detected=grad_date,
        years_experience_est=years_exp,
        certifications=certifications,
    )


def sync_parsed_resume_to_profile(db: Session, parsed: ParsedResumeMetadata) -> UserProfile:
    """Updates unset or mergeable fields on the singleton UserProfile from a parsed resume."""
    profile = db.get(UserProfile, 1)
    if not profile:
        profile = UserProfile(id=1, updated_at=utcnow())
        db.add(profile)

    if parsed.highest_degree_detected:
        current_rank = DEGREE_RANK.get(profile.highest_degree or "none", 0)
        new_rank = DEGREE_RANK.get(parsed.highest_degree_detected, 0)
        if new_rank > current_rank:
            profile.highest_degree = parsed.highest_degree_detected

    if parsed.graduation_date_detected and not profile.graduation_date:
        profile.graduation_date = parsed.graduation_date_detected

    if parsed.years_experience_est is not None and profile.years_experience_total is None:
        profile.years_experience_total = parsed.years_experience_est

    existing_skills: list[str] = []
    if profile.skills_and_domains_json:
        try:
            existing_skills = json.loads(profile.skills_and_domains_json)
        except ValueError:
            existing_skills = []

    combined = list(dict.fromkeys([*existing_skills, *parsed.skills, *parsed.domains]))
    profile.skills_and_domains_json = json.dumps(combined)
    profile.updated_at = utcnow()
    db.commit()
    db.refresh(profile)
    return profile
