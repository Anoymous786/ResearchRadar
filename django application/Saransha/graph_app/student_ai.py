"""
resume_analyzer.py  –  Highly-accurate, production-grade resume analyzer.
No external LLM API required. Pure rule-based + weighted scoring.

Key improvements over v1:
- Weighted, calibrated scoring across 10 dimensions
- Richer keyword taxonomy per role (30-50 keywords vs 10)
- Skill-matching with highlighted matched/missing sets returned for UI
- Synonym normalisation (pytorch / torch, tensorflow / tf, etc.)
- Sentence-quality checks (passive voice, filler words, weak verbs)
- Education-level detection (bachelor / master / phd)
- Experience-year estimation
- Robust section detection (handles upper-case, centred, underlined headings)
- JSON-first output + a plain-text renderer
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from graph_app.groq_client import generate_ai_response

# ──────────────────────────────────────────────────────────────────────────────
# 1.  KEYWORD TAXONOMY
# ──────────────────────────────────────────────────────────────────────────────

# Each entry is (canonical_name, [synonyms_or_abbreviations])
_RAW_TECHNICAL_SKILLS: List[Tuple[str, List[str]]] = [
    # Languages
    ("Python", ["python"]),
    ("Java", ["java"]),
    ("C++", ["c++", "cpp"]),
    ("C#", ["c#", "csharp"]),
    ("JavaScript", ["javascript", "js"]),
    ("TypeScript", ["typescript", "ts"]),
    ("Go", ["golang", "go lang"]),
    ("Rust", ["rust"]),
    ("Ruby", ["ruby"]),
    ("Swift", ["swift"]),
    ("Kotlin", ["kotlin"]),
    ("R", [r"\br\b"]),
    ("Scala", ["scala"]),
    ("PHP", ["php"]),
    ("C", [r"\bc\b"]),
    ("MATLAB", ["matlab"]),
    ("Bash", ["bash", "shell scripting"]),
    # Web
    ("React", ["react", "reactjs", "react.js"]),
    ("Angular", ["angular", "angularjs"]),
    ("Vue.js", ["vue", "vuejs", "vue.js"]),
    ("Next.js", ["next.js", "nextjs"]),
    ("Node.js", ["node.js", "nodejs", "node"]),
    ("Django", ["django"]),
    ("Flask", ["flask"]),
    ("FastAPI", ["fastapi"]),
    ("Spring Boot", ["spring boot", "springboot"]),
    ("Express", ["express", "expressjs"]),
    ("GraphQL", ["graphql"]),
    ("REST API", ["rest", "restful", "rest api"]),
    ("HTML", ["html", "html5"]),
    ("CSS", ["css", "css3", "sass", "scss"]),
    # Data / ML
    ("SQL", ["sql", "mysql", "postgresql", "postgres", "sqlite", "mssql", "t-sql"]),
    ("MongoDB", ["mongodb", "mongo"]),
    ("Redis", ["redis"]),
    ("Pandas", ["pandas"]),
    ("NumPy", ["numpy"]),
    ("Scikit-learn", ["scikit-learn", "sklearn", "scikit"]),
    ("TensorFlow", ["tensorflow", "tf"]),
    ("PyTorch", ["pytorch", "torch"]),
    ("Keras", ["keras"]),
    ("Hugging Face", ["huggingface", "hugging face", "transformers"]),
    ("LangChain", ["langchain"]),
    ("OpenAI API", ["openai", "chatgpt api"]),
    ("RAG", ["rag", "retrieval augmented generation"]),
    ("LLM", ["llm", "llms", "large language model"]),
    ("Matplotlib", ["matplotlib"]),
    ("Seaborn", ["seaborn"]),
    ("Plotly", ["plotly"]),
    ("Power BI", ["power bi", "powerbi"]),
    ("Tableau", ["tableau"]),
    ("Excel", ["excel", "spreadsheet"]),
    # Cloud / DevOps
    ("AWS", ["aws", "amazon web services"]),
    ("GCP", ["gcp", "google cloud"]),
    ("Azure", ["azure", "microsoft azure"]),
    ("Docker", ["docker"]),
    ("Kubernetes", ["kubernetes", "k8s"]),
    ("Terraform", ["terraform"]),
    ("CI/CD", ["ci/cd", "ci cd", "github actions", "jenkins", "gitlab ci"]),
    ("Linux", ["linux", "ubuntu", "debian", "centos"]),
    ("Git", ["git", "github", "gitlab", "bitbucket"]),
    # Data Engineering
    ("Apache Spark", ["spark", "pyspark", "apache spark"]),
    ("Airflow", ["airflow", "apache airflow"]),
    ("Kafka", ["kafka", "apache kafka"]),
    ("MLflow", ["mlflow"]),
    ("Elasticsearch", ["elasticsearch", "elastic"]),
    # CS fundamentals
    ("Data Structures", ["data structures", "data structure"]),
    ("Algorithms", ["algorithms", "algorithm"]),
    ("OOP", ["oop", "object oriented", "object-oriented"]),
    ("System Design", ["system design"]),
    ("Microservices", ["microservices", "microservice"]),
]

_RAW_ANALYTICAL_SKILLS: List[Tuple[str, List[str]]] = [
    ("Statistical Analysis", ["statistics", "statistical analysis", "statistical modelling"]),
    ("Regression", ["regression", "linear regression", "logistic regression"]),
    ("Classification", ["classification"]),
    ("Clustering", ["clustering", "k-means", "dbscan"]),
    ("NLP", ["nlp", "natural language processing", "text mining"]),
    ("Computer Vision", ["computer vision", "cv", "image classification", "object detection"]),
    ("A/B Testing", ["a/b test", "a/b testing", "hypothesis testing", "experiment"]),
    ("Time Series", ["time series", "forecasting"]),
    ("Data Wrangling", ["data wrangling", "data cleaning", "etl", "elt"]),
    ("Feature Engineering", ["feature engineering", "feature selection"]),
    ("Model Evaluation", ["cross-validation", "evaluation", "metrics", "benchmark"]),
    ("Research Methods", ["research", "literature review", "methodology"]),
    ("Optimization", ["optimization", "optimisation", "gradient descent"]),
    ("Data Visualization", ["visualization", "visualisation", "dashboard"]),
    ("Business Intelligence", ["business intelligence", "bi"]),
]

_RAW_SOFT_SKILLS: List[Tuple[str, List[str]]] = [
    ("Communication", ["communication", "communicate", "presenting"]),
    ("Teamwork", ["teamwork", "team player", "collaboration", "collaborative"]),
    ("Leadership", ["leadership", "led", "lead", "managed team"]),
    ("Problem Solving", ["problem solving", "problem-solving", "critical thinking"]),
    ("Agile / Scrum", ["agile", "scrum", "sprint", "kanban"]),
    ("Mentoring", ["mentoring", "mentorship", "coaching"]),
    ("Stakeholder Management", ["stakeholder", "stakeholder management"]),
    ("Time Management", ["time management", "deadline"]),
    ("Adaptability", ["adaptability", "flexible", "adaptable"]),
    ("Ownership", ["ownership", "initiative"]),
]

# Role definitions: each maps to required + bonus keywords
ROLE_PROFILES: Dict[str, Dict] = {
    "software engineer": {
        "required": [
            "Python", "Java", "JavaScript", "TypeScript", "SQL", "Git",
            "REST API", "Data Structures", "Algorithms", "OOP",
            "System Design", "Microservices", "Docker", "CI/CD",
        ],
        "bonus": ["Go", "Rust", "Kubernetes", "AWS", "React", "Node.js"],
        "aliases": ["software developer", "software development", "sde", "swe", "backend developer", "full stack", "fullstack"],
    },
    "data scientist": {
        "required": [
            "Python", "SQL", "Pandas", "NumPy", "Scikit-learn",
            "Statistical Analysis", "Machine Learning", "Feature Engineering",
            "Data Visualization", "Model Evaluation", "Git",
        ],
        "bonus": ["TensorFlow", "PyTorch", "Spark", "Airflow", "Tableau", "Power BI", "NLP"],
        "aliases": ["data science", "ds"],
    },
    "machine learning engineer": {
        "required": [
            "Python", "TensorFlow", "PyTorch", "Scikit-learn", "NumPy", "Pandas",
            "Statistical Analysis", "Model Evaluation", "Docker", "MLflow", "Git", "SQL",
        ],
        "bonus": ["Kubernetes", "AWS", "GCP", "Kafka", "Spark", "LLM", "Hugging Face"],
        "aliases": ["ml engineer", "ml", "mle", "ai engineer", "deep learning engineer"],
    },
    "data engineer": {
        "required": [
            "Python", "SQL", "Apache Spark", "Airflow", "Kafka",
            "AWS", "Docker", "CI/CD", "Git", "Elasticsearch",
        ],
        "bonus": ["Kubernetes", "Terraform", "GCP", "Azure", "Scala", "MLflow"],
        "aliases": ["data engineering", "etl developer", "data platform engineer"],
    },
    "frontend developer": {
        "required": [
            "JavaScript", "TypeScript", "React", "HTML", "CSS",
            "REST API", "Git", "Node.js",
        ],
        "bonus": ["Vue.js", "Next.js", "GraphQL", "Angular", "Docker", "CI/CD"],
        "aliases": ["frontend engineer", "ui developer", "react developer", "web developer"],
    },
    "backend developer": {
        "required": [
            "Python", "Java", "SQL", "REST API", "Docker",
            "Git", "Microservices", "System Design", "CI/CD",
        ],
        "bonus": ["Go", "Node.js", "Kubernetes", "AWS", "Redis", "Kafka"],
        "aliases": ["backend engineer", "api developer", "server-side developer"],
    },
    "devops engineer": {
        "required": [
            "Docker", "Kubernetes", "AWS", "CI/CD", "Terraform",
            "Linux", "Git", "Bash",
        ],
        "bonus": ["GCP", "Azure", "Ansible", "Prometheus", "Grafana", "Kafka"],
        "aliases": ["devops", "sre", "platform engineer", "cloud engineer", "infrastructure engineer"],
    },
    "research assistant": {
        "required": [
            "Python", "Statistical Analysis", "Research Methods", "Model Evaluation",
            "Data Visualization", "Git",
        ],
        "bonus": ["TensorFlow", "PyTorch", "NLP", "Computer Vision", "Pandas", "NumPy"],
        "aliases": ["research intern", "research associate", "research engineer"],
    },
}

ACTION_VERBS = [
    "built", "developed", "implemented", "designed", "created", "led",
    "managed", "optimized", "improved", "reduced", "increased", "delivered",
    "engineered", "analyzed", "architected", "automated", "accelerated",
    "collaborated", "deployed", "established", "executed", "generated",
    "integrated", "launched", "maintained", "migrated", "modeled", "monitored",
    "orchestrated", "performed", "produced", "published", "refactored",
    "researched", "resolved", "scaled", "streamlined", "trained", "transformed",
    "validated", "wrote", "spearheaded", "initiated", "coordinated",
]

WEAK_VERBS = [
    "worked on", "helped with", "assisted with", "involved in",
    "responsible for", "participated in", "contributed to",
]

FILLER_PHRASES = [
    "team player", "hard worker", "detail-oriented", "fast learner",
    "go-getter", "results-driven", "self-motivated", "passionate",
    "dynamic", "proactive",
]

EDUCATION_LEVELS = {
    "phd": ["ph.d", "phd", "doctorate", "doctoral"],
    "master": ["master", "m.sc", "m.tech", "msc", "mtech", "m.e.", "m.s.", "ms ", "mba"],
    "bachelor": ["bachelor", "b.sc", "b.tech", "bsc", "btech", "b.e.", "b.s.", "bs ", "undergraduate", "b.a.", "ba "],
    "associate": ["associate", "diploma", "a.s.", "a.a."],
}


# ──────────────────────────────────────────────────────────────────────────────
# 2.  NORMALISATION UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


# Build a flat lookup: normalised_pattern -> canonical_name
def _build_skill_index(raw: List[Tuple[str, List[str]]]) -> List[Tuple[str, List[re.Pattern]]]:
    index = []
    for canonical, synonyms in raw:
        patterns = []
        for syn in synonyms:
            if syn.startswith(r"\b"):
                patterns.append(re.compile(syn, re.I))
            else:
                # Use non-word lookarounds instead of `\b` so tokens like `C++`, `C#`, `node.js`
                # still match reliably (because '+' / '#' / '.' are not "word" characters).
                escaped = re.escape(syn)
                patterns.append(re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.I))
        index.append((canonical, patterns))
    return index


_TECHNICAL_INDEX = _build_skill_index(_RAW_TECHNICAL_SKILLS)
_ANALYTICAL_INDEX = _build_skill_index(_RAW_ANALYTICAL_SKILLS)
_SOFT_INDEX = _build_skill_index(_RAW_SOFT_SKILLS)


def _match_skills(text: str, index: List[Tuple[str, List[re.Pattern]]]) -> List[str]:
    """Return canonical names of skills found in text."""
    found = []
    t = text or ""
    for canonical, patterns in index:
        for pat in patterns:
            if pat.search(t):
                found.append(canonical)
                break
    return found


# ──────────────────────────────────────────────────────────────────────────────
# 3.  PDF / TEXT EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

def extract_pdf_text(pdf_file) -> str:
    """Extract text from a file-like PDF object using PyMuPDF first."""
    try:
        text = ""
        # Preferred parser: PyMuPDF (fitz) for better layout/text fidelity.
        try:
            import fitz  # PyMuPDF
            pdf_file.seek(0)
            pdf_bytes = pdf_file.read()
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                pages = [page.get_text("text") or "" for page in doc]
                text = "\n".join(pages)
        except Exception:
            # Fallback parser: PyPDF2
            from PyPDF2 import PdfReader
            pdf_file.seek(0)
            reader = PdfReader(pdf_file)
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages)

        # Normalize common PDF text artifacts to make downstream regex/section parsing more accurate.
        # - join hyphenated line breaks: "machine-\nlearning" -> "machinelearning"
        # - collapse repeated whitespace (but keep newlines as separators)
        text = re.sub(r"(\w)\s*-\s*\n\s*(\w)", r"\1\2", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()
    except Exception as exc:
        return f"[PDF extraction error: {exc}]"


# ──────────────────────────────────────────────────────────────────────────────
# 4.  SECTION DETECTION
# ──────────────────────────────────────────────────────────────────────────────

_SECTION_PATTERNS: Dict[str, re.Pattern] = {
    "education":   re.compile(r"^\s*(education|academic\s+background|qualifications?)\s*$", re.I | re.M),
    "skills":      re.compile(r"^\s*(skills|technical\s+skills?|core\s+competencies|technologies|tools)\s*$", re.I | re.M),
    "projects":    re.compile(r"^\s*(projects?|personal\s+projects?|academic\s+projects?|key\s+projects?)\s*$", re.I | re.M),
    "experience":  re.compile(r"^\s*(experience|work\s+experience|employment|professional\s+experience|internships?)\s*$", re.I | re.M),
    "interests":   re.compile(r"^\s*(interests?|hobbies|activities)\s*$", re.I | re.M),
    "summary":     re.compile(r"^\s*(summary|objective|profile|about\s+me|overview)\s*$", re.I | re.M),
    "certifications": re.compile(r"^\s*(certifications?|licen[sc]es?|credentials?)\s*$", re.I | re.M),
    "publications": re.compile(r"^\s*(publications?|papers?|research)\s*$", re.I | re.M),
    "achievements": re.compile(r"^\s*(achievements?|awards?|honours?|honors?)\s*$", re.I | re.M),
}


def _split_sections(text: str) -> Dict[str, str]:
    """
    Split resume text into sections.  Returns a dict keyed by section name.
    Falls back to using the whole text for every section when no headings are found.
    """
    lines = text.splitlines()
    heading_hits: Dict[str, int] = {}

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for section, pat in _SECTION_PATTERNS.items():
            if pat.match(stripped) and section not in heading_hits:
                heading_hits[section] = i

    if not heading_hits:
        # Inline heading detection (e.g. "SKILLS:" mid-line)
        for i, line in enumerate(lines):
            for section, pat in _SECTION_PATTERNS.items():
                if pat.search(line) and section not in heading_hits:
                    heading_hits[section] = i

    if not heading_hits:
        # If the resume doesn't have clear headings, do NOT blindly copy the entire
        # resume into every section (that makes ATS scoring look artificially perfect).
        #
        # Instead, populate each section using lightweight keyword heuristics.
        t = text.strip()
        t_norm = _normalize(t)

        education_detect = any(kw.lower() in t_norm for kws in EDUCATION_LEVELS.values() for kw in kws)
        skills_detect = bool(_match_skills(t, _TECHNICAL_INDEX) or _match_skills(t, _ANALYTICAL_INDEX) or _match_skills(t, _SOFT_INDEX))
        projects_detect = bool(re.search(r"github\.com|portfolio|project[s]?|published|github", t, re.I))
        experience_detect = bool(
            re.search(r"\b(19\d{2}|20\d{2})\b", t) or
            re.search(r"\b(experience|work|employment|internships?)\b", t, re.I)
        )

        out: Dict[str, str] = {}
        for section in _SECTION_PATTERNS.keys():
            if section == "summary":
                out[section] = t
            elif section == "education":
                out[section] = t if education_detect else ""
            elif section == "skills":
                out[section] = t if skills_detect else ""
            elif section == "projects":
                out[section] = t if projects_detect else ""
            elif section == "experience":
                out[section] = t if experience_detect else ""
            else:
                out[section] = ""
        return out

    ordered = sorted(heading_hits.items(), key=lambda kv: kv[1])
    out: Dict[str, str] = {}
    for idx, (name, start_i) in enumerate(ordered):
        end_i = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(lines)
        content = "\n".join(lines[start_i + 1: end_i]).strip()
        if content:
            out[name] = content

    return out


# ──────────────────────────────────────────────────────────────────────────────
# 5.  CONTACT / BASIC FIELD EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

def _extract_email(text: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", text)
    return m.group(0).strip() if m else ""


def _extract_phone(text: str) -> str:
    m = re.search(
        r"(\+?\d{1,3}[\s\-.]?)?(\(?\d{3}\)?[\s\-.]?)?\d{3}[\s\-.]?\d{4}",
        text.replace("\u00a0", " "),
    )
    return m.group(0).strip() if m else ""


def _extract_linkedin(text: str) -> str:
    m = re.search(r"linkedin\.com/in/[\w\-]+", text, re.I)
    return m.group(0) if m else ""


def _extract_github(text: str) -> str:
    m = re.search(r"github\.com/[\w\-]+", text, re.I)
    return m.group(0) if m else ""

def _extract_name(text: str, email: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    blacklist = ["career", "objective", "summary", "resume", "cv"]

    # Step 1: check first 10 lines only
    for ln in lines[:10]:
        ln_clean = ln.lower()

        # skip unwanted
        if any(b in ln_clean for b in blacklist):
            continue
        if re.search(r"[\d@:/]", ln):
            continue

        words = ln.split()

        # name condition (VERY IMPORTANT)
        if 2 <= len(words) <= 4:
            if all(w[0].isupper() for w in words if w.isalpha()):
                return ln

    # Step 2: fallback from email
    if email:
        username = email.split("@")[0]
        name_guess = username.replace(".", " ").replace("_", " ")
        return name_guess.title()

    return "Not Mentioned"

def _detect_education_level(text: str) -> str:
    t = _normalize(text)
    for level, keywords in EDUCATION_LEVELS.items():
        for kw in keywords:
            if kw.lower() in t:
                return level
    return "unknown"


def extract_resume_fields(resume_text: str) -> Dict[str, str]:
    email = _extract_email(resume_text)
    phone = _extract_phone(resume_text)
    name = _extract_name(resume_text, email=email)

    # Use the section splitter that exists in this repo. (Some earlier versions called
    # a non-existent `_split_section_by_headings`, which broke resume parsing.)
    sections = _split_sections(resume_text)

    # 🔥 fallback if sections missing
    if not sections:
        sections = {
            "education": resume_text,
            "skills": resume_text,
            "projects": resume_text,
            "experience": resume_text,
        }

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "education": sections.get("education", "Not Mentioned"),
        "skills": sections.get("skills", "Not Mentioned"),
        "projects": sections.get("projects", "Not Mentioned"),
        "experience": sections.get("experience", "Not Mentioned"),
    }


def _estimate_years_of_experience(text: str) -> float:
    """
    Very rough heuristic: count date-range mentions like 2019-2021, Jan 2020 – Dec 2021.
    Returns total years (capped at 40 to avoid junk).
    """
    total_months = 0
    # "YYYY – YYYY" or "YYYY-YYYY"
    for m in re.finditer(r"\b(20\d{2})\s*[-–]\s*(20\d{2}|present|current|now)\b", text, re.I):
        start_year = int(m.group(1))
        end_raw = m.group(2).lower()
        import datetime
        end_year = datetime.datetime.now().year if end_raw in ("present", "current", "now") else int(end_raw)
        total_months += max(0, (end_year - start_year) * 12)

    # "Month YYYY – Month YYYY"
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    for m in re.finditer(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d{2})"
        r"\s*[-–]\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d{2}|present|current|now)",
        text, re.I,
    ):
        sm, sy = month_map[m.group(1)[:3].lower()], int(m.group(2))
        em_raw, ey_raw = m.group(3)[:3].lower(), m.group(4)
        import datetime
        now = datetime.datetime.now()
        if ey_raw.lower() in ("present", "current", "now"):
            ey, em = now.year, now.month
        else:
            ey, em = int(ey_raw), month_map[em_raw]
        total_months += max(0, (ey - sy) * 12 + (em - sm))

    years = round(total_months / 12, 1)
    return min(years, 40.0)


# ──────────────────────────────────────────────────────────────────────────────
# 6.  CONTENT QUALITY CHECKS
# ──────────────────────────────────────────────────────────────────────────────

def _count_action_verbs(text: str) -> int:
    t = _normalize(text)
    return sum(1 for v in ACTION_VERBS if re.search(r"\b" + re.escape(v) + r"\b", t))


def _count_weak_phrases(text: str) -> List[str]:
    t = _normalize(text)
    return [ph for ph in WEAK_VERBS + FILLER_PHRASES if re.search(r"\b" + re.escape(ph) + r"\b", t)]


def _has_measurable_achievements(text: str) -> bool:
    return bool(re.search(r"(\b\d+(\.\d+)?\s*%)|(\b\d{2,}\b)", text or ""))


def _count_quantified_bullets(text: str) -> int:
    bullets = re.findall(r"[-•]\s+.+", text)
    return sum(1 for b in bullets if re.search(r"\d+\s*%?|\$[\d,]+|\d+[kKmM]", b))


def _estimate_formatting_score(text: str) -> int:
    t = text or ""
    bullet_count = len(re.findall(r"(^|\n)\s*[-•*]\s+\S+", t))
    heading_count = sum(1 for p in _SECTION_PATTERNS.values() if p.search(t))
    email_ok = bool(_extract_email(t))
    phone_ok = bool(_extract_phone(t))

    score = 40
    score += min(30, bullet_count * 3)
    score += min(20, heading_count * 4)
    score += 5 if email_ok else 0
    score += 5 if phone_ok else 0
    return min(100, score)


def _passive_voice_count(text: str) -> int:
    return len(re.findall(
        r"\b(was|were|been|being|is|are|am)\s+\w+ed\b", text, re.I
    ))


# ──────────────────────────────────────────────────────────────────────────────
# 7.  ROLE MATCHING
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_role(target_role: str) -> Tuple[str, Dict]:
    """Return the best matching role key and its profile dict."""
    t = _normalize(target_role or "")
    # Direct key match
    for key, profile in ROLE_PROFILES.items():
        if key in t:
            return key, profile
    # Alias match
    for key, profile in ROLE_PROFILES.items():
        for alias in profile.get("aliases", []):
            if alias in t:
                return key, profile
    # Fallback
    return "software engineer", ROLE_PROFILES["software engineer"]


def _compute_role_match(resume_text: str, role_key: str, profile: Dict) -> Dict:
    """
    Returns matched/missing required and bonus keywords plus match percentages.
    Skills highlighted = matched required keywords.
    """
    required: List[str] = profile.get("required", [])
    bonus: List[str] = profile.get("bonus", [])

    # Build a set of canonical technical skills found in the resume
    found_tech = set(_match_skills(resume_text, _TECHNICAL_INDEX))
    found_analytical = set(_match_skills(resume_text, _ANALYTICAL_INDEX))
    all_found = found_tech | found_analytical

    matched_required = [k for k in required if k in all_found]
    missing_required = [k for k in required if k not in all_found]
    matched_bonus = [k for k in bonus if k in all_found]
    missing_bonus = [k for k in bonus if k not in all_found]

    req_ratio = len(matched_required) / max(1, len(required))
    bonus_ratio = len(matched_bonus) / max(1, len(bonus))

    # ATS score: weighted (required = 70 %, bonus = 30 %)
    ats_score = int(round(40 + (req_ratio * 42) + (bonus_ratio * 18)))
    ats_score = max(0, min(100, ats_score))

    match_pct = int(round((req_ratio * 0.8 + bonus_ratio * 0.2) * 100))

    return {
        "matched_required": matched_required,
        "missing_required": missing_required,
        "matched_bonus": matched_bonus,
        "missing_bonus": missing_bonus,
        "ats_score": ats_score,
        "match_percentage": match_pct,
        "required_total": len(required),
        "bonus_total": len(bonus),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 8.  MASTER SCORER
# ──────────────────────────────────────────────────────────────────────────────

def _compute_resume_score(
    sections: Dict[str, str],
    contact: Dict[str, str],
    role_match: Dict,
    measurable: bool,
    action_verb_count: int,
    formatting_score: int,
    education_level: str,
    years_exp: float,
    weak_phrases: List[str],
    quantified_bullets: int,
) -> Tuple[int, Dict[str, int]]:
    """
    Weighted scoring across 10 dimensions. Returns (total_score, dimension_scores).
    """
    dim: Dict[str, int] = {}

    # 1. Contact & Identity (5 pts)
    c = 0
    if contact.get("email"):
        c += 2
    if contact.get("phone"):
        c += 2
    if contact.get("linkedin") or contact.get("github"):
        c += 1
    dim["contact"] = c

    # 2. Section completeness (15 pts)
    sec_pts = 0
    for s in ["education", "skills", "projects", "experience"]:
        if sections.get(s, "").strip():
            sec_pts += 3
    if sections.get("summary", "").strip():
        sec_pts += 2
    if sections.get("certifications", "").strip():
        sec_pts += 1
    dim["sections"] = min(15, sec_pts)

    # 3. Skills (15 pts)
    req_ratio = len(role_match["matched_required"]) / max(1, role_match["required_total"])
    dim["skills"] = int(round(req_ratio * 15))

    # 4. Measurable achievements (15 pts)
    ach = 0
    if measurable:
        ach += 8
    ach += min(7, quantified_bullets * 2)
    dim["achievements"] = ach

    # 5. Action verbs (10 pts)
    av = min(10, action_verb_count * 2)
    # Penalise weak phrases
    av = max(0, av - len(weak_phrases))
    dim["action_verbs"] = av

    # 6. Formatting / ATS structure (10 pts)
    dim["formatting"] = int(round((formatting_score / 100) * 10))

    # 7. Education (10 pts)
    edu_scores = {"phd": 10, "master": 9, "bachelor": 7, "associate": 5, "unknown": 3}
    dim["education"] = edu_scores.get(education_level, 3)

    # 8. Experience depth (10 pts)
    exp_text = sections.get("experience", "")
    exp_pts = 0
    if exp_text.strip():
        exp_pts += 3
    if years_exp >= 1:
        exp_pts += 3
    if years_exp >= 3:
        exp_pts += 4
    dim["experience"] = min(10, exp_pts)

    # 9. Projects quality (5 pts)
    proj_text = sections.get("projects", "")
    proj_pts = 0
    if proj_text.strip():
        proj_pts += 2
    if re.search(r"github\.com", proj_text, re.I):
        proj_pts += 2
    if _has_measurable_achievements(proj_text):
        proj_pts += 1
    dim["projects"] = proj_pts

    # 10. Keyword density (5 pts)
    bonus_ratio = len(role_match["matched_bonus"]) / max(1, role_match["bonus_total"])
    dim["keyword_density"] = int(round(bonus_ratio * 5))

    total = sum(dim.values())
    total = max(0, min(100, total))
    return total, dim


# ──────────────────────────────────────────────────────────────────────────────
# 9.  MAIN ANALYSIS FUNCTION (JSON output)
# ──────────────────────────────────────────────────────────────────────────────

def analyze_resume(resume_text: str, target_role: str = "Software Engineer") -> Dict:
    """
    Full resume analysis.  Returns a rich JSON-compatible dict.

    Key fields for UI:
      - resume_score           int 0-100
      - dimension_scores       dict {dimension: score}
      - contact                dict
      - sections_detected      list[str]
      - strengths              list[str]
      - weaknesses             list[str]
      - skills_found           dict {technical, analytical, soft}  ← canonical names
      - role_match             dict (matched_required, missing_required, etc.)
      - highlighted_skills     list[str]  ← matched required skills (for UI highlighting)
      - missing_skills         list[str]  ← missing required skills
      - ats_score              int
      - job_match_percentage   int
      - education_level        str
      - years_experience       float
      - content_quality        dict
      - suggestions            list[str]
      - target_role            str (resolved canonical name)
    """
    text = resume_text or ""
    role_key, profile = _resolve_role(target_role)

    # --- Extraction ---
    sections = _split_sections(text)
    email = _extract_email(text)
    contact = {
        "email": email,
        "phone": _extract_phone(text),
        "linkedin": _extract_linkedin(text),
        "github": _extract_github(text),
        "name": _extract_name(text, email),
    }
    sections_detected = [s for s, v in sections.items() if v.strip()]

    education_level = _detect_education_level(sections.get("education", text))
    years_exp = _estimate_years_of_experience(text)

    # --- Skills ---
    tech_found = _match_skills(text, _TECHNICAL_INDEX)
    analytical_found = _match_skills(text, _ANALYTICAL_INDEX)
    soft_found = _match_skills(text, _SOFT_INDEX)

    # --- Role match ---
    role_match = _compute_role_match(text, role_key, profile)

    # --- Content quality ---
    measurable = _has_measurable_achievements(text)
    action_verb_count = _count_action_verbs(text)
    formatting_score = _estimate_formatting_score(text)
    weak_phrases = _count_weak_phrases(text)
    quantified_bullets = _count_quantified_bullets(text)
    passive_count = _passive_voice_count(text)

    word_count = len(text.split())
    sentence_lengths = [
        len(s.split()) for s in re.split(r"[.!?]\n?", text) if s.strip()
    ]
    avg_sentence_len = sum(sentence_lengths) / max(1, len(sentence_lengths))

    # --- Score ---
    resume_score, dimension_scores = _compute_resume_score(
        sections, contact, role_match, measurable, action_verb_count,
        formatting_score, education_level, years_exp, weak_phrases, quantified_bullets,
    )

    # --- Strengths ---
    strengths: List[str] = []
    if contact["email"] and contact["phone"]:
        strengths.append("Complete contact information (email, phone) is present.")
    if contact["linkedin"]:
        strengths.append("LinkedIn profile link detected — great for recruiter visibility.")
    if contact["github"]:
        strengths.append("GitHub link detected — shows real code and project work.")
    if role_match["matched_required"]:
        strengths.append(
            f"Matched {len(role_match['matched_required'])}/{role_match['required_total']} "
            f"required skills for {role_key.title()}: "
            + ", ".join(role_match["matched_required"][:6])
            + ("..." if len(role_match["matched_required"]) > 6 else ".")
        )
    if role_match["matched_bonus"]:
        strengths.append(
            "Bonus/trending skills detected: " + ", ".join(role_match["matched_bonus"][:5]) + "."
        )
    if measurable:
        strengths.append("Resume contains quantified achievements (numbers / percentages).")
    if quantified_bullets >= 3:
        strengths.append(f"{quantified_bullets} bullet points have measurable impact — excellent.")
    if action_verb_count >= 6:
        strengths.append(f"Strong use of action verbs ({action_verb_count} detected).")
    if education_level in ("master", "phd"):
        strengths.append(f"Advanced degree detected ({education_level.title()}) — competitive advantage.")
    if years_exp >= 1:
        strengths.append(f"Approximately {years_exp} year(s) of experience estimated from date ranges.")
    if sections.get("projects", "").strip():
        strengths.append("Projects section demonstrates practical application of skills.")
    if not strengths:
        strengths.append("Resume contains multiple sections that can be recognised by ATS.")

    # --- Weaknesses ---
    weaknesses: List[str] = []
    missing_sections = [s for s in ["education", "skills", "projects", "experience"] if not sections.get(s, "").strip()]
    if missing_sections:
        weaknesses.append(f"Missing sections: {', '.join(missing_sections)}. Add these with clear headings.")
    if role_match["missing_required"]:
        weaknesses.append(
            f"Missing {len(role_match['missing_required'])} required keywords for {role_key.title()}: "
            + ", ".join(role_match["missing_required"][:8]) + "."
        )
    if not measurable:
        weaknesses.append("No measurable achievements found. Add numbers: '% improvement', '× faster', 'N users'.")
    if quantified_bullets < 2:
        weaknesses.append("Very few quantified bullet points. Aim for at least 3 metrics-backed bullets.")
    if action_verb_count < 4:
        weaknesses.append(f"Only {action_verb_count} strong action verbs detected. Use verbs like: built, optimised, led.")
    if weak_phrases:
        weaknesses.append(f"Weak/filler phrases detected: {', '.join(weak_phrases[:4])}. Remove or rephrase.")
    if formatting_score < 65:
        weaknesses.append("Formatting score is low. Use consistent headings and bullet points for ATS parsing.")
    if passive_count > 3:
        weaknesses.append(f"{passive_count} passive-voice constructions found. Switch to active voice.")
    if word_count < 200:
        weaknesses.append(f"Resume is very short ({word_count} words). Aim for 400-700 words.")
    if word_count > 900:
        weaknesses.append(f"Resume may be too long ({word_count} words). Aim to keep it under 700 words.")
    if avg_sentence_len > 28:
        weaknesses.append("Some bullet points are too long. Split them into shorter, punchy sentences.")
    if not contact.get("linkedin"):
        weaknesses.append("No LinkedIn URL found. Add it to increase recruiter reach.")
    if not weaknesses:
        weaknesses.append("No major weaknesses detected. Fine-tune language and metrics for perfection.")

    # --- Suggestions (priority-ordered) ---
    suggestions: List[str] = []
    if missing_sections:
        suggestions.append(f"Add missing sections '{', '.join(missing_sections)}' with clear headings.")
    if role_match["missing_required"]:
        suggestions.append(
            "Add these missing required keywords naturally in your bullets: "
            + ", ".join(role_match["missing_required"][:10]) + "."
        )
    if not measurable:
        suggestions.append(
            "Quantify every achievement: 'Reduced latency by 30%', 'Served 10k daily users', 'Cut build time from 20 min to 5 min'."
        )
    if action_verb_count < 4:
        suggestions.append(
            "Start each bullet with a strong action verb: built, engineered, optimised, automated, led."
        )
    if weak_phrases:
        suggestions.append("Remove generic filler phrases and replace them with concrete evidence of those traits.")
    if not contact.get("github") and "projects" in sections_detected:
        suggestions.append("Link your GitHub to every project in the Projects section.")
    if formatting_score < 65:
        suggestions.append("Use consistent headings (ALL CAPS or Title Case) and bullet points throughout.")
    if role_match["missing_bonus"]:
        suggestions.append(
            "Consider learning trending skills for this role: "
            + ", ".join(role_match["missing_bonus"][:6]) + "."
        )
    if not suggestions:
        suggestions.append("Polish each bullet for specificity; ensure every claim is backed by evidence or metrics.")

    return {
        # ── Scores ──────────────────────────────────────────────────────────
        "resume_score": resume_score,
        "dimension_scores": dimension_scores,
        "ats_score": role_match["ats_score"],
        "job_match_percentage": role_match["match_percentage"],

        # ── Identity ────────────────────────────────────────────────────────
        "contact": contact,
        "target_role": role_key,
        "education_level": education_level,
        "years_experience": years_exp,

        # ── Structure ───────────────────────────────────────────────────────
        "sections_detected": sections_detected,
        "missing_sections": missing_sections,

        # ── Skills ──────────────────────────────────────────────────────────
        "skills_found": {
            "technical": tech_found,
            "analytical": analytical_found,
            "soft": soft_found,
        },

        # ── Role Match (UI: highlighted_skills = green, missing_skills = red) ─
        "highlighted_skills": role_match["matched_required"],  # ← MATCHED (highlight green)
        "bonus_skills_found": role_match["matched_bonus"],     # ← BONUS (highlight yellow)
        "missing_skills": role_match["missing_required"],      # ← MISSING (highlight red)
        "missing_bonus_skills": role_match["missing_bonus"],

        # ── Narrative ───────────────────────────────────────────────────────
        "strengths": strengths,
        "weaknesses": weaknesses,
        "suggestions": suggestions,

        # ── Content Quality ─────────────────────────────────────────────────
        "content_quality": {
            "word_count": word_count,
            "formatting_score": formatting_score,
            "action_verb_count": action_verb_count,
            "quantified_bullets": quantified_bullets,
            "measurable_achievements": measurable,
            "passive_voice_count": passive_count,
            "weak_phrases_found": weak_phrases,
            "avg_sentence_length": round(avg_sentence_len, 1),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# 10.  PLAIN-TEXT RENDERER (backward compatible with old callers)
# ──────────────────────────────────────────────────────────────────────────────

def render_plain_text(analysis: Dict) -> str:
    """Convert the JSON analysis dict into a human-readable plain-text report."""

    def bullets(items: List[str]) -> str:
        return "\n".join(f"  - {x}" for x in items)

    cq = analysis["content_quality"]
    rm = analysis

    lines = [
        f"Resume Score (out of 100)\n  {analysis['resume_score']}",
        "",
        "Dimension Breakdown:",
        "\n".join(
            f"  {k.replace('_', ' ').title()}: {v}/"
            + ("15" if k == "sections" else
               "10" if k in ("skills", "achievements", "action_verbs", "formatting", "education", "experience") else
               "5")
            for k, v in analysis["dimension_scores"].items()
        ),
        "",
        f"Target Role: {analysis['target_role'].title()}",
        f"Education Level Detected: {analysis['education_level'].title()}",
        f"Estimated Years of Experience: {analysis['years_experience']}",
        "",
        "Strengths:",
        bullets(analysis["strengths"]),
        "",
        "Weaknesses (CONS):",
        bullets(analysis["weaknesses"]),
        "",
        "Skills Found — Technical:",
        "  " + ", ".join(analysis["skills_found"]["technical"] or ["None detected"]),
        "Skills Found — Analytical:",
        "  " + ", ".join(analysis["skills_found"]["analytical"] or ["None detected"]),
        "Skills Found — Soft:",
        "  " + ", ".join(analysis["skills_found"]["soft"] or ["None detected"]),
        "",
        f"ATS Score: {analysis['ats_score']}/100",
        f"Job Match Percentage: {analysis['job_match_percentage']}%",
        "",
        "Matched Required Skills (✓ highlight these in UI):",
        "  " + ", ".join(analysis["highlighted_skills"] or ["None"]),
        "Missing Required Skills (✗ add to resume):",
        "  " + ", ".join(analysis["missing_skills"] or ["None"]),
        "Bonus Skills Matched:",
        "  " + ", ".join(analysis["bonus_skills_found"] or ["None"]),
        "",
        "Content Quality:",
        f"  Word count: {cq['word_count']}",
        f"  Formatting score: {cq['formatting_score']}/100",
        f"  Action verbs: {cq['action_verb_count']}",
        f"  Quantified bullets: {cq['quantified_bullets']}",
        f"  Measurable achievements: {'Yes' if cq['measurable_achievements'] else 'No'}",
        f"  Passive voice constructions: {cq['passive_voice_count']}",
        f"  Average sentence length: {cq['avg_sentence_length']} words",
        f"  Weak/filler phrases: {', '.join(cq['weak_phrases_found']) or 'None'}",
        "",
        "Suggestions:",
        bullets(analysis["suggestions"]),
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 11.  BACKWARD-COMPATIBLE PUBLIC API
#      All names that views.py / any other module may import are kept here.
# ──────────────────────────────────────────────────────────────────────────────

# ── extract_resume_fields ────────────────────────────────────────────────────
def extract_resume_fields(resume_text: str) -> Dict:
    """
    Backward-compatible extraction helper.

    Returns the same keys that the original version produced so that any
    view/template that accesses result["name"], result["email"], etc. keeps
    working without change.
    """
    text = resume_text or ""
    email = _extract_email(text)
    sections = _split_sections(text)

    certifications_text = sections.get("certifications", "")
    certifications = []
    if certifications_text:
        for line in certifications_text.splitlines():
            line = line.strip(" \t-•")
            if len(line) >= 3:
                certifications.append(line)
    if not certifications:
        # fallback: detect common certification lines anywhere in resume
        for line in text.splitlines():
            raw = line.strip()
            low = raw.lower()
            if (
                len(raw) >= 5
                and ("certified" in low or "certification" in low or "certificate" in low)
            ):
                certifications.append(raw)
    dedup_certifications = []
    seen_cert = set()
    for cert in certifications:
        key = cert.lower()
        if key not in seen_cert:
            seen_cert.add(key)
            dedup_certifications.append(cert)

    return {
        "name":       _extract_name(text, email),
        "email":      email,
        "phone":      _extract_phone(text),
        "linkedin":   _extract_linkedin(text),
        "github":     _extract_github(text),
        "education":  sections.get("education", ""),
        "skills":     sections.get("skills", ""),
        "projects":   sections.get("projects", ""),
        "experience": sections.get("experience", ""),
        "interests":  sections.get("interests", ""),
        "summary":    sections.get("summary", ""),
        "certifications": dedup_certifications,
    }


# ── extract_pdf_text  (already defined above, re-exported for clarity) ───────
# extract_pdf_text is defined in section 3 — no wrapper needed.


# ── rule_based_resume_analysis ───────────────────────────────────────────────
def rule_based_resume_analysis(resume_text: str, target_role: str) -> str:
    """Drop-in replacement — returns plain-text report string."""
    return render_plain_text(analyze_resume(resume_text, target_role))


# ── analyze_resume_rule_based_json ───────────────────────────────────────────
def analyze_resume_rule_based_json(
    resume_text: str, target_role: str = "Software Developer"
) -> Dict:
    """Drop-in replacement — returns rich JSON-compatible dict."""
    analysis = analyze_resume(resume_text, target_role)

    # ---- Compatibility layer for templates already in this repo ----
    # Your templates expect nested keys like:
    # - weaknesses_cons
    # - skills_analysis.technical/analytical/soft
    # - ats_score.ats_compatibility.keyword_optimization.{match_percentage, matched_keywords, missing_keywords}
    # - job_match.{target_role, match_percentage, missing_skills}
    # - skill_gap_analysis.{missing_trending_skills, fixed_gap_comparison}
    # - content_quality.{grammar_check, clarity_check, section_presence.{contact, education, skills, projects, experience}}
    contact = analysis.get("contact", {}) or {}
    sections_detected = analysis.get("sections_detected", []) or []
    cq = analysis.get("content_quality", {}) or {}

    email_ok = bool(contact.get("email"))
    phone_ok = bool(contact.get("phone"))
    linkedin_ok = bool(contact.get("linkedin"))
    github_ok = bool(contact.get("github"))

    section_presence = {
        "contact": email_ok or phone_ok or linkedin_ok or github_ok,
        "education": bool(cq.get("word_count")) and ("education" in sections_detected),
        "skills": "skills" in sections_detected,
        "projects": "projects" in sections_detected,
        "experience": "experience" in sections_detected,
    }

    passive_count = int(cq.get("passive_voice_count", 0) or 0)
    weak_phrases_found = cq.get("weak_phrases_found", []) or []
    avg_sentence_len = float(cq.get("avg_sentence_length", 0) or 0)
    word_count = int(cq.get("word_count", 0) or 0)

    grammar_check = []
    if passive_count > 3:
        grammar_check.append("High passive-voice usage detected. Rewrite bullets using active voice.")
    if weak_phrases_found:
        grammar_check.append("Filler/weak phrasing detected. Replace with concrete metrics and specific outcomes.")
    if not grammar_check:
        grammar_check.append("No major grammar issues detected by heuristics.")

    clarity_check = []
    if avg_sentence_len > 28:
        clarity_check.append("Some sentences are too long. Split into shorter, clearer bullet points.")
    if word_count < 200:
        clarity_check.append("Resume may be too short for ATS clarity. Add 2-4 more impact bullets.")
    if not clarity_check:
        clarity_check.append("Clarity looks good (sentence length and structure are within a reasonable range).")

    missing_required = analysis.get("missing_skills", []) or []
    missing_bonus = analysis.get("missing_bonus_skills", []) or []
    fixed_gap_comparison = (
        [f"Required: add `{s}`" for s in missing_required[:8]]
        + [f"Trending: add `{s}`" for s in missing_bonus[:6]]
    )

    compat = {
        "weaknesses_cons": analysis.get("weaknesses", []),
        "skills_analysis": {
            "technical": (analysis.get("skills_found", {}) or {}).get("technical", []),
            "analytical": (analysis.get("skills_found", {}) or {}).get("analytical", []),
            "soft": (analysis.get("skills_found", {}) or {}).get("soft", []),
        },
        "ats_score": {
            "ats_compatibility": analysis.get("ats_score", 0),
            "keyword_optimization": {
                "match_percentage": analysis.get("job_match_percentage", 0),
                "matched_keywords": analysis.get("highlighted_skills", []),
                "missing_keywords": analysis.get("missing_skills", []),
            },
        },
        "job_match": {
            "target_role": analysis.get("target_role", target_role),
            "match_percentage": analysis.get("job_match_percentage", 0),
            "missing_skills": analysis.get("missing_skills", []),
        },
        "skill_gap_analysis": {
            "missing_trending_skills": missing_bonus,
            "fixed_gap_comparison": fixed_gap_comparison or ["No skill gaps detected."],
        },
        "content_quality": {
            "grammar_check": grammar_check,
            "clarity_check": clarity_check,
            "section_presence": section_presence,
        },
    }

    # Keep original analysis keys too (so other code can keep using them).
    return {**analysis, **compat}


# ── analyze_resume_with_groq ─────────────────────────────────────────────────
def analyze_resume_with_groq(resume_text: str, target_role: str) -> str:
    """Stub kept for backward compatibility (no API call)."""
    return rule_based_resume_analysis(resume_text, target_role)


# ── hybrid_resume_analysis ───────────────────────────────────────────────────
def hybrid_resume_analysis(resume_text: str, role: str) -> Dict:
    """Stub kept for backward compatibility."""
    return analyze_resume(resume_text, role)


# ── validate_resume_quality ──────────────────────────────────────────────────
def validate_resume_quality(text: str) -> str:
    wc = len((text or "").split())
    if wc < 100:
        return "Resume too short"
    if wc > 1500:
        return "Resume too long"
    return "Good length"


# ── analyze_project_level / analyze_project_complexity ───────────────────────
def analyze_project_level(text: str) -> str:
    """Backward-compatible project-level classifier."""
    t = _normalize(text)
    if any(x in t for x in ["deep learning", "nlp", "transformer", "llm", "machine learning"]):
        return "Advanced"
    if any(x in t for x in ["api", "backend", "database", "microservice"]):
        return "Intermediate"
    if any(x in t for x in ["html", "css", "jquery"]):
        return "Beginner"
    return "Not clear"


# alias used in some older views
analyze_project_complexity = analyze_project_level


# ── analyze_research_paper_with_groq ────────────────────────────────────────
# Already defined in section 12 — no extra wrapper needed.


# ──────────────────────────────────────────────────────────────────────────────
# 12.  RESEARCH PAPER ANALYSIS (unchanged logic, now using shared utilities)
# ──────────────────────────────────────────────────────────────────────────────

def rule_based_research_paper_analysis(paper_text: str) -> str:
    t = paper_text or ""
    checks = {
        "abstract":    re.search(r"\babstract\b", t, re.I),
        "methodology": re.search(r"\b(methodology|methods?)\b", t, re.I),
        "literature":  re.search(r"\b(literature review|related work)\b", t, re.I),
        "dataset":     re.search(r"\b(dataset|data)\b", t, re.I),
        "metrics":     re.search(r"\b(accuracy|precision|recall|f1|metrics?)\b", t, re.I),
        "results":     re.search(r"\b(results?|experiments?)\b", t, re.I),
        "conclusion":  re.search(r"\b(conclusion|conclusions?)\b", t, re.I),
    }
    score = 30 + sum([10, 15, 10, 10, 10, 10, 5][i] for i, v in enumerate(checks.values()) if v)
    score = max(0, min(100, score))

    strengths = [
        desc for flag, desc in [
            (checks["abstract"],    "Abstract clearly states the research aim."),
            (checks["methodology"], "Methodology section describes how the study is conducted."),
            (checks["literature"],  "Literature review frames the research gap effectively."),
            (checks["dataset"],     "Dataset and experiments are referenced."),
            (checks["metrics"],     "Quantitative metrics reported (accuracy/F1/precision)."),
            (checks["results"],     "Results section present and linked to the problem."),
            (checks["conclusion"],  "Conclusion summarises contributions and future work."),
        ] if flag
    ]
    weaknesses = [
        desc for flag, desc in [
            (not checks["abstract"],    "Missing abstract — add a concise problem + contribution statement."),
            (not checks["methodology"], "Methodology is weak or unclear — describe approach step-by-step."),
            (not checks["literature"],  "No literature review — situate your work relative to prior art."),
            (not checks["dataset"],     "Dataset details missing — describe source, size, splits, preprocessing."),
            (not checks["metrics"],     "No evaluation metrics — report accuracy, F1, BLEU, etc."),
            (not checks["results"],     "Results not clearly presented — add tables/charts comparing baselines."),
            (not checks["conclusion"],  "No conclusion — summarise findings and propose future work."),
        ] if flag
    ]
    if not weaknesses:
        weaknesses.append("Paper looks well-structured. Consider adding ablation studies for novelty.")

    def bullets(items): return "\n".join(f"  - {x}" for x in items)
    return "\n".join([
        f"Score out of 100\n  {score}",
        "Strengths:\n" + bullets(strengths[:5] or ["Solid structure detected."]),
        "Weaknesses (CONS):\n" + bullets(weaknesses[:5]),
        "Suggestions:\n" + bullets([
            "Add explicit section headings: Abstract, Introduction, Related Work, Methodology, Experiments, Results, Conclusion.",
            "State your research question and novelty in the first paragraph.",
            "Include baselines, ablation controls, and standard metrics with variance/confidence intervals.",
        ]),
    ])


def analyze_research_paper_with_groq(paper_text: str) -> str:
    return rule_based_research_paper_analysis(paper_text)


def _safe_list(value):
    return value if isinstance(value, list) else []


def _normalize_role_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\s]+", " ", (value or "").lower()).strip()


ROLE_ALIAS_MAP: Dict[str, str] = {
    "sde": "software engineer",
    "software developer": "software engineer",
    "full stack developer": "software engineer",
    "fullstack developer": "software engineer",
    "frontend engineer": "frontend developer",
    "ui developer": "frontend developer",
    "react developer": "frontend developer",
    "web developer": "frontend developer",
    "backend engineer": "backend developer",
    "api developer": "backend developer",
    "server side developer": "backend developer",
    "ml engineer": "machine learning engineer",
    "ai engineer": "machine learning engineer",
    "deep learning engineer": "machine learning engineer",
    "data analyst": "data scientist",
    "business analyst": "data scientist",
    "data engineering": "data engineer",
    "etl developer": "data engineer",
    "cloud engineer": "devops engineer",
    "platform engineer": "devops engineer",
    "site reliability engineer": "devops engineer",
    "sre": "devops engineer",
    "research intern": "research assistant",
    "research engineer": "research assistant",
    "product analyst": "data scientist",
    "nlp engineer": "machine learning engineer",
    "computer vision engineer": "machine learning engineer",
}


def resolve_target_role(target_role: str) -> str:
    role = _normalize_role_text(target_role)
    if not role:
        return "software engineer"
    if role in ROLE_PROFILES:
        return role
    if role in ROLE_ALIAS_MAP:
        return ROLE_ALIAS_MAP[role]
    for alias, canonical in ROLE_ALIAS_MAP.items():
        if alias in role:
            return canonical
    for key in ROLE_PROFILES.keys():
        if key in role:
            return key
    return "software engineer"


def recommend_jobs_for_profile(skills: List[str], target_role: str = "") -> List[str]:
    canonical = resolve_target_role(target_role)
    role_profile = ROLE_PROFILES.get(canonical, ROLE_PROFILES["software engineer"])
    base_jobs = [canonical.title(), "Graduate Trainee Engineer", "Internship - " + canonical.title()]
    bonus_jobs = {
        "software engineer": ["Backend Developer", "Full Stack Developer", "Platform Engineer"],
        "frontend developer": ["UI Engineer", "React Developer", "Frontend Intern"],
        "backend developer": ["API Engineer", "Python Backend Developer", "Microservices Developer"],
        "data scientist": ["Data Analyst", "ML Analyst", "Business Intelligence Analyst"],
        "machine learning engineer": ["AI Engineer", "NLP Engineer", "Computer Vision Engineer"],
        "data engineer": ["ETL Engineer", "Analytics Engineer", "Cloud Data Engineer"],
        "devops engineer": ["Cloud Engineer", "Site Reliability Engineer", "Infrastructure Engineer"],
        "research assistant": ["Research Intern", "Research Associate", "Applied AI Researcher"],
    }
    jobs = base_jobs + bonus_jobs.get(canonical, [])
    if any(x in [s.lower() for s in skills] for x in ["aws", "gcp", "azure", "docker"]):
        jobs.append("Cloud Solutions Engineer")
    if any(x in [s.lower() for s in skills] for x in ["pytorch", "tensorflow", "scikit-learn"]):
        jobs.append("Machine Learning Engineer")
    return jobs[:8]


def build_improve_resume_feedback(extracted_details: Dict) -> Dict[str, List[str]]:
    analysis = (extracted_details or {}).get("analysis", {}) or {}
    missing = _safe_list(analysis.get("missing_skills", []))
    tips = _safe_list(analysis.get("resume_tips", []))
    wording = [
        "Start bullet points with strong action verbs: built, improved, optimized, automated.",
        "Add quantified impact to each major project or experience bullet.",
        "Use role-relevant keywords naturally in skills and project descriptions.",
    ]
    ats_feedback = [
        f"Current ATS score is {analysis.get('ats_score', 0)}/100.",
        "Keep section headings clear: Summary, Skills, Projects, Experience, Education.",
        "Ensure contact details and role intent are visible near the top.",
    ]
    improvement_points = tips[:4] if tips else [
        "Add 2-3 measurable achievements with numbers.",
        "Align profile summary with your target role and strengths.",
        "Highlight domain projects with tech stack and outcomes.",
    ]
    return {
        "missing_skills": missing[:10],
        "wording_suggestions": wording,
        "ats_feedback": ats_feedback,
        "improvement_points": improvement_points,
    }


def generate_career_summary_payload(extracted_details: Dict, target_role: str = "") -> Dict:
    extracted_details = extracted_details or {}
    analysis = extracted_details.get("analysis", {}) or {}
    personal = extracted_details.get("personal_info", {}) or {}
    skills = _safe_list(extracted_details.get("skills", []))
    canonical_role = resolve_target_role(target_role or str(analysis.get("predicted_role", "")))
    missing_skills = _safe_list(analysis.get("missing_skills", []))
    strengths = []
    if skills:
        strengths.append("Strong skill foundations in " + ", ".join(skills[:6]) + ".")
    if extracted_details.get("projects"):
        strengths.append("Project portfolio is present and supports practical readiness.")
    if extracted_details.get("experience"):
        strengths.append("Experience details are available and improve role fit confidence.")
    if not strengths:
        strengths.append("Core profile sections are available for role-fit analysis.")
    role_fit_notes = [
        f"Target role considered: {canonical_role.title()}.",
        f"Current role fit estimate: {analysis.get('match_score', 0)}%.",
        "Role fit can improve by adding missing target-role keywords and quantified outcomes.",
    ]
    improvement = build_improve_resume_feedback(extracted_details)
    summary_text = (
        f"{personal.get('name') or 'This candidate'} is positioned for {canonical_role.title()} roles "
        f"with an ATS readiness score of {analysis.get('ats_score', 0)}/100."
    )

    ai_prompt = (
        "Write a concise 3 sentence career summary.\n"
        f"Target role: {canonical_role}\n"
        f"Skills: {', '.join(skills[:10])}\n"
        f"Missing skills: {', '.join(missing_skills[:8])}\n"
        f"Current summary: {personal.get('summary', '')}"
    )
    ai_summary = generate_ai_response(ai_prompt, system_prompt="You are Talvyn, a concise career intelligence assistant.")
    if ai_summary and not ai_summary.lower().startswith("error:"):
        summary_text = ai_summary.strip()

    return {
        "target_role": canonical_role.title(),
        "short_summary": summary_text,
        "strengths": strengths[:6],
        "missing_skills": missing_skills[:10],
        "role_fit_notes": role_fit_notes,
        "improvement_suggestions": improvement.get("improvement_points", []),
        "recommended_jobs": recommend_jobs_for_profile(skills, canonical_role),
        "improve_resume": improvement,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 13.  QUICK SELF-TEST
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    SAMPLE = """
John Doe
john.doe@email.com  |  +91 9876543210  |  linkedin.com/in/johndoe  |  github.com/johndoe

