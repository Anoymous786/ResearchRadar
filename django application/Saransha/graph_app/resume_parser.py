import json
import logging
import re
from typing import Any, Dict, List, Tuple

import fitz
from PyPDF2 import PdfReader

from graph_app.groq_client import generate_ai_response

logger = logging.getLogger(__name__)

MAX_RESUME_BYTES = 5 * 1024 * 1024
BAD_NAME_WORDS = {"resume", "cv", "profile", "student profile", "curriculum vitae"}

TECH_SKILLS = {
    "python",
    "java",
    "c++",
    "c",
    "c#",
    "javascript",
    "typescript",
    "sql",
    "react",
    "node.js",
    "django",
    "flask",
    "fastapi",
    "git",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "tensorflow",
    "pytorch",
    "machine learning",
    "deep learning",
    "numpy",
    "pandas",
    "scikit-learn",
    "linux",
    "mongodb",
    "postgresql",
    "mysql",
    "redis",
    "rest api",
}

SKILL_ALIASES = {
    "node": "node.js",
    "nodejs": "node.js",
    "js": "javascript",
    "ts": "typescript",
    "postgres": "postgresql",
    "sklearn": "scikit-learn",
    "scikit": "scikit-learn",
    "tf": "tensorflow",
    "torch": "pytorch",
    "reactjs": "react",
    "ml": "machine learning",
    "machine learning": "machine learning",
    "mysql": "sql",
    "pytorch": "deep learning",
    "nlp": "natural language processing",
    "nodejs": "node.js",
}

ROLE_SKILL_MAP = {
    "software engineer": ["python", "java", "javascript", "sql", "git", "docker", "rest api"],
    "data scientist": ["python", "sql", "numpy", "pandas", "scikit-learn", "tensorflow", "pytorch"],
    "backend developer": ["python", "java", "sql", "django", "flask", "rest api", "docker"],
    "frontend developer": ["javascript", "typescript", "react", "node.js", "git"],
    "machine learning engineer": ["python", "numpy", "pandas", "scikit-learn", "tensorflow", "pytorch", "docker"],
}

GENERAL_KEYWORDS = {
    "api",
    "backend",
    "frontend",
    "database",
    "cloud",
    "deployment",
    "testing",
    "automation",
    "agile",
    "scrum",
    "microservices",
    "analytics",
    "optimization",
    "performance",
    "scalable",
}

SEMANTIC_CONCEPTS = {
    "machine learning": {"machine learning", "deep learning", "neural network", "ai", "model"},
    "web development": {"web", "frontend", "backend", "react", "api", "django", "flask", "node"},
    "data engineering": {"etl", "pipeline", "warehouse", "spark", "airflow", "kafka"},
    "cloud": {"aws", "azure", "gcp", "cloud", "docker", "kubernetes", "devops"},
    "data analysis": {"sql", "pandas", "numpy", "analysis", "visualization", "statistics"},
}


SECTION_PATTERNS = {
    "education": re.compile(r"^\s*(education|academic background|qualification[s]?)\s*:?\s*$", re.I),
    "experience": re.compile(r"^\s*(experience|work experience|employment|internship[s]?)\s*:?\s*$", re.I),
    "projects": re.compile(r"^\s*(project[s]?|academic projects|personal projects)\s*:?\s*$", re.I),
    "skills": re.compile(r"^\s*(skills|technical skills|tech stack|tools|technologies)\s*:?\s*$", re.I),
    "summary": re.compile(r"^\s*(summary|objective|profile|about me)\s*:?\s*$", re.I),
    "certifications": re.compile(r"^\s*(certification[s]?|certificate[s]?|licenses?)\s*:?\s*$", re.I),
    "courses": re.compile(r"^\s*(online courses|courses completed|training programs|courses|trainings?)\s*:?\s*$", re.I),
    "achievements": re.compile(r"^\s*(achievements?|awards?|honou?rs?|accomplishments?|competitions?)\s*:?\s*$", re.I),
}