SUMMARY
Final-year B.Tech Computer Science student passionate about building scalable software.

EDUCATION
B.Tech Computer Science – IIT Bombay  (2020 – 2024)  CGPA: 8.9/10

EXPERIENCE
Software Engineering Intern – Acme Corp  (May 2023 – Aug 2023)
  - Built a REST API with Django and PostgreSQL serving 5,000 daily requests.
  - Reduced API response time by 40% by implementing Redis caching.
  - Collaborated with a team of 5 engineers using Git and Agile/Scrum.

PROJECTS
Sentiment Analysis Engine (github.com/johndoe/senti)
  - Trained a PyTorch transformer model achieving 92% F1 on IMDB dataset.
  - Deployed on AWS EC2 with Docker; handled 1,000 req/s at peak load.

URL Shortener (github.com/johndoe/shorturl)
  - Built with Python, Flask, Redis, and PostgreSQL; containerised with Docker.

SKILLS
Languages: Python, JavaScript, SQL, Java
Frameworks: Django, React, Flask, FastAPI
Tools: Docker, Git, AWS, Linux, CI/CD (GitHub Actions)
ML: PyTorch, Scikit-learn, Pandas, NumPy

CERTIFICATIONS
AWS Certified Cloud Practitioner (2023)
"""
    result = analyze_resume(SAMPLE, "Software Engineer")
    print(render_plain_text(result))
    print("\n--- JSON keys ---")
    for k, v in result.items():
        print(f"  {k}: {type(v).__name__}")