def _clean_resume_text(raw_text: str) -> str:
    text = raw_text or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    merged_lines = []
    for block in text.split("\n\n"):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        merged_block = [lines[0]]
        for line in lines[1:]:
            prev = merged_block[-1]
            if prev.endswith((".", ":", ";", "?", "!", "•", "-")):
                merged_block.append(line)
            elif len(line.split()) <= 3:
                merged_block.append(line)
            else:
                merged_block[-1] = prev + " " + line
        merged_lines.append("\n".join(merged_block))

    return "\n\n".join(merged_lines).strip()


def extract_pdf_text_from_upload(uploaded_file) -> str:
    uploaded_file.seek(0)
    data = uploaded_file.read()

    text = ""
    fitz_error = None
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            pages = [page.get_text("text") or "" for page in doc]
            text = "\n\n".join(pages)
    except Exception as exc:
        fitz_error = exc

    if not text.strip():
        try:
            uploaded_file.seek(0)
            reader = PdfReader(uploaded_file)
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(pages)
        except Exception as pypdf_exc:
            if fitz_error:
                raise ValueError(
                    f"Unable to parse this PDF (PyMuPDF: {fitz_error}; PyPDF2: {pypdf_exc})."
                ) from pypdf_exc
            raise ValueError(f"Unable to parse this PDF: {pypdf_exc}") from pypdf_exc

    return _clean_resume_text(text)


def validate_resume_upload(uploaded_file) -> Tuple[bool, str]:
    if not uploaded_file:
        return False, "Please upload your resume PDF."
    if not uploaded_file.name.lower().endswith(".pdf"):
        return False, "Only PDF resumes are supported."
    if uploaded_file.size > MAX_RESUME_BYTES:
        return False, "Resume size is too large. Please upload a file under 5 MB."
    return True, ""


def _extract_email_phone(text: str) -> Tuple[str, str]:
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text)
    phone_match = re.search(
        r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{3,4}\)?[\s\-.]?)?\d{3}[\s\-.]?\d{4}",
        text,
    )
    email = email_match.group(0).strip() if email_match else ""
    phone = phone_match.group(0).strip() if phone_match else ""
    return email, phone


def _is_valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", value or ""))


def _is_valid_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value or "")
    return 10 <= len(digits) <= 13


def _is_bad_name(name: str) -> bool:
    if not name:
        return True
    n = name.strip().lower()
    return n in BAD_NAME_WORDS or any(word == n for word in BAD_NAME_WORDS)


def _extract_name_heuristic(text: str, email: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines[:8]:
        lc = line.lower()
        if _is_bad_name(lc) or re.search(r"[@\d:/]", line):
            continue
        words = line.split()
        if 2 <= len(words) <= 4 and all(w[:1].isalpha() for w in words):
            return line.title()
    if email:
        base = email.split("@")[0].replace(".", " ").replace("_", " ").strip()
        return base.title() if base else ""
    return ""


def _extract_section_list(text: str, section_words: List[str]) -> List[str]:
    lines = [ln.strip(" -\t") for ln in text.splitlines() if ln.strip()]
    out = []
    capture = False
    for line in lines:
        l = line.lower().strip(":")
        if any(l == s for s in section_words):
            capture = True
            continue
        if capture and re.fullmatch(r"[A-Za-z ]{2,30}:?", line):
            break
        if capture and len(line) > 2:
            out.append(line)
    return out[:8]


def _split_sections(text: str) -> Dict[str, str]:
    lines = text.splitlines()
    heads: Dict[str, int] = {}
    for idx, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue
        for name, pattern in SECTION_PATTERNS.items():
            if pattern.match(line_clean) and name not in heads:
                heads[name] = idx
    if not heads:
        return {}

    ordered = sorted(heads.items(), key=lambda kv: kv[1])
    sections: Dict[str, str] = {}
    for i, (name, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if body:
            sections[name] = body
    return sections


def _section_to_entries(section_text: str, limit: int = 8) -> List[str]:
    if not section_text:
        return []
    entries = []
    for line in section_text.splitlines():
        line = line.strip(" \t-•")
        if len(line) < 3:
            continue
        if re.fullmatch(r"[A-Za-z ]{2,30}:?", line):
            continue
        entries.append(line)
    # merge short trailing fragments
    merged: List[str] = []
    for line in entries:
        if not merged:
            merged.append(line)
            continue
        if len(line.split()) <= 3:
            merged[-1] = merged[-1] + " " + line
        else:
            merged.append(line)
    dedup = []
    seen = set()
    for item in merged:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    return dedup[:limit]


def _extract_cgpa(clean_text: str) -> str:
    match = re.search(r"\b(?:cgpa|gpa)\s*[:\-]?\s*(\d(?:\.\d{1,2})?)\s*(?:/10|/4)?\b", clean_text, re.I)
    return match.group(1) if match else ""


def _extract_certifications_heuristic(text: str) -> List[str]:
    out: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip(" \t-•")
        if len(line) < 5:
            continue
        low = line.lower()
        if "certified" in low or "certification" in low or "certificate" in low:
            out.append(line)
    dedup: List[str] = []
    seen = set()
    for item in out:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    return dedup[:12]


def _extract_achievements_heuristic(text: str) -> List[str]:
    out: List[str] = []
    keywords = ("award", "winner", "won", "rank", "achievement", "honor", "honour", "accomplish", "competition")
    for raw in (text or "").splitlines():
        line = raw.strip(" \t-•")
        if len(line) < 5:
            continue
        low = line.lower()
        if any(k in low for k in keywords):
            out.append(line)
    dedup: List[str] = []
    seen = set()
    for item in out:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    return dedup[:15]


def _extract_social_links(text: str) -> List[Dict[str, str]]:
    raw_urls = re.findall(r"(?:(?:https?://)|(?:www\.))[^\s<>\]\)\"']+", text or "", flags=re.I)
    links: List[Dict[str, str]] = []
    seen = set()
    for raw in raw_urls:
        url = raw.rstrip(".,;)]}").strip()
        if not url:
            continue
        normalized = url if url.lower().startswith(("http://", "https://")) else f"https://{url}"
        low = normalized.lower()
        if "linkedin.com" in low:
            platform = "LinkedIn"
        elif "github.com" in low:
            platform = "GitHub"
        elif "kaggle.com" in low:
            platform = "Kaggle"
        elif "leetcode.com" in low:
            platform = "LeetCode"
        else:
            platform = "Portfolio"
        key = (platform.lower(), normalized.lower())
        if key in seen:
            continue
        seen.add(key)
        links.append({"platform": platform, "url": normalized})
    return links[:20]


def _extract_skills_heuristic(text: str) -> List[str]:
    found = []
    low = text.lower()
    for skill in sorted(TECH_SKILLS):
        pattern = r"(?<!\w)" + re.escape(skill) + r"(?!\w)"
        if re.search(pattern, low):
            found.append(skill)
    return found


def _normalize_skill_token(value: str) -> str:
    token = (value or "").strip().lower()
    token = token.replace("  ", " ")
    token = SKILL_ALIASES.get(token, token)
    return token


def _tokenize_words(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z0-9\+\#\.-]*", (text or "").lower())


def _extract_general_keywords(text: str) -> List[str]:
    low = (text or "").lower()
    found = []
    for kw in sorted(GENERAL_KEYWORDS):
        if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", low):
            found.append(kw)
    return found


def _extract_role_keywords(role: str) -> List[str]:
    canonical = (role or "").strip().lower()
    if not canonical:
        return []
    if canonical in ROLE_SKILL_MAP:
        return ROLE_SKILL_MAP[canonical]
    for key in ROLE_SKILL_MAP:
        if canonical in key or key in canonical:
            return ROLE_SKILL_MAP[key]
    return []


def _compute_formatting_quality(parsed: Dict[str, Any], clean_text: str) -> int:
    score = 35
    if parsed.get("email"):
        score += 10
    if parsed.get("phone"):
        score += 10
    if parsed.get("summary"):
        score += 10
    if parsed.get("education"):
        score += 10
    if parsed.get("experience"):
        score += 10
    if parsed.get("projects"):
        score += 8
    if re.search(r"(^|\n)\s*[-•*]\s+\S+", clean_text):
        score += 7
    return max(0, min(100, score))


def _compute_experience_quality(experience_entries: List[str]) -> int:
    if not experience_entries:
        return 20
    text = " ".join(experience_entries)
    score = 45 + min(30, len(experience_entries) * 10)
    if re.search(r"\b\d+(\.\d+)?%|\b\d{2,}\b", text):
        score += 15
    if re.search(r"\b(built|developed|improved|optimized|designed|led|implemented)\b", text.lower()):
        score += 10
    return max(0, min(100, score))


def _compute_projects_quality(project_entries: List[str]) -> int:
    if not project_entries:
        return 20
    text = " ".join(project_entries).lower()
    score = 45 + min(30, len(project_entries) * 10)
    if "github.com" in text:
        score += 10
    if re.search(r"\b(api|model|deployment|pipeline|dashboard|application)\b", text):
        score += 10
    if re.search(r"\b\d+(\.\d+)?%|\b\d{2,}\b", text):
        score += 5
    return max(0, min(100, score))


def _semantic_similarity_boost(resume_text: str, keyword_pool: List[str]) -> int:
    if not keyword_pool:
        return 0
    tokens = set(_tokenize_words(resume_text))
    if not tokens:
        return 0
    concept_hits = 0
    for concept_terms in SEMANTIC_CONCEPTS.values():
        if tokens.intersection(concept_terms):
            concept_hits += 1
    keyword_tokens = set()
    for kw in keyword_pool:
        keyword_tokens.update(_tokenize_words(kw))
    overlap = len(tokens.intersection(keyword_tokens))
    jaccard = overlap / max(1, len(keyword_tokens))
    semantic = int(round((jaccard * 70) + (min(4, concept_hits) * 7.5)))
    return max(0, min(100, semantic))


def _top_alternative_roles(skills: List[str], selected_role: str) -> List[str]:
    skill_set = set(skills or [])
    ranked = []
    selected = (selected_role or "").strip().lower()
    for role, req in ROLE_SKILL_MAP.items():
        if role == selected:
            continue
        matched = len([s for s in req if s in skill_set])
        ratio = matched / max(1, len(req))
        ranked.append((ratio, role))
    ranked.sort(reverse=True)
    return [role for _, role in ranked[:3]]


def _merge_skills(ai_skills: List[str], heuristic_skills: List[str]) -> List[str]:
    merged = []
    for skill in (ai_skills or []) + (heuristic_skills or []):
        normalized = _normalize_skill_token(skill)
        if normalized in TECH_SKILLS and normalized not in merged:
            merged.append(normalized)
    return merged[:24]


def _strict_json_prompt(clean_text: str) -> str:
    return (
        "You are a strict JSON extractor for student resumes.\n"
        "Return ONLY valid JSON. No markdown. No explanation.\n"
        "Use exactly this schema and key order:\n"
        "{\n"
        '  "name": "",\n'
        '  "email": "",\n'
        '  "phone": "",\n'
        '  "skills": [],\n'
        '  "education": [],\n'
        '  "experience": [],\n'
        '  "projects": [],\n'
        '  "certifications": [],\n'
        '  "achievements": [],\n'
        '  "social_links": [],\n'
        '  "summary": ""\n'
        "}\n"
        "Rules:\n"
        "- Do not hallucinate missing data.\n"
        "- If missing, keep empty string or empty array.\n"
        "- Name cannot be heading words like Resume/CV/Profile.\n"
        "- Skills must include only technical keywords.\n\n"
        "Resume text:\n"
        f"{clean_text}"
    )


def _parse_ai_json(clean_text: str) -> Dict[str, Any]:
    response = generate_ai_response(
        _strict_json_prompt(clean_text),
        system_prompt="Return strict JSON only.",
    )
    candidate = (response or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?", "", candidate).strip()
        candidate = candidate.rstrip("`").strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("AI did not return JSON.")
    payload = json.loads(candidate[start : end + 1])

    schema = {
        "name": "",
        "email": "",
        "phone": "",
        "skills": [],
        "education": [],
        "experience": [],
        "projects": [],
        "certifications": [],
        "achievements": [],
        "social_links": [],
        "summary": "",
    }
    for key, default in schema.items():
        if key not in payload or payload[key] is None:
            payload[key] = default
    return payload


def _normalize_parsed_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {
        "name": str(payload.get("name", "") or "").strip(),
        "email": str(payload.get("email", "") or "").strip(),
        "phone": str(payload.get("phone", "") or "").strip(),
        "skills": [str(x).strip().lower() for x in (payload.get("skills") or []) if str(x).strip()],
        "education": [str(x).strip() for x in (payload.get("education") or []) if str(x).strip()],
        "experience": [str(x).strip() for x in (payload.get("experience") or []) if str(x).strip()],
        "projects": [str(x).strip() for x in (payload.get("projects") or []) if str(x).strip()],
        "certifications": [str(x).strip() for x in (payload.get("certifications") or []) if str(x).strip()],
        "achievements": [str(x).strip() for x in (payload.get("achievements") or []) if str(x).strip()],
        "social_links": payload.get("social_links") or [],
        "summary": str(payload.get("summary", "") or "").strip(),
    }
    normalized["skills"] = [_normalize_skill_token(s) for s in normalized["skills"]]
    normalized["skills"] = [s for s in normalized["skills"] if s in TECH_SKILLS]
    if not isinstance(normalized["social_links"], list):
        normalized["social_links"] = []
    return normalized


def _apply_fallbacks(parsed: Dict[str, Any], clean_text: str) -> Dict[str, Any]:
    heuristic_skills = _extract_skills_heuristic(clean_text)
    section_map = _split_sections(clean_text)
    email_h, phone_h = _extract_email_phone(clean_text)
    if not parsed["email"] or not _is_valid_email(parsed["email"]):
        parsed["email"] = email_h
    if not parsed["phone"] or not _is_valid_phone(parsed["phone"]):
        parsed["phone"] = phone_h
    if not parsed["name"] or _is_bad_name(parsed["name"]):
        parsed["name"] = _extract_name_heuristic(clean_text, parsed["email"])
    parsed["skills"] = _merge_skills(parsed.get("skills", []), heuristic_skills)
    if not parsed["education"]:
        if section_map.get("education"):
            parsed["education"] = _section_to_entries(section_map["education"])
        if not parsed["education"]:
            parsed["education"] = _extract_section_list(clean_text, ["education", "academic background", "qualification"])
    if not parsed["experience"]:
        if section_map.get("experience"):
            parsed["experience"] = _section_to_entries(section_map["experience"])
        if not parsed["experience"]:
            parsed["experience"] = _extract_section_list(clean_text, ["experience", "work experience", "internships"])
    if not parsed["projects"]:
        if section_map.get("projects"):
            parsed["projects"] = _section_to_entries(section_map["projects"])
        if not parsed["projects"]:
            parsed["projects"] = _extract_section_list(clean_text, ["projects", "project"])
    if not parsed["summary"]:
        if section_map.get("summary"):
            parsed["summary"] = " ".join(_section_to_entries(section_map["summary"], limit=3))[:420]
        if not parsed["summary"]:
            lines = [ln.strip() for ln in clean_text.splitlines() if ln.strip()]
            parsed["summary"] = " ".join(lines[:3])[:420] if lines else ""
    if not parsed.get("certifications"):
        if section_map.get("certifications"):
            parsed["certifications"] = _section_to_entries(section_map["certifications"])
        if not parsed.get("certifications"):
            parsed["certifications"] = _extract_certifications_heuristic(clean_text)
    course_entries = _section_to_entries(section_map.get("courses", "")) if section_map.get("courses") else []
    if course_entries:
        parsed["certifications"] = list(dict.fromkeys((parsed.get("certifications") or []) + course_entries))[:16]
    if not parsed.get("achievements"):
        if section_map.get("achievements"):
            parsed["achievements"] = _section_to_entries(section_map["achievements"], limit=15)
        if not parsed.get("achievements"):
            parsed["achievements"] = _extract_achievements_heuristic(clean_text)
    if not parsed.get("social_links"):
        parsed["social_links"] = _extract_social_links(clean_text)
    return parsed


def _confidence(parsed: Dict[str, Any]) -> Dict[str, int]:
    conf = {
        "name": 30,
        "email": 30,
        "phone": 30,
        "skills": 30,
        "education": 30,
        "experience": 30,
        "projects": 30,
        "summary": 30,
    }
    if parsed["name"] and not _is_bad_name(parsed["name"]):
        conf["name"] = 80
    if _is_valid_email(parsed["email"]):
        conf["email"] = 95
    if _is_valid_phone(parsed["phone"]):
        conf["phone"] = 90
    if parsed["skills"]:
        conf["skills"] = min(95, 55 + len(parsed["skills"]) * 3)
    for k in ("education", "experience", "projects"):
        if parsed[k]:
            conf[k] = min(90, 50 + len(parsed[k]) * 5)
    if parsed["summary"]:
        conf["summary"] = 75
    return conf


def _ats_analysis(parsed: Dict[str, Any], clean_text: str, student_profile=None, target_role: str = "") -> Dict[str, Any]:
    role_pref = ""
    if student_profile is not None:
        role_pref = (getattr(student_profile, "target_role", "") or "").strip()
    selected_role = (target_role or role_pref or "").strip().lower()
    selected_role = selected_role if selected_role in ROLE_SKILL_MAP else ""
    mode = "role-based" if selected_role else "general"

    normalized_skills = [_normalize_skill_token(s) for s in parsed.get("skills", [])]
    skill_set = {s for s in normalized_skills if s}

    role_keywords = _extract_role_keywords(selected_role) if selected_role else []
    general_keywords = _extract_general_keywords(clean_text)
    keyword_pool = role_keywords if role_keywords else general_keywords

    matched_skills = sorted([s for s in (role_keywords or TECH_SKILLS) if s in skill_set])[:25]
    missing_skills = sorted([s for s in role_keywords if s not in skill_set])[:25] if role_keywords else []

    matched_keywords = sorted([k for k in keyword_pool if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", clean_text.lower())])[:25]
    missing_keywords = sorted([k for k in keyword_pool if k not in matched_keywords])[:25]

    skills_score = int(round((len(matched_skills) / max(1, len(role_keywords or TECH_SKILLS))) * 100))
    if mode == "general":
        skills_score = max(skills_score, min(100, 25 + len(skill_set) * 8))

    experience_base = _compute_experience_quality(parsed.get("experience", []))
    projects_base = _compute_projects_quality(parsed.get("projects", []))
    keywords_exact = int(round((len(matched_keywords) / max(1, len(keyword_pool))) * 100)) if keyword_pool else 0
    semantic_boost = _semantic_similarity_boost(clean_text, keyword_pool)
    keywords_score = int(round((keywords_exact * 0.75) + (semantic_boost * 0.25)))
    formatting_score = _compute_formatting_quality(parsed, clean_text)

    weighted_score = (
        (skills_score * 0.30)
        + (experience_base * 0.25)
        + (projects_base * 0.20)
        + (keywords_score * 0.15)
        + (formatting_score * 0.10)
    )
    ats_score = int(round(max(0, min(100, weighted_score))))

    predicted_role = selected_role or "software engineer"
    if not selected_role:
        # General mode still provides a best-fit role for continuity.
        best = _top_alternative_roles(normalized_skills, "")
        if best:
            predicted_role = best[0]

    alternative_roles = _top_alternative_roles(normalized_skills, selected_role) if mode == "role-based" else []

    strengths = []
    weaknesses = []
    suggestions = []

    if parsed.get("email") and parsed.get("phone"):
        strengths.append("Contact information is complete and recruiter-friendly.")
    if parsed.get("projects"):
        strengths.append("Projects section is present and supports profile credibility.")
    if len(parsed.get("skills", [])) >= 6:
        strengths.append("Technical skill coverage is good for a student profile.")
    if parsed.get("experience"):
        strengths.append("Experience section is present and helps ATS relevance.")

    if not parsed.get("summary"):
        weaknesses.append("Professional summary is missing or too weak.")
    if len(parsed.get("skills", [])) < 5:
        weaknesses.append("Technical skill coverage is currently limited.")
    if not parsed.get("experience"):
        weaknesses.append("Experience section is missing.")
    if missing_skills:
        weaknesses.append("Some target-role skills are missing for stronger ATS fit.")

    if not parsed.get("summary"):
        suggestions.append("Add a concise 3-4 line summary focused on outcomes and technologies.")
    if missing_skills:
        suggestions.append("Add these missing target-role skills where genuinely applicable: " + ", ".join(missing_skills[:8]))
    if not parsed.get("experience"):
        suggestions.append("Add internships, freelance work, or practical contributions with measurable impact.")
    if not parsed.get("projects"):
        suggestions.append("Include 2-3 technical projects with stack, role, and measurable outcomes.")
    if not suggestions:
        suggestions.append("Keep improving quantified achievements and role-specific keyword coverage.")

    return {
        "ats_score": ats_score,
        "mode": mode,
        "job_role": selected_role or None,
        "role_match": predicted_role,
        "role_match_score": keywords_exact if mode == "role-based" else skills_score,
        "breakdown": {
            "skills": skills_score,
            "experience": experience_base,
            "projects": projects_base,
            "keywords": keywords_score,
            "formatting": formatting_score,
        },
        "matched_skills": matched_skills,
        "missing_skills": missing_skills[:12],
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords,
        "alternative_roles": alternative_roles,
        "score_explanation": (
            f"Weighted ATS score using skills(30%), experience(25%), projects(20%), "
            f"keywords(15%), formatting(10%). Mode: {mode}."
        ),
        "strengths": strengths[:6],
        "weaknesses": weaknesses[:6],
        "suggestions": suggestions[:6],
    }


def _build_student_summary(parsed: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    name = parsed.get("name") or "This student"
    role = (analysis.get("role_match") or "software engineer").title()
    skills = ", ".join(parsed.get("skills", [])[:6]) or "core technical foundations"
    projects_count = len(parsed.get("projects", []) or [])
    exp_count = len(parsed.get("experience", []) or [])
    return (
        f"{name} demonstrates alignment toward {role} with strengths in {skills}. "
        f"The resume includes {projects_count} project entry(s) and {exp_count} experience entry(s). "
        f"ATS readiness is {analysis.get('ats_score', 0)}/100, with focused recommendations available for further improvement."
    )


def analyze_resume_file(uploaded_file, student_profile=None, target_role: str = "") -> Dict[str, Any]:
    ok, error = validate_resume_upload(uploaded_file)
    if not ok:
        raise ValueError(error)

    clean_text = extract_pdf_text_from_upload(uploaded_file)
    if not clean_text.strip():
        raise ValueError("Could not extract readable text from this PDF.")

    ai_used = True
    try:
        ai_raw = _parse_ai_json(clean_text)
        parsed = _normalize_parsed_payload(ai_raw)
    except Exception as exc:
        logger.warning("AI parsing failed; fallback heuristics used. Error: %s", exc)
        ai_used = False
        parsed = _normalize_parsed_payload({})

    parsed = _apply_fallbacks(parsed, clean_text)
    if _is_bad_name(parsed.get("name", "")):
        parsed["name"] = ""
    analysis = _ats_analysis(parsed, clean_text=clean_text, student_profile=student_profile, target_role=target_role)
    student_summary = _build_student_summary(parsed, analysis)
    cgpa = _extract_cgpa(clean_text)

    return {
        "parsed_profile": parsed,
        "analysis": analysis,
        "student_summary": student_summary,
        "extracted_cgpa": cgpa,
        "confidence": _confidence(parsed),
        "validation": {
            "email_valid": _is_valid_email(parsed.get("email", "")),
            "phone_valid": _is_valid_phone(parsed.get("phone", "")),
            "name_valid": bool(parsed.get("name")) and not _is_bad_name(parsed.get("name", "")),
        },
        "meta": {
            "ai_used": ai_used,
            "text_length": len(clean_text),
            "clean_text": clean_text,
        },
    }
