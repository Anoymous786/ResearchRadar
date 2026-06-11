# Saransha/views.py

from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.core.files.storage import FileSystemStorage
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Q
from django.urls import reverse
import pandas as pd
import openpyxl
import os
import io
import tempfile
import ast
import re
from datetime import datetime
from supabase import create_client
from graph_app.groq_client import generate_ai_response, generate_resume_insights
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from django.http import JsonResponse
import json
from dotenv import load_dotenv

from .utils import (
    load_and_filter_excel,  
    get_publications_from_profile,
    get_publications_safe,
    process_profiles_from_excel,
    generate_author_summary,
    update_publication_details
)

from graph_app.models import (
    Users_Publication,
    FacultyProfile,
    Publication,
    StudentProfile,
    TalvynConversation,
    TalvynMessage,
)
from graph_app.forms import FacultyProfileForm
from graph_app.student_ai import (
    extract_pdf_text,
    extract_resume_fields,
    analyze_resume_with_groq,
    analyze_research_paper_with_groq,
    rule_based_resume_analysis,
    rule_based_research_paper_analysis,
    analyze_resume_rule_based_json,
    generate_career_summary_payload,
)
from graph_app.resume_parser import analyze_resume_file
from graph_app.services.resume_ai_service import extract_resume_text, generate_resume_ai_insights
from graph_app.services.supabase_storage import (
    delete_resume_from_supabase,
    upload_resume_to_supabase,
)


# Always load environment variables from the Django app root:
# e:\publication summary generator\django application\Saransha\.env
APP_ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
ENV_FILE_PATH = os.path.join(APP_ROOT_DIR, ".env")
load_dotenv(dotenv_path=ENV_FILE_PATH)


def _sync_signup_to_supabase_auth(email: str, password: str, username: str, role: str):
    """
    Create a corresponding Supabase Auth user so the signup is visible in Supabase.
    Returns (ok: bool, error_message: str).
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        return False, "Supabase credentials are missing in .env (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)."

    try:
        client = create_client(supabase_url, service_role_key)

        # Prevent duplicate auth users.
        users_page = client.auth.admin.list_users(page=1, per_page=1000)
        existing = [
            u for u in (getattr(users_page, "users", []) or [])
            if (getattr(u, "email", "") or "").lower().strip() == email.lower().strip()
        ]
        if existing:
            return False, "Email already exists in Supabase Auth. Please use another email."

        client.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "name": username,
                    "role": role,
                },
            }
        )
        return True, ""
    except Exception as exc:
        return False, f"Supabase signup sync failed: {exc}"


def parse_resume(pdf_path: str):
    """
    Backward-compatible local parser wrapper for older upload flow.
    """
    with open(pdf_path, "rb") as f:
        text = extract_pdf_text(f)
    fields = extract_resume_fields(text) or {}
    return {
        "name": fields.get("name", ""),
        "email": fields.get("email", ""),
        "phone": fields.get("phone", ""),
        "education": fields.get("education", []),
        "skills": fields.get("skills", []),
        "projects": fields.get("projects", []),
        "experience": fields.get("experience", []),
        "cleaned_text": text,
    }


def _extract_resume_extras_from_text(text: str):
    lines = [ln.strip(" \t-•") for ln in (text or "").splitlines() if ln.strip()]
    cert_keywords = ("certified", "certification", "certificate", "course", "training", "udemy", "coursera", "nptel")
    achievement_keywords = ("award", "winner", "won", "rank", "achievement", "honor", "honour", "accomplish", "competition")
    certifications = []
    achievements = []
    for ln in lines:
        low = ln.lower()
        if len(ln) >= 5 and any(k in low for k in cert_keywords):
            certifications.append(ln)
        if len(ln) >= 5 and any(k in low for k in achievement_keywords):
            achievements.append(ln)

    urls = re.findall(r"(?:(?:https?://)|(?:www\.))[^\s<>\]\)\"']+", text or "", flags=re.I)
    social_links = []
    for raw in urls:
        url = raw.rstrip(".,;)]}").strip()
        if not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            url = f"https://{url}"
        low = url.lower()
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
        social_links.append({"platform": platform, "url": url})

    return {
        "certifications": _normalize_certifications(certifications),
        "achievements": list(dict.fromkeys(achievements))[:20],
        "social_links": _normalize_social_links(social_links),
    }


def analyze_student_resume_pdf(pdf_bytes: bytes, target_role="Software Developer", student_profile_skills=""):
    """
    Backward-compatible adapter for old resume analyzer payload shape.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        temp_pdf_path = tmp.name
    try:
        with open(temp_pdf_path, "rb") as f:
            result = analyze_resume_file(
                f,
                student_profile=None,
                target_role=(target_role or "").strip(),
            )
    finally:
        try:
            os.remove(temp_pdf_path)
        except Exception:
            pass

    parsed_profile = result.get("parsed_profile", {}) or {}
    analysis = result.get("analysis", {}) or {}
    return {
        "ok": True,
        "parsed": parsed_profile,
        "confidence_by_field": result.get("confidence", {}) or {},
        "ats": {
            "score": analysis.get("ats_score", 0),
            "missing_skills": analysis.get("missing_skills", []),
            "matched_skills": analysis.get("matched_skills", []),
            "matched_keywords": analysis.get("matched_keywords", []),
            "missing_keywords": analysis.get("missing_keywords", []),
            "breakdown": analysis.get("breakdown", {}),
            "mode": analysis.get("mode", "general"),
            "job_role": analysis.get("job_role"),
            "suggestions": analysis.get("suggestions", []),
        },
        "domain_match": {
            "target_role": analysis.get("job_role") or target_role,
            "score": analysis.get("role_match_score", 0),
            "role_match": analysis.get("role_match", ""),
        },
    }


FACULTY_ROLES = {'faculty', 'professor', 'associate professor', 'assistant professor'}


def _get_effective_role(user: Users_Publication) -> str:
    """
    Prefer the new `role` field; fall back to legacy `user_category` for backward compatibility.
    """
    role = (getattr(user, "role", "") or "").lower().strip()
    if role in {"student", "faculty", "organization"}:
        # Treat organization accounts as student for dashboard/routing purposes.
        return "student" if role == "organization" else role

    user_category = (getattr(user, "user_category", "") or "").lower().strip()
    if user_category in FACULTY_ROLES:
        return "faculty"

    # Default to student if it's not clearly faculty.
    return "student"


def _get_logged_in_user(request):
    if "user_email" not in request.session:
        return None
    return Users_Publication.objects.filter(user_email=request.session["user_email"]).first()


def _get_supabase_service_client():
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        raise ValueError("Supabase credentials are missing in .env")
    return create_client(supabase_url, service_role_key)


def _resolve_message_text(row):
    return (
        row.get("content")
        or row.get("message")
        or row.get("text")
        or row.get("body")
        or ""
    )


def _resolve_row_ts(row):
    return (
        row.get("created_at")
        or row.get("sent_at")
        or row.get("timestamp")
        or row.get("updated_at")
        or ""
    )


def _create_message_row(client, payload):
    for text_key in ["content", "message", "text", "body"]:
        try:
            insert_payload = dict(payload)
            if "content" in insert_payload and text_key != "content":
                insert_payload[text_key] = insert_payload.pop("content")
            inserted = client.table("messages").insert(insert_payload).execute()
            return (inserted.data or [None])[0]
        except Exception:
            continue
    raise ValueError("Could not insert message row using known text fields.")


def _find_existing_conversation(client, me_id, peer_id):
    column_pairs = [
        ("participant_1_id", "participant_2_id"),
        ("participant_one_id", "participant_two_id"),
        ("user1_id", "user2_id"),
        ("user_a_id", "user_b_id"),
    ]
    for c1, c2 in column_pairs:
        try:
            expr = f"and({c1}.eq.{me_id},{c2}.eq.{peer_id}),and({c1}.eq.{peer_id},{c2}.eq.{me_id})"
            res = client.table("conversations").select("*").or_(expr).limit(1).execute()
            if res.data:
                return res.data[0], c1, c2
        except Exception:
            continue
    return None, None, None


def _resolve_supabase_user_id(client, email: str):
    target = (email or "").strip().lower()
    if not target:
        return None
    try:
        users_page = client.auth.admin.list_users(page=1, per_page=1000)
        for row in (getattr(users_page, "users", []) or []):
            if (getattr(row, "email", "") or "").strip().lower() == target:
                return str(getattr(row, "id", "") or "")
    except Exception:
        return None
    return None


def _conversation_field_pairs():
    return [
        ("participant_1_id", "participant_2_id"),
        ("participant_one_id", "participant_two_id"),
        ("user1_id", "user2_id"),
        ("user_a_id", "user_b_id"),
        ("sender_id", "receiver_id"),
        ("from_user_id", "to_user_id"),
        ("student_id", "faculty_id"),
    ]


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    if isinstance(value, list):
        cleaned = []
        for v in value:
            if isinstance(v, dict):
                cleaned.append(v)
            else:
                text = str(v).strip()
                if text:
                    cleaned.append(text)
        return cleaned
    if isinstance(value, str):
        return [ln.strip() for ln in value.splitlines() if ln.strip()]
    return []


def _split_tokens(value):
    if not value:
        return []
    text = str(value).replace("\n", ",")
    return [x.strip() for x in text.split(",") if x.strip()]


def _clean_text_value(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.startswith("{") and text.endswith("}"):
        parsed = _parse_loose_dict(text)
        if parsed:
            return ""
    return text


def _normalize_plain_list(entries):
    cleaned = []
    seen = set()
    for item in _as_list(entries):
        text = _clean_text_value(item) if not isinstance(item, dict) else ""
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _normalize_education_entries(entries):
    normalized = []
    seen = set()
    for item in _as_list(entries):
        row = _safe_dict(item) if isinstance(item, dict) else {}
        if row:
            degree = (
                row.get("degree")
                or row.get("qualification")
                or row.get("course")
                or row.get("program")
                or ""
            ).strip()
            institute = (
                row.get("institute")
                or row.get("institution")
                or row.get("college")
                or row.get("school")
                or row.get("university")
                or ""
            ).strip()
            duration = (
                row.get("duration")
                or row.get("year")
                or row.get("years")
                or row.get("batch")
                or ""
            ).strip()
            score = (
                row.get("cgpa")
                or row.get("gpa")
                or row.get("percentage")
                or row.get("marks")
                or ""
            ).strip()
            line = row.get("details", "").strip()
            if not line:
                parts = [x for x in [degree, institute] if x]
                line = " - ".join(parts) if parts else ""
                meta = " | ".join([x for x in [duration, score] if x])
                if meta:
                    line = f"{line} ({meta})" if line else meta
            if line:
                key = line.lower()
                if key not in seen:
                    seen.add(key)
                    normalized.append(
                        {
                            "degree": degree,
                            "institute": institute,
                            "duration": duration,
                            "score": score,
                            "details": line,
                        }
                    )
            continue

        text = _clean_text_value(item)
        if text:
            key = text.lower()
            if key not in seen:
                seen.add(key)
                normalized.append(
                    {
                        "degree": "",
                        "institute": "",
                        "duration": "",
                        "score": "",
                        "details": text,
                    }
                )
    return normalized


def _infer_tech_stack_from_text(text):
    if not text:
        return []
    known = [
        "Python",
        "Django",
        "Flask",
        "FastAPI",
        "Java",
        "C++",
        "C",
        "C#",
        "JavaScript",
        "TypeScript",
        "React",
        "Node.js",
        "SQL",
        "PostgreSQL",
        "MySQL",
        "MongoDB",
        "Git",
        "Docker",
        "Kubernetes",
        "AWS",
        "Azure",
        "GCP",
        "Pandas",
        "NumPy",
        "TensorFlow",
        "PyTorch",
        "Scikit-learn",
        "HTML",
        "CSS",
        "Bootstrap",
    ]
    low = text.lower()
    found = []
    for skill in known:
        if skill.lower() in low and skill not in found:
            found.append(skill)
    return found


def _normalize_projects(projects):
    normalized = []
    seen = set()
    for item in _as_list(projects):
        row = _safe_dict(item) if isinstance(item, dict) else {}
        if row:
            title = (
                row.get("title")
                or row.get("name")
                or row.get("project_name")
                or ""
            ).strip()
            description = (
                row.get("description")
                or row.get("details")
                or row.get("role")
                or ""
            ).strip()
            tech_stack = row.get("tech_stack") or row.get("technologies") or row.get("tools") or []
            if isinstance(tech_stack, str):
                tech_stack = _split_tokens(tech_stack)
            elif isinstance(tech_stack, list):
                tech_stack = [str(x).strip() for x in tech_stack if str(x).strip()]
            else:
                tech_stack = []
            dedup_tech = []
            seen_tech = set()
            for tech in tech_stack:
                key = tech.lower()
                if key in seen_tech:
                    continue
                seen_tech.add(key)
                dedup_tech.append(tech)
            tech_stack = dedup_tech
            if not tech_stack:
                tech_stack = _infer_tech_stack_from_text(f"{title}. {description}")
            links = row.get("links") or row.get("url") or row.get("github") or []
            if isinstance(links, str):
                links = _split_tokens(links)
            elif not isinstance(links, list):
                links = []
            if not title and description:
                title = description[:80]
            if title or description:
                key = (title.lower(), description.lower())
                if key not in seen:
                    seen.add(key)
                    normalized.append(
                        {
                            "title": title or "Project",
                            "description": description,
                            "tech_stack": tech_stack,
                            "tech_display": ", ".join(tech_stack),
                            "links": links,
                        }
                    )
            continue

        text = _clean_text_value(item)
        if text:
            key = (text.lower(), "")
            if key not in seen:
                seen.add(key)
                inferred = _infer_tech_stack_from_text(text)
                normalized.append(
                    {
                        "title": text[:80] if text else "Project",
                        "description": text,
                        "tech_stack": inferred,
                        "tech_display": ", ".join(inferred),
                        "links": [],
                    }
                )
    return normalized


def _normalize_certifications(entries):
    normalized = []
    seen = set()
    for item in _as_list(entries):
        row = _safe_dict(item) if isinstance(item, dict) else {}
        if row:
            name = (row.get("name") or row.get("certification") or row.get("title") or "").strip()
            issuer = (row.get("issuer") or row.get("organization") or "").strip()
            year = (row.get("year") or "").strip()
            line = " - ".join([x for x in [name, issuer] if x])
            if year:
                line = f"{line} ({year})" if line else year
            if line:
                key = line.lower()
                if key not in seen:
                    seen.add(key)
                    normalized.append(line)
            continue

        text = _clean_text_value(item)
        if text:
            low = text.lower()
            if any(noise in low for noise in ["responsible for", "experience in", "worked on"]) and len(text) > 110:
                continue
            key = text.lower()
            if key not in seen:
                seen.add(key)
                normalized.append(text)
    return normalized


def _normalize_social_links(entries):
    normalized = []
    seen = set()
    for item in _as_list(entries):
        row = _safe_dict(item) if isinstance(item, dict) else {}
        platform = ""
        url = ""
        if row:
            platform = (row.get("platform") or row.get("type") or "").strip()
            url = (row.get("url") or row.get("link") or "").strip()
        else:
            text = str(item).strip()
            if text.startswith("http://") or text.startswith("https://") or text.startswith("www."):
                url = text
        if not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            url = f"https://{url}"
        low = url.lower()
        if not platform:
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
        key = (platform.lower(), url.lower())
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"platform": platform, "url": url})
    return normalized


def _parse_loose_dict(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text.startswith("{") or not text.endswith("}"):
        return {}
    try:
        # First try strict JSON (some sources store JSON strings).
        parsed_json = json.loads(text)
        return parsed_json if isinstance(parsed_json, dict) else {}
    except Exception:
        pass
    try:
        parsed = ast.literal_eval(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalize_project_entry(item):
    raw = _safe_dict(item)
    if not raw and isinstance(item, str):
        raw = _parse_loose_dict(item)

    name = _clean_text_value(
        raw.get("title")
        or raw.get("name")
        or raw.get("Name")
        or ""
    ) or "Untitled Project"
    role = _clean_text_value(
        raw.get("description")
        or raw.get("role")
        or raw.get("roles")
        or raw.get("Roles")
        or ""
    )
    tech_value = (
        raw.get("tech_stack")
        or raw.get("key_technologies")
        or raw.get("Key_Technologies")
        or raw.get("technologies")
        or []
    )
    if isinstance(tech_value, str):
        tech_list = _split_tokens(tech_value)
    elif isinstance(tech_value, list):
        tech_list = [t for t in (_clean_text_value(x) for x in tech_value) if t]
    else:
        tech_list = []

    links = raw.get("links") or []
    if isinstance(links, str):
        links = _split_tokens(links)
    if not isinstance(links, list):
        links = []
    links = [u for u in (_clean_text_value(x) for x in links) if u]

    if not raw and isinstance(item, str):
        return {
            "name": item[:80],
            "role": item,
            "tech_stack": [],
            "links": [],
        }

    return {
        "name": str(name).strip(),
        "role": str(role).strip(),
        "tech_stack": tech_list,
        "links": links,
    }


def _safe_list(value):
    return value if isinstance(value, list) else []


def _profile_sections_for_view(profile):
    projects = [_normalize_project_entry(item) for item in _safe_list(profile.project_entries)]
    for project in projects:
        tech_stack = [t for t in _split_tokens(",".join(_as_list(project.get("tech_stack", [])))) if t]
        dedup = []
        seen = set()
        for tech in tech_stack:
            key = tech.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(tech)
        project["tech_stack"] = dedup
        project["tech_display"] = ", ".join(dedup)
    education = []
    for item in _safe_list(profile.education_entries):
        row = _safe_dict(item) if isinstance(item, dict) else {}
        if not row and isinstance(item, str):
            row = _parse_loose_dict(item)
        text_fallback = _clean_text_value(item) if not row else ""
        record = {
            "degree": _clean_text_value(row.get("degree") or row.get("qualification") or row.get("course") or "") if row else "",
            "institute": _clean_text_value(row.get("institute") or row.get("institution") or row.get("college") or "") if row else "",
            "duration": _clean_text_value(row.get("duration") or row.get("year") or row.get("batch") or "") if row else "",
            "score": _clean_text_value(row.get("score") or row.get("percentage") or row.get("cgpa") or row.get("gpa") or "") if row else "",
            "details": _clean_text_value(row.get("details") or row.get("description") or "") if row else text_fallback,
        }
        if any(record.values()):
            education.append(record)
    experience = []
    for item in _safe_list(profile.experience_entries):
        row = _safe_dict(item) if isinstance(item, dict) else {}
        if not row and isinstance(item, str):
            row = _parse_loose_dict(item)
        text_fallback = _clean_text_value(item) if not row else ""
        record = {
            "title": _clean_text_value(row.get("title") or row.get("role") or row.get("position") or "") if row else "",
            "organization": _clean_text_value(row.get("organization") or row.get("company") or row.get("employer") or "") if row else "",
            "duration": _clean_text_value(row.get("duration") or row.get("year") or row.get("dates") or "") if row else "",
            "description": _clean_text_value(row.get("description") or row.get("details") or "") if row else text_fallback,
        }
        if any(record.values()):
            experience.append(record)
    certifications = []
    for item in _safe_list(profile.certification_entries):
        row = _safe_dict(item) if isinstance(item, dict) else {}
        if not row and isinstance(item, str):
            row = _parse_loose_dict(item)
        text_fallback = _clean_text_value(item) if not row else ""
        record = {
            "name": _clean_text_value(row.get("name") or row.get("certification") or row.get("title") or "") if row else text_fallback,
            "issuer": _clean_text_value(row.get("issuer") or row.get("organization") or row.get("source") or "") if row else "",
            "year": _clean_text_value(row.get("year") or "") if row else "",
            "link": _clean_text_value(row.get("link") or row.get("url") or "") if row else "",
            "file_url": _clean_text_value(row.get("file_url") or "") if row else "",
            "file_name": _clean_text_value(row.get("file_name") or "") if row else "",
        }
        # filter obvious non-certification paragraphs/noise
        noise_text = (record.get("name") or "").lower()
        if record.get("name") and len(record["name"]) > 140 and any(k in noise_text for k in ["extracts crucial details", "resume", "experience", "education"]):
            continue
        if any(record.values()) and record.get("name"):
            certifications.append(record)
    social_links = _normalize_social_links(profile.social_links)
    return {
        "projects": projects,
        "education": education,
        "experience": experience,
        "certifications": certifications,
        "interests": _normalize_plain_list(profile.interests_entries),
        "languages": _normalize_plain_list(profile.languages_entries),
        "achievements": _normalize_plain_list(profile.achievements_entries),
        "social_links": social_links,
    }


def _normalize_resume_display_data(result=None, resume_fields=None, resume_analysis_json=None, groq_analysis=None):
    result = _safe_dict(result)
    resume_fields = _safe_dict(resume_fields)
    resume_analysis_json = _safe_dict(resume_analysis_json)
    groq_analysis = _safe_dict(groq_analysis)

    parsed_from_result = _safe_dict(result.get("parsed")) or _safe_dict(result.get("parsed_profile"))
    parsed_from_fields = _safe_dict(resume_fields)

    parsed = {
        "name": parsed_from_result.get("name") or parsed_from_fields.get("name", ""),
        "email": parsed_from_result.get("email") or parsed_from_fields.get("email", ""),
        "phone": parsed_from_result.get("phone") or parsed_from_fields.get("phone", ""),
        "summary": groq_analysis.get("ai_summary", "") or parsed_from_result.get("summary") or result.get("student_summary", ""),
        "skills": groq_analysis.get("skills", []) or parsed_from_result.get("skills") or parsed_from_fields.get("skills", []),
        "education": parsed_from_result.get("education") or parsed_from_fields.get("education", []),
        "experience": parsed_from_result.get("experience") or parsed_from_fields.get("experience", []),
        "projects": parsed_from_result.get("projects") or parsed_from_fields.get("projects", []),
        "certifications": parsed_from_result.get("certifications") or parsed_from_fields.get("certifications", []),
        "achievements": parsed_from_result.get("achievements") or parsed_from_fields.get("achievements", []),
        "social_links": parsed_from_result.get("social_links") or parsed_from_fields.get("social_links", []),
    }

    ats_payload = _safe_dict(result.get("ats"))
    parser_analysis_payload = _safe_dict(result.get("analysis"))
    if not ats_payload and parser_analysis_payload:
        ats_payload = parser_analysis_payload
    domain_payload = _safe_dict(result.get("domain_match"))
    keyword_payload = _safe_dict(ats_payload.get("keyword_optimization"))
    job_match_payload = _safe_dict(resume_analysis_json.get("job_match"))

    resume_json_ats = _safe_dict(resume_analysis_json.get("ats_score"))
    if resume_json_ats:
        keyword_payload = keyword_payload or _safe_dict(resume_json_ats.get("keyword_optimization"))

    match_score = domain_payload.get("score")
    if match_score is None:
        match_score = domain_payload.get("role_match_score")
    if match_score is None:
        match_score = job_match_payload.get("match_percentage")

    predicted_role = (
        domain_payload.get("role_match")
        or ats_payload.get("role_match")
        or job_match_payload.get("target_role")
        or ""
    )
    if not predicted_role:
        predicted_roles = _as_list(groq_analysis.get("predicted_roles", []))
        predicted_role = predicted_roles[0] if predicted_roles else "Not predicted yet"

    missing_skills = ats_payload.get("missing_skills") or job_match_payload.get("missing_skills") or []
    resume_tips = ats_payload.get("suggestions") or resume_analysis_json.get("suggestions") or groq_analysis.get("ai_suggestions", [])

    ats_mode = ats_payload.get("mode") or ("role-based" if job_match_payload.get("target_role") else "general")
    ats_job_role = ats_payload.get("job_role") or job_match_payload.get("target_role") or ""
    if not ats_job_role:
        ats_mode = "general"

    ats_score_raw = ats_payload.get("score", ats_payload.get("ats_score"))
    if ats_score_raw in (None, "", 0):
        ats_score_raw = resume_json_ats.get("ats_compatibility")
    if ats_score_raw in (None, ""):
        ats_score_raw = groq_analysis.get("ats_score")
    ats_available = ats_score_raw not in (None, "")
    ats_score = int(ats_score_raw) if str(ats_score_raw).isdigit() else ats_score_raw
    keyword_match_percentage = keyword_payload.get("match_percentage", 0)
    if keyword_match_percentage in (None, ""):
        keyword_match_percentage = _safe_dict(ats_payload.get("breakdown")).get("keywords", 0)

    # --- Final Unified Payload ---
    extracted = {
        "personal_info": {
            "name": parsed["name"],
            "email": parsed["email"],
            "phone": parsed["phone"],
            "summary": parsed["summary"],
        },
        "skills": _as_list(parsed["skills"]),
        "education": _normalize_education_entries(parsed["education"]),
        "experience": _normalize_plain_list(parsed["experience"]),
        "interests": _normalize_plain_list(parsed_from_fields.get("interests", [])),
        "projects": _normalize_projects(parsed["projects"]),
        "certifications": _normalize_certifications(parsed["certifications"]),
        "achievements": _normalize_plain_list(parsed["achievements"]),
        "social_links": _normalize_social_links(parsed["social_links"]),
        "analysis": {
            "predicted_role": predicted_role,
            "match_score": match_score or 0,
            "ats_score": ats_score,
            "ats_available": ats_available,
            "ats_display": f"{ats_score}%" if ats_available and isinstance(ats_score, int) else "Not analyzed yet",
            "mode": ats_mode,
            "job_role": ats_job_role,
            "missing_skills": _as_list(missing_skills),
            "keyword_match_percentage": keyword_match_percentage or 0,
            "matched_keywords": _as_list(keyword_payload.get("matched_keywords", [])),
            "missing_keywords": _as_list(keyword_payload.get("missing_keywords", [])),
            "resume_tips": _as_list(resume_tips),
        },
    }
    return extracted


def _build_resume_text_from_fields(resume_fields):
    if not isinstance(resume_fields, dict):
        return ""
    parts = []
    for key in ["name", "email", "phone", "summary", "skills", "education", "experience", "projects", "certifications", "achievements", "social_links"]:
        value = resume_fields.get(key, "")
        if isinstance(value, list):
            value = "\n".join(str(v) for v in value if str(v).strip())
        if value:
            parts.append(f"{key.title()}:\n{value}")
    return "\n\n".join(parts).strip()


def _student_resume_state(request, profile):
    result_payload = _safe_dict(request.session.get("student_resume_result"))
    resume_fields = _safe_dict(request.session.get("resume_fields", {}))
    resume_analysis_json = _safe_dict(request.session.get("resume_analysis_json", {}))
    groq_analysis = _safe_dict(request.session.get("groq_resume_analysis", {}))
    extracted_details = _normalize_resume_display_data(
        result=result_payload,
        resume_fields=resume_fields,
        resume_analysis_json=resume_analysis_json,
        groq_analysis=groq_analysis,
    )
    has_extracted = any(
        [
            extracted_details.get("personal_info", {}).get("name"),
            extracted_details.get("skills"),
            extracted_details.get("education"),
            extracted_details.get("experience"),
            extracted_details.get("projects"),
        ]
    )
    has_profile = bool(
        profile.summary
        or profile.skills.strip()
        or profile.project_entries
        or profile.education_entries
        or profile.experience_entries
    )
    resume_text = request.session.get("resume_text", "") or _build_resume_text_from_fields(resume_fields)
    return {
        "result_payload": result_payload,
        "resume_fields": resume_fields,
        "resume_analysis_json": resume_analysis_json,
        "groq_analysis": groq_analysis,
        "extracted_details": extracted_details,
        "has_resume_data": bool(has_extracted or has_profile),
        "resume_text": resume_text,
    }


def _autofill_student_profile_from_extracted(profile, extracted_details):
    """
    Fill StudentProfile fields from extracted resume data.
    """
    personal = _safe_dict(extracted_details.get("personal_info"))
    skills = extracted_details.get("skills", [])
    experience = extracted_details.get("experience", [])
    projects = extracted_details.get("projects", [])

    project_lines = []
    for project in projects:
        p = _safe_dict(project)
        title = (p.get("title") or "").strip()
        desc = (p.get("description") or "").strip()
        if title and desc and title != desc:
            project_lines.append(f"{title}: {desc}")
        elif desc:
            project_lines.append(desc)
        elif title:
            project_lines.append(title)

    profile.full_name = (personal.get("name") or profile.full_name or "").strip()
    profile.email = (personal.get("email") or profile.email or "").strip()
    profile.phone = (personal.get("phone") or profile.phone or "").strip()
    profile.summary = (personal.get("summary") or profile.summary or "").strip()
    profile.skills = "\n".join(_as_list(skills))
    profile.experience = "\n".join(_as_list(experience))
    profile.projects = "\n".join(project_lines)
    if not profile.education_entries:
        normalized_education = []
        for item in extracted_details.get("education", []):
            row = _safe_dict(item) if isinstance(item, dict) else {}
            if row:
                normalized_education.append(
                    {
                        "institute": row.get("institute", ""),
                        "degree": row.get("degree", ""),
                        "duration": row.get("duration", ""),
                        "score": row.get("score", ""),
                        "details": row.get("details", ""),
                    }
                )
            else:
                text = _clean_text_value(item)
                if text:
                    normalized_education.append({"institute": "", "degree": "", "duration": "", "score": "", "details": text})
        profile.education_entries = normalized_education
    if not profile.experience_entries:
        profile.experience_entries = [{"title": "", "organization": "", "duration": "", "description": item} for item in extracted_details.get("experience", [])]
    if not profile.project_entries:
        profile.project_entries = projects
    if extracted_details.get("certifications"):
        merged_certifications = list(_safe_list(profile.certification_entries))
        seen_cert = {(str(_safe_dict(c).get("name", c)).strip().lower()) for c in merged_certifications}
        for item in extracted_details.get("certifications", []):
            text = _clean_text_value(item)
            key = text.lower()
            if key and key not in seen_cert:
                merged_certifications.append({"name": text, "issuer": "", "year": "", "link": ""})
                seen_cert.add(key)
        profile.certification_entries = merged_certifications
    if extracted_details.get("achievements"):
        merged_achievements = [str(x).strip() for x in _safe_list(profile.achievements_entries) if str(x).strip()]
        seen_ach = {x.lower() for x in merged_achievements}
        for item in extracted_details.get("achievements", []):
            text = str(item).strip()
            if text and text.lower() not in seen_ach:
                merged_achievements.append(text)
                seen_ach.add(text.lower())
        profile.achievements_entries = merged_achievements
    if extracted_details.get("social_links"):
        merged_links = _normalize_social_links(profile.social_links) + _normalize_social_links(extracted_details.get("social_links", []))
        profile.social_links = _normalize_social_links(merged_links)
    if not profile.interests_entries and extracted_details.get("interests"):
        profile.interests_entries = _as_list(extracted_details.get("interests"))
    profile.save()


def _section_field_map():
    return {
        "education": "education_entries",
        "projects": "project_entries",
        "experience": "experience_entries",
        "certifications": "certification_entries",
        "interests": "interests_entries",
        "languages": "languages_entries",
        "achievements": "achievements_entries",
        "social_links": "social_links",
    }


def _clean_section_item(section, post_data):
    if section == "education":
        return {
            "institute": post_data.get("institute", "").strip(),
            "degree": post_data.get("degree", "").strip(),
            "duration": post_data.get("duration", "").strip(),
            "details": post_data.get("details", "").strip(),
        }
    if section == "projects":
        tech_stack = [x.strip() for x in post_data.get("tech_stack", "").split(",") if x.strip()]
        links = [x.strip() for x in post_data.get("links", "").split(",") if x.strip()]
        return {
            "title": post_data.get("title", "").strip(),
            "description": post_data.get("description", "").strip(),
            "tech_stack": tech_stack,
            "links": links,
        }
    if section == "experience":
        return {
            "title": post_data.get("title", "").strip(),
            "organization": post_data.get("organization", "").strip(),
            "duration": post_data.get("duration", "").strip(),
            "description": post_data.get("description", "").strip(),
        }
    if section == "certifications":
        return {
            "name": post_data.get("name", "").strip(),
            "issuer": post_data.get("issuer", "").strip(),
            "year": post_data.get("year", "").strip(),
            "link": post_data.get("link", "").strip(),
            "file_url": post_data.get("file_url", "").strip(),
            "file_name": post_data.get("file_name", "").strip(),
        }
    if section == "social_links":
        return {
            "platform": post_data.get("platform", "").strip(),
            "url": post_data.get("url", "").strip(),
        }
    return post_data.get("value", "").strip()


def _profile_completeness(profile):
    checks = [
        bool(profile.full_name),
        bool(profile.email),
        bool(profile.phone),
        bool(profile.summary),
        bool(profile.education_entries),
        bool(profile.skills.strip()),
        bool(profile.project_entries),
        bool(profile.experience_entries),
        bool(profile.certification_entries),
        bool(profile.interests_entries),
        bool(profile.languages_entries),
        bool(profile.achievements_entries),
        bool(profile.social_links),
        bool(profile.target_role),
        bool(profile.location),
        bool(profile.availability),
    ]
    return int((sum(1 for x in checks if x) / len(checks)) * 100)


def _build_user_profile_context(request):
    """
    Build a concise profile context block for chatbot responses.
    """
    user = _get_logged_in_user(request)
    if user is None:
        return "No logged-in user profile context is available."

    role = _get_effective_role(user)
    lines = [
        f"User Name: {getattr(user, 'user_name', '') or 'N/A'}",
        f"User Email: {getattr(user, 'user_email', '') or 'N/A'}",
        f"User Role: {role}",
    ]

    if role == "student":
        profile = StudentProfile.objects.filter(user=user).first()
        resume_fields = request.session.get("resume_fields", {}) or {}
        resume_analysis_json = request.session.get("resume_analysis_json", {}) or {}

        if profile:
            lines.extend(
                [
                    f"Student Full Name: {profile.full_name or 'N/A'}",
                    f"Student CGPA: {profile.cgpa if profile.cgpa is not None else 'N/A'}",
                    f"Student Skills (manual): {profile.skills or 'N/A'}",
                    f"Student Interests: {profile.interests or 'N/A'}",
                    f"Student Projects (manual): {profile.projects or 'N/A'}",
                    f"Student Experience (manual): {profile.experience or 'N/A'}",
                ]
            )

        lines.extend(
            [
                f"Resume Extracted Name: {resume_fields.get('name') or 'N/A'}",
                f"Resume Extracted Email: {resume_fields.get('email') or 'N/A'}",
                f"Resume Extracted Phone: {resume_fields.get('phone') or 'N/A'}",
                f"Resume Extracted Skills: {resume_fields.get('skills') or 'N/A'}",
                f"Resume Extracted Projects: {resume_fields.get('projects') or 'N/A'}",
                f"Resume Extracted Experience: {resume_fields.get('experience') or 'N/A'}",
                f"Resume Score: {resume_analysis_json.get('resume_score', 'N/A')}",
                f"Strengths: {', '.join(resume_analysis_json.get('strengths', [])[:6]) or 'N/A'}",
                f"Weaknesses/Cons: {', '.join(resume_analysis_json.get('weaknesses_cons', [])[:6]) or 'N/A'}",
                f"Suggestions: {', '.join(resume_analysis_json.get('suggestions', [])[:6]) or 'N/A'}",
            ]
        )
    elif role == "faculty":
        profile = FacultyProfile.objects.filter(user=user).first()
        if profile:
            lines.extend(
                [
                    f"Faculty Full Name: {profile.full_name or 'N/A'}",
                    f"Designation: {profile.designation or 'N/A'}",
                    f"Department: {profile.department or 'N/A'}",
                    f"Research Interests: {profile.research_interests or 'N/A'}",
                ]
            )

    return "\n".join(lines)


def _talvyn_title_from_query(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return "New chat"
    words = text.split()
    return " ".join(words[:8])[:80]


def _talvyn_recent_context(conversation):
    if conversation is None:
        return ""
    rows = conversation.messages.order_by("-created_at")[:8]
    rows = list(reversed(rows))
    chunks = []
    for row in rows:
        prefix = "User" if row.role == "user" else "Talvyn"
        chunks.append(f"{prefix}: {row.content}")
    return "\n".join(chunks)


# =====================================================
# UPLOAD PAGE (EXCEL OR GOOGLE SCHOLAR URL)
# =====================================================

def upload_page(request):
    if "user_email" not in request.session:
        return redirect("login")

    # Faculty-only area; keep student users out of the Excel pipeline.
    user = _get_logged_in_user(request)
    if user and _get_effective_role(user) != "faculty":
        return redirect("student_dashboard")

    excel_data = []
    publications_data = []
    error_message = None
    success_message = None
    profile_url_value = ""

    # Pending (approval) state: keep fetched Scholar results as preview until user confirms "Add to Profile".
    pending_publications = request.session.get("faculty_pending_publications") or []
    pending_profile_url = request.session.get("faculty_pending_profile_url") or ""

    def _faculty_cache_path(fs_obj, current_user):
        # Per-user cache to avoid overwriting between faculty accounts.
        return fs_obj.path(f"all_authors_publications_{getattr(current_user, 'id', '0')}.xlsx")

    def _as_int(value, default=0):
        try:
            if value is None:
                return default
            if isinstance(value, str) and value.strip() == "":
                return default
            return int(float(value))
        except Exception:
            return default

    def _upsert_faculty_publications(profile, pubs):
        """
        Store Google Scholar fetched publications into DB for faculty profile display.
        """
        if profile is None:
            return 0
        created_or_updated = 0
        for row in pubs or []:
            title = (row.get("Title") or "").strip()
            if not title:
                continue
            year = row.get("Year")
            year_int = _as_int(year, default=0)
            if year_int <= 0:
                year_int = datetime.now().year

            cited_by = _as_int(row.get("Cited by"), default=0)
            journal = (row.get("Journal") or "").strip()
            conference = (row.get("conference") or "").strip()
            venue = journal if journal and journal != "N/A" else (conference if conference and conference != "N/A" else "")
            main_author = (row.get("Main Author") or profile.full_name or "").strip()

            obj, created = Publication.objects.get_or_create(
                faculty=profile,
                title=title,
                year=year_int,
                defaults={
                    "main_author": main_author or (profile.full_name or ""),
                    "cited_by": cited_by,
                    "conference_journal": venue,
                    "co_author": (row.get("co_author") or "").strip(),
                },
            )
            if not created:
                changed = False
                if obj.cited_by != cited_by:
                    obj.cited_by = cited_by
                    changed = True
                if venue and (obj.conference_journal or "") != venue:
                    obj.conference_journal = venue
                    changed = True
                if main_author and (obj.main_author or "") != main_author:
                    obj.main_author = main_author
                    changed = True
                co_author = (row.get("co_author") or "").strip()
                if co_author and (obj.co_author or "") != co_author:
                    obj.co_author = co_author
                    changed = True
                if changed:
                    obj.save()
            created_or_updated += 1
        return created_or_updated

    # Persist last results on revisit (GET): load from the per-user cached export (or pending preview if present).
    try:
        fs = FileSystemStorage()
        if request.method != "POST":
            if pending_publications:
                publications_data = pending_publications
                profile_url_value = pending_profile_url
            else:
                cache_file = _faculty_cache_path(fs, user)
                if os.path.exists(cache_file):
                    df_prev = pd.read_excel(cache_file)
                    if not df_prev.empty:
                        publications_data = df_prev.to_dict(orient="records")
    except Exception:
        pass

    if request.method == "POST":
        # Handle approval action first (no refetch).
        decision = (request.POST.get("decision") or "").strip()
        if decision in {"add_to_profile", "do_not_add"}:
            if decision == "add_to_profile":
                try:
                    faculty_profile_obj, _ = FacultyProfile.objects.get_or_create(user=user)
                    pubs_to_add = request.session.get("faculty_pending_publications") or []
                    url_to_add = (request.session.get("faculty_pending_profile_url") or "").strip()
                    if url_to_add:
                        faculty_profile_obj.google_scholar_id = url_to_add
                        faculty_profile_obj.save(update_fields=["google_scholar_id"])
                    added = _upsert_faculty_publications(faculty_profile_obj, pubs_to_add) if pubs_to_add else 0
                    success_message = f"Added {added} publications to your profile."
                    messages.success(request, success_message)
                except Exception as exc:
                    error_message = f"Could not add publications to profile: {exc}"
                    messages.error(request, error_message)

            # In both cases, clear pending state.
            request.session.pop("faculty_pending_publications", None)
            request.session.pop("faculty_pending_profile_url", None)
            request.session.modified = True
            return redirect("upload")

        excel_file = request.FILES.get("excel_file")
        profile_url = request.POST.get("profile_url", "").strip()
        profile_url_value = profile_url

        # Neither provided
        if not excel_file and not profile_url:
            error_message = "Please upload an Excel file OR paste a Google Scholar profile URL."
            return render(request, "auth/upload.html", locals())

        fs = FileSystemStorage()

        # =================================================
        # CASE 1: GOOGLE SCHOLAR URL ONLY
        # =================================================
        if profile_url and not excel_file:
            try:
                print(f"[INFO] Processing single profile URL: {profile_url}")
                publications = get_publications_from_profile(profile_url, timeout=30, max_publications=100)

                if not publications:
                    error_message = "No publications found. Please check the Google Scholar profile URL."
                else:
                    publications_data = publications
                    success_message = f"Fetched {len(publications)} publications from Google Scholar."
                    
                    # Cache per-user and keep as PENDING until user approves adding to profile.
                    cache_file = _faculty_cache_path(fs, user)
                    pd.DataFrame(publications).to_excel(cache_file, index=False)
                    print(f"[SUCCESS] Saved to {cache_file}")

                    request.session["faculty_pending_publications"] = publications
                    request.session["faculty_pending_profile_url"] = profile_url
                    request.session.modified = True

                    messages.success(request, success_message)
                    return render(request, "auth/upload.html", locals())
                
                messages.error(request, error_message or "Failed to fetch Google Scholar data.")
                return render(request, "auth/upload.html", locals())

            except Exception as e:
                error_message = f"Error fetching Google Scholar data: {str(e)}"
                print(f"[ERROR] {error_message}")
                return render(request, "auth/upload.html", locals())

        # =================================================
        # CASE 2: EXCEL FILE UPLOAD
        # =================================================
        if excel_file:
            ext = os.path.splitext(excel_file.name)[1].lower()
            if ext not in [".xlsx", ".xls"]:
                error_message = "Invalid file type. Please upload an Excel file (.xlsx or .xls)."
                return render(request, "auth/upload.html", locals())

            filename = fs.save(excel_file.name, excel_file)
            file_path = fs.path(filename)

            # Validate Excel structure
            try:
                wb = openpyxl.load_workbook(file_path)
                ws = wb.active
                headers = [cell.value for cell in ws[1]]

                if "Profile URL" not in headers:
                    error_message = "Excel file must contain a 'Profile URL' column."
                    return render(request, "auth/upload.html", locals())

            except Exception as e:
                error_message = f"Invalid Excel file: {str(e)}"
                return render(request, "auth/upload.html", locals())

            # Process Excel
            try:
                output_file = fs.path("all_authors_publications.xlsx")
                process_profiles_from_excel(file_path, output_file)

                if not os.path.exists(output_file):
                    error_message = "Failed to generate output file."
                    return render(request, "auth/upload.html", locals())

                df = pd.read_excel(output_file)
                if df.empty:
                    error_message = "No publications found. Check Profile URLs."
                else:
                    publications_data = df.to_dict(orient="records")
                    success_message = f"Successfully processed {len(publications_data)} publications."
                    # Cache per-user for persistence on revisit (no approval needed for Excel workflow).
                    cache_file = _faculty_cache_path(fs, user)
                    try:
                        df.to_excel(cache_file, index=False)
                    except Exception:
                        pass

            except Exception as e:
                error_message = str(e)
                return render(request, "auth/upload.html", locals())

    # GET request (or POST that didn't early-return): always render the page.
    # This prevents Django from receiving `None` in any control-flow branch.
    return render(request, "auth/upload.html", locals())

# =====================================================
# GENERATE SUMMARY
# =====================================================

def generatesummary(request):
    if "user_email" not in request.session:
        return redirect("login")

    user = _get_logged_in_user(request)
    if user and _get_effective_role(user) != "faculty":
        return redirect("student_dashboard")

    fs = FileSystemStorage()
    output_file_path = fs.path("all_authors_publications.xlsx")

    authors = []
    result_df = pd.DataFrame()
    summary = pd.DataFrame()
    # Do NOT auto-display the full table on GET.
    # Only render results after an explicit POST action.
    data = ""
    error_message = None

    if not os.path.exists(output_file_path):
        error_message = "Please upload publication data first."
        return render(request, "auth/generatesummary.html", locals())

    try:
        df = pd.read_excel(output_file_path)
        if df.empty or "Main Author" not in df.columns:
            error_message = "Invalid data file. Please upload data again."
            return render(request, "auth/generatesummary.html", locals())

        authors = df["Main Author"].dropna().unique().tolist()

    except Exception as e:
        error_message = f"Error reading data file: {str(e)}"
        return render(request, "auth/generatesummary.html", locals())

    if request.method == "POST":
        faculty = request.POST.get("facultySelect", "All")
        start_year = int(request.POST.get("startYear", 0) or 0)
        end_year = int(request.POST.get("endYear", 9999) or 9999)
        sort_by = request.POST.get("sortBy", "")

        try:
            result_df = load_and_filter_excel(
                file_path=output_file_path,
                columns=[
                    "Main Author", "Title", "Journal", "conference",
                    "Publication Type", "Year", "Cited by", "co_author"
                ],
                column_name="Main Author",
                valid_names=authors if faculty == "All" else [faculty],
                year_range=[start_year, end_year],
                cited_by_sort_order=sort_by
            )
            
            if not result_df.empty:
                data = result_df.to_html(classes='table table-striped table-hover', index=False)

        except Exception as e:
            error_message = f"Error filtering data: {str(e)}"

        if "downloadSummary" in request.POST:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                result_df.to_excel(writer, index=False)
            buffer.seek(0)

            response = HttpResponse(
                buffer,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            response["Content-Disposition"] = 'attachment; filename="filtered_summary.xlsx"'
            return response

        if "generateSummary" in request.POST and not result_df.empty:
            try:
                summary = generate_author_summary(result_df)
                data = summary.to_html(classes='table table-striped table-hover', index=False)
            except Exception as e:
                error_message = f"Error generating summary: {str(e)}"

    return render(request, "auth/generatesummary.html", locals())


# =====================================================
# AUTH PAGES
# =====================================================

def home(request):
    # Always show landing page when "Home" is clicked.
    # Keep session intact; do not auto-redirect by role here.
    return render(request, "index.html")


def login(request):
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")

        user = Users_Publication.objects.filter(user_email=email).first()
        if user and str(user.user_password) == str(password):
            # Clear student analyzer-related session so results don't "carry" across profiles.
            for k in [
                "resume_text",
                "resume_fields",
                "resume_analysis_json",
                "resume_analysis_output",
                "paper_text",
                "paper_analysis_output",
            ]:
                request.session.pop(k, None)

            request.session["user_email"] = email

            role = _get_effective_role(user)
            if role == "student":
                return redirect("student_dashboard")
            if role == "faculty":
                return redirect("faculty_profile")
            return redirect("student_dashboard")

        return render(request, "login.html", {"error": "Invalid credentials"})

    return render(request, "login.html")


def signup(request):
    error_message = None
    
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        category = request.POST.get("category", "")
        category_value = (category or "").lower().strip()

        # Map signup selection into the required `role` values.
        role = category_value if category_value in {"student", "faculty", "organization"} else ""
        if role == "":
            # Backward compatibility: map legacy faculty wording to faculty, everything else to student.
            if category_value in FACULTY_ROLES:
                role = "faculty"
            else:
                role = "student"

        user_category = role
        
        # Validate required fields
        if not email or not username or not password or not category:
            error_message = "All fields are required. Please fill in all the information."
            return render(request, "signup.html", {"error": error_message})
        
        # Check if email already exists before attempting to create
        if Users_Publication.objects.filter(user_email=email).exists():
            error_message = "Email already registered. Please use a different email or try logging in."
            return render(request, "signup.html", {"error": error_message})
        
        # Create the user with error handling
        try:
            new_user = Users_Publication.objects.create(
                user_name=username,
                user_email=email,
                user_password=password,
                user_category=user_category,
                role=role,
            )

            # Keep Supabase Auth in sync with app signup.
            supabase_ok, supabase_error = _sync_signup_to_supabase_auth(
                email=email,
                password=password,
                username=username,
                role=role,
            )
            if not supabase_ok:
                # Roll back local user creation to avoid split-brain auth state.
                new_user.delete()
                return render(request, "signup.html", {"error": supabase_error})
            
            # Auto-create profile
            if role == "faculty":
                try:
                    FacultyProfile.objects.create(
                        user=new_user,
                        full_name=username  # Initialize with username
                    )
                except Exception as e:
                    # Log error but don't break signup process
                    print(f"Warning: Could not create FacultyProfile for {new_user.user_email}: {str(e)}")

            if role == "student":
                try:
                    StudentProfile.objects.create(
                        user=new_user,
                    )
                except Exception as e:
                    print(f"Warning: Could not create StudentProfile for {new_user.user_email}: {str(e)}")

            # Success - redirect to login
            messages.success(request, "Account created successfully! Please login.")
            return redirect("login")
            
        except IntegrityError as e:
            # Handle database integrity errors (e.g., unique constraint violations)
            error_message = "Email already registered. Please use a different email or try logging in."
            return render(request, "signup.html", {"error": error_message})
            
        except Exception as e:
            # Handle any other unexpected errors
            error_message = "An error occurred while creating your account. Please try again later."
            print(f"Error creating user: {str(e)}")
            return render(request, "signup.html", {"error": error_message})

    return render(request, "signup.html", {"error": error_message})


def logo_view(request):
    # Historically this endpoint flushed session; that caused accidental logouts
    # when users clicked navigation/logo links. Keep it as a safe redirect.
    return redirect("home")


def logout_view(request):
    request.session.flush()
    return redirect("home")


# =====================================================
# STUDENT MODULE (resume + paper analyzers)
# =====================================================

def student_dashboard(request):
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    # This legacy dashboard duplicates functionality available in Upload/Profile.
    # Keep the route, but redirect students to the Upload dashboard.
    return redirect("student_upload")

    # Prefill empty manual fields with account values (student can override anytime if not approved).
    if not profile.full_name and user.user_name:
        profile.full_name = user.user_name
    if not profile.email and user.user_email:
        profile.email = user.user_email
    if not profile.phone and getattr(user, "user_phone", None):
        profile.phone = user.user_phone
    if not profile.phone:
        # keep blank if user model doesn't have phone
        pass
    profile.save(update_fields=["full_name", "email"])

    # Handle profile update and approval request.
    if request.method == "POST":
        action = request.POST.get("action", "")

        # Freeze system: once approved, student cannot edit.
        # But allow students to re-request approval (which re-opens editing).
        if profile.is_approved and action != "request_approval":
            messages.warning(request, "Your profile is approved and is now read-only.")
            return redirect("student_dashboard")

        if action == "save_profile":
            full_name = request.POST.get("full_name", "").strip()
            email = request.POST.get("email", "").strip()
            phone = request.POST.get("phone", "").strip()
            cgpa_raw = request.POST.get("cgpa", "").strip()
            skills = request.POST.get("skills", "").strip()
            interests = request.POST.get("interests", "").strip()
            projects = request.POST.get("projects", "").strip()
            experience = request.POST.get("experience", "").strip()

            profile.full_name = full_name
            profile.email = email
            profile.phone = phone
            profile.skills = skills
            profile.interests = interests
            profile.projects = projects
            profile.experience = experience

            if cgpa_raw:
                try:
                    profile.cgpa = cgpa_raw
                except Exception:
                    profile.cgpa = None
            else:
                profile.cgpa = None

            profile.save()
            messages.success(request, "Profile saved. You can request approval when ready.")
            return redirect("student_dashboard")

        if action == "request_approval":
            profile.approval_status = "Pending"
            profile.is_approved = False
            profile.approval_requested = True
            profile.save(update_fields=["approval_status", "is_approved", "approval_requested"])
            messages.success(request, "Approval requested. Faculty will review your profile.")
            return redirect("student_dashboard")

    return render(
        request,
        "student/student_dashboard.html",
        {
            "profile": profile,
            "resume_analysis_json": resume_analysis_json,
            "paper_analysis_output": paper_analysis_output,
            "paper_uploaded": paper_uploaded,
            "nav_active": "home",
        },
    )


def student_profile(request):
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)
    extracted_details = _normalize_resume_display_data(
        result=request.session.get("student_resume_result"),
        resume_fields=request.session.get("resume_fields", {}),
        resume_analysis_json=request.session.get("resume_analysis_json", {}),
    )
    edit_mode = request.GET.get("edit") == "1"

    def _redirect_student_profile_edit():
        return redirect(f"{reverse('student_profile')}?edit=1")

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if profile.is_approved and action != "request_approval":
            messages.warning(request, "Your profile is approved and is now read-only.")
            return _redirect_student_profile_edit()

        if action == "save_personal":
            profile.full_name = request.POST.get("full_name", "").strip()
            profile.email = request.POST.get("email", "").strip()
            profile.phone = request.POST.get("phone", "").strip()
            profile.summary = request.POST.get("summary", "").strip()
            profile.target_role = request.POST.get("target_role", "").strip()
            profile.location = request.POST.get("location", "").strip()
            profile.availability = request.POST.get("availability", "").strip()
            profile.skills = request.POST.get("skills", "").strip()
            profile.save()
            messages.success(request, "Personal information updated.")
            return redirect("student_profile")

        if action in {"add_section", "edit_section", "delete_section"}:
            section = request.POST.get("section", "").strip()
            section_map = _section_field_map()
            if section not in section_map:
                messages.error(request, "Invalid section selected.")
                return _redirect_student_profile_edit()

            field_name = section_map[section]
            values = list(getattr(profile, field_name, []) or [])
            idx_raw = request.POST.get("index", "").strip()
            idx = int(idx_raw) if idx_raw.isdigit() else -1

            if action == "delete_section":
                if 0 <= idx < len(values):
                    values.pop(idx)
                    setattr(profile, field_name, values)
                    profile.save(update_fields=[field_name])
                    messages.success(request, f"{section.replace('_', ' ').title()} entry deleted.")
                else:
                    messages.error(request, "Entry not found for deletion.")
                return _redirect_student_profile_edit()

            item = _clean_section_item(section, request.POST)
            is_empty = False
            if isinstance(item, str):
                is_empty = not item
            elif isinstance(item, dict):
                is_empty = not any(bool(str(v).strip()) for v in item.values())
            if is_empty:
                messages.error(request, "Please enter details before saving.")
                return _redirect_student_profile_edit()

            if action == "edit_section" and 0 <= idx < len(values):
                values[idx] = item
            else:
                values.append(item)

            setattr(profile, field_name, values)
            profile.save(update_fields=[field_name])
            messages.success(request, f"{section.replace('_', ' ').title()} updated.")
            return _redirect_student_profile_edit()

        if action == "request_approval":
            profile.approval_status = "Pending"
            profile.is_approved = False
            profile.approval_requested = True
            profile.save(update_fields=["approval_status", "is_approved", "approval_requested"])
            messages.success(request, "Approval requested. Faculty will review your profile.")
            return _redirect_student_profile_edit()

    resume_analysis_json = request.session.get("resume_analysis_json", {})
    profile_completeness = _profile_completeness(profile)
    section_data = _profile_sections_for_view(profile)

    return render(
        request,
        "student/profile.html",
        {
            "profile": profile,
            "extracted_details": extracted_details,
            "resume_analysis_json": resume_analysis_json,
            "profile_completeness": profile_completeness,
            "skills_list": _split_tokens(profile.skills),
            "ai_suggestions_list": [line.strip("- ").strip() for line in (profile.ai_suggestions or "").splitlines() if line.strip()],
            "section_data": section_data,
            "edit_mode": edit_mode,
            "nav_active": "insights",
        },
    )


def upload_resume(request):
    """
    End-to-end resume pipeline:
    - Upload file to Supabase Storage (bucket: resumes)
    - Replace existing resume for this user (delete old object)
    - Extract text and run GROQ analysis
    - Persist AI + metadata to StudentProfile (PostgreSQL)
    """
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)
    error_message = None
    success_message = None

    if request.method == "POST":
        resume_file = request.FILES.get("resume_file")
        if not resume_file:
            error_message = "Please upload your resume (PDF, DOC, or DOCX)."
        else:
            allowed_extensions = (".pdf", ".doc", ".docx")
            lower_name = (resume_file.name or "").lower()
            if not lower_name.endswith(allowed_extensions):
                error_message = "Resume must be a PDF, DOC, or DOCX file."
            else:
                try:
                    resume_bytes = resume_file.read()
                    uploaded = upload_resume_to_supabase(
                        user_id=user.id,
                        original_name=resume_file.name,
                        file_bytes=resume_bytes,
                        content_type=getattr(resume_file, "content_type", "") or "",
                    )

                    old_resume_url = profile.resume_url or ""
                    profile.resume_url = uploaded["public_url"]
                    profile.resume_name = resume_file.name

                    # --- New Centralized GROQ Analysis ---
                    resume_text = extract_resume_text(file_name=resume_file.name, file_bytes=resume_bytes)
                    ai_payload = generate_resume_ai_insights(resume_text=resume_text)

                    profile.ai_summary = ai_payload.get("ai_summary", "") or ""
                    # Update profile with AI insights
                    profile.ai_summary = ai_payload.get("ai_summary", "")
                    extracted_skills = ai_payload.get("skills", []) or []
                    predicted_roles = ai_payload.get("job_roles", []) or []
                    if extracted_skills:
                        profile.skills = ", ".join(extracted_skills)
                    profile.ai_suggestions = "\n".join([f"- {item}" for item in ai_payload.get("ai_suggestions", [])])

                    # Update target role from explicit user selection only.
                    # If user chooses general mode, clear stale role context.
                    predicted_roles = ai_payload.get("predicted_roles", [])
                    manual_role = request.POST.get("target_role", "").strip()
                    if manual_role == "__general__":
                        manual_role = ""
                    if manual_role:
                        profile.target_role = manual_role
                    else:
                        profile.target_role = ""
                    profile.save()

                    # Keep session values in sync for templates/components.
                    # --- Session Management for UI ---
                    request.session["resume_text"] = resume_text
                    
                    # Use a simple field extractor for basic info (name, email, etc.)
                    resume_fields = extract_resume_fields(resume_text) or {}
                    extras = _extract_resume_extras_from_text(resume_text)
                    resume_fields["certifications"] = extras.get("certifications", []) or resume_fields.get("certifications", [])
                    resume_fields["achievements"] = extras.get("achievements", [])
                    resume_fields["social_links"] = extras.get("social_links", [])
                    request.session["resume_fields"] = resume_fields

                    selected_role = manual_role.strip()
                    try:
                        if selected_role:
                            resume_analysis_json = analyze_resume_rule_based_json(
                                resume_text,
                                target_role=selected_role,
                            )
                        else:
                            resume_analysis_json = {}
                    except Exception:
                        resume_analysis_json = {}
                    request.session["resume_analysis_json"] = resume_analysis_json
                    # Store the clean GROQ analysis payload. This is the new source of truth.
                    request.session["groq_resume_analysis"] = ai_payload
                    
                    # Clean up old, messy session keys
                    request.session.pop("student_resume_result", None)
                    request.session.pop("groq_resume_insights", None)

                    # Primary parser payload for profile/insights pages (best for PDF).
                    # For DOC/DOCX we still keep resume_fields + resume_analysis_json so ATS works.
                    parsed_result = {}
                    if lower_name.endswith(".pdf"):
                        try:
                            from io import BytesIO

                            file_obj = BytesIO(resume_bytes)
                            file_obj.name = resume_file.name
                            file_obj.size = len(resume_bytes)
                            parsed_result = analyze_resume_file(
                                file_obj,
                                student_profile=profile,
                                target_role=selected_role,
                            )
                        except Exception:
                            parsed_result = {}

                    if parsed_result:
                        request.session["student_resume_result"] = parsed_result
                    else:
                        request.session["student_resume_result"] = {
                            "parsed_profile": {
                                "name": resume_fields.get("name", ""),
                                "email": resume_fields.get("email", ""),
                                "phone": resume_fields.get("phone", ""),
                                "skills": _as_list(resume_fields.get("skills", [])),
                                "education": _as_list(resume_fields.get("education", [])),
                                "experience": _as_list(resume_fields.get("experience", [])),
                                "projects": _as_list(resume_fields.get("projects", [])),
                                "certifications": _as_list(resume_fields.get("certifications", [])),
                                "achievements": _as_list(resume_fields.get("achievements", [])),
                                "social_links": _as_list(resume_fields.get("social_links", [])),
                                "summary": resume_fields.get("summary", ""),
                            },
                            "analysis": {
                                "ats_score": resume_analysis_json.get("ats_score"),
                                "role_match": (resume_analysis_json.get("job_match", {}) or {}).get("target_role", ""),
                                "role_match_score": (resume_analysis_json.get("job_match", {}) or {}).get("match_percentage", 0),
                                "missing_skills": (resume_analysis_json.get("job_match", {}) or {}).get("missing_skills", []),
                                "suggestions": _as_list(resume_analysis_json.get("suggestions", [])),
                            },
                            "student_summary": profile.ai_summary or "",
                        }

                    # Autofill profile from basic extracted fields
                    extracted_details = _normalize_resume_display_data(
                        result=request.session.get("student_resume_result"),
                        resume_fields=request.session.get("resume_fields", {}),
                        resume_analysis_json=request.session.get("resume_analysis_json", {}),

                        groq_analysis=ai_payload,
                    )
                    _autofill_student_profile_from_extracted(profile, extracted_details)
                    
                    # Update payload for other features like the chatbot
                    request.session["career_summary_payload"] = generate_career_summary_payload(
                        extracted_details=extracted_details,
                        target_role=selected_role,

                    )

                    request.session["groq_resume_insights"] = {
                        "career_summary": profile.ai_summary,
                        "job_roles": predicted_roles if predicted_roles else _as_list((resume_analysis_json.get("job_match", {}) or {}).get("target_role", "")),
                        "improvements": ai_payload.get("ai_suggestions", []),
                    }
                    request.session.modified = True

                    # Delete old object only after successful upload + save.
                    if old_resume_url and old_resume_url != profile.resume_url:
                        try:
                            delete_resume_from_supabase(old_resume_url)
                        except Exception:
                            pass
                    # This part was missing from the original code, but is good practice.
                    old_resume_url = getattr(profile, 'resume_url', '')
                    if old_resume_url and old_resume_url != uploaded["public_url"]:
                        delete_resume_from_supabase(old_resume_url)

                    success_message = "Resume uploaded to Supabase, analyzed with GROQ, and profile updated."
                    success_message = "Resume analyzed and profile successfully updated."
                    messages.success(request, success_message)
                    return redirect("student_upload")
                except Exception as exc:
                    error_message = f"Resume upload/processing failed: {exc}"

    state = _student_resume_state(request, profile)
    extracted_details = state["extracted_details"]
    if not state["has_resume_data"]:
        extracted_details = {
            "personal_info": {
                "name": profile.full_name or profile.user.user_name,
                "email": profile.email or profile.user.user_email,
                "phone": profile.phone or "",
                "summary": profile.ai_summary or profile.summary or "",
            },
            "skills": _split_tokens(profile.skills),
            "education": _as_list(profile.education_entries),
            "experience": _as_list(profile.experience_entries),
            "interests": _as_list(profile.interests_entries),
            "projects": _safe_list(profile.project_entries),
            "certifications": _as_list(profile.certification_entries),
            "analysis": {
                "ats_score": None,
                "ats_available": False,
                "ats_display": "Not analyzed yet",
                "mode": "general",
                "job_role": "",
                "match_score": 0,
                "predicted_role": profile.target_role or "Not set",
                "keyword_match_percentage": 0,
                "matched_keywords": [],
                "missing_keywords": [],
                "resume_tips": [],
            },
        }
    ai_suggestions_list = [line.strip("- ").strip() for line in (profile.ai_suggestions or "").splitlines() if line.strip()]

    return render(
        request,
        "student/upload_resume.html",
        {
            "profile": profile,
            "extracted_details": extracted_details,
            "error_message": error_message,
            "success_message": success_message,
            "groq_insights": {
                "career_summary": profile.ai_summary or "",
                "job_roles": request.session.get("groq_resume_insights", {}).get("job_roles", []) if request.session.get("groq_resume_insights") else _as_list(
                    (state.get("resume_analysis_json", {}).get("job_match", {}) or {}).get("target_role", "")
                ) if state.get("resume_analysis_json") else [],
                "improvements": ai_suggestions_list,
            },
            "ai_suggestions_list": ai_suggestions_list,
            "nav_active": "upload",
        },
    )


def student_resume_upload(request):
    """
    New Student Resume Analyzer module (PDF-only), Django-native and presentation-first.
    Upload -> analyze immediately -> redirect to results page.
    """
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")
    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)

    error_message = None
    if request.method == "POST":
        resume_file = request.FILES.get("resume_file")
        target_role = (request.POST.get("target_role", "") or "").strip()
        if target_role == "__general__":
            target_role = ""
        max_mb = 6

        if not resume_file:
            error_message = "Please upload your resume PDF."
        elif not (resume_file.name or "").lower().endswith(".pdf"):
            error_message = "Only PDF resumes are supported."
        elif getattr(resume_file, "size", 0) and resume_file.size > (max_mb * 1024 * 1024):
            error_message = f"File too large. Please upload a PDF under {max_mb} MB."

        if not error_message:
            try:
                pdf_bytes = resume_file.read()
                result = analyze_student_resume_pdf(
                    pdf_bytes=pdf_bytes,
                    target_role=target_role,
                    student_profile_skills=getattr(profile, "skills", "") or "",
                )
            except Exception as exc:
                print(f"[resume_analyzer] upload failed: {exc}")
                result = {"ok": False, "error": "We couldn’t process that PDF. Please try a different file."}

            if not result.get("ok"):
                error_message = result.get("error") or "Resume parsing failed. Please try again."
            else:
                request.session["student_resume_result"] = {
                    "target_role": target_role,
                    "parsed": result.get("parsed", {}),
                    "confidence_by_field": result.get("confidence_by_field", {}),
                    "ats": result.get("ats", {}),
                    "domain_match": result.get("domain_match", {}),
                }
                extracted_details = _normalize_resume_display_data(
                    result=request.session["student_resume_result"],
                    resume_fields=request.session.get("resume_fields", {}),
                    resume_analysis_json=request.session.get("resume_analysis_json", {}),
                )
                _autofill_student_profile_from_extracted(profile, extracted_details)
                request.session.modified = True
                return redirect("student_resume_result")

    return render(
        request,
        "student_resume_upload.html",
        {
            "profile": profile,
            "error_message": error_message,
        },
    )


def _resume_result_to_extracted_details(result):
    parsed = (result or {}).get("parsed_profile", {}) or {}
    analysis = (result or {}).get("analysis", {}) or {}
    keyword = (analysis.get("keyword_coverage") or {})
    return {
        "personal_info": {
            "name": parsed.get("name", ""),
            "email": parsed.get("email", ""),
            "phone": parsed.get("phone", ""),
            "summary": (result or {}).get("student_summary", ""),
        },
        "skills": parsed.get("skills", []) or [],
        "projects": parsed.get("projects", []) or [],
        "analysis": {
            "predicted_role": analysis.get("role_match", "Not predicted yet"),
            "match_score": analysis.get("role_match_score", 0),
            "ats_score": analysis.get("ats_score", 0),
            "missing_skills": analysis.get("missing_skills", []) or [],
            "keyword_match_percentage": keyword.get("match_percentage", 0),
            "matched_keywords": keyword.get("matched_keywords", []) or [],
            "resume_tips": analysis.get("suggestions", []) or [],
        },
    }


def resume_analyzer_section(request):
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")
    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)
    error_message = None
    success_message = None
    result = request.session.get("student_resume_result")

    if request.method == "POST":
        resume_file = request.FILES.get("resume_file")
        try:
            result = analyze_resume_file(resume_file, student_profile=profile)
            request.session["student_resume_result"] = result
            extracted_details = _resume_result_to_extracted_details(result)
            _autofill_student_profile_from_extracted(profile, extracted_details)
            request.session.modified = True
            success_message = "Resume parsed and analyzed successfully."
        except Exception as exc:
            error_message = str(exc)

    extracted_details = _resume_result_to_extracted_details(result)
    return render(
        request,
        "student/resume_analyzer_section.html",
        {
            "profile": profile,
            "result": result,
            "success_message": success_message,
            "error_message": error_message,
            "extracted_details": extracted_details,
            "nav_active": "upload",
        },
    )


def student_resume_result(request):
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")
    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)
    payload = request.session.get("student_resume_result")
    if not payload:
        return redirect("student_resume_upload")

    return render(
        request,
        "student_resume_result.html",
        {
            "profile": profile,
            "target_role": payload.get("target_role", "Software Developer"),
            "parsed": payload.get("parsed", {}) or {},
            "confidence_by_field": payload.get("confidence_by_field", {}) or {},
            "ats": payload.get("ats", {}) or {},
            "domain_match": payload.get("domain_match", {}) or {},
            "nav_active": "upload",
        },
    )


def generate_resume_analysis(request):
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)
    state = _student_resume_state(request, profile)
    resume_fields = state["resume_fields"]
    resume_analysis_json = state["resume_analysis_json"]
    resume_text = state["resume_text"]
    extracted_details = state["extracted_details"]
    section_data = _profile_sections_for_view(profile)
    paper_text = request.session.get("paper_text", "")
    paper_analysis_output = request.session.get("paper_analysis_output", "")
    error_message = None
    success_message = None

    if not state["has_resume_data"]:
        error_message = "Please upload your resume first."
    else:
        typed_target_role = request.POST.get("target_role", "").strip() if request.method == "POST" else ""
        selected_role = typed_target_role or profile.target_role or resume_analysis_json.get("job_match", {}).get("target_role", "") or "Software Engineer"

        if request.method == "POST":
            try:
                if resume_text:
                    resume_analysis_json = analyze_resume_rule_based_json(
                        resume_text, target_role=selected_role
                    )
                    request.session["resume_analysis_json"] = resume_analysis_json
                if paper_text:
                    paper_analysis_output = rule_based_research_paper_analysis(paper_text)
                    request.session["paper_analysis_output"] = paper_analysis_output

                extracted_details = _normalize_resume_display_data(
                    result=request.session.get("student_resume_result"),
                    resume_fields=resume_fields,
                    resume_analysis_json=resume_analysis_json,
                )
                request.session["career_summary_payload"] = generate_career_summary_payload(
                    extracted_details=extracted_details,
                    target_role=selected_role,
                )
                request.session.modified = True
                success_message = "Summary generated successfully."
            except Exception as e:
                error_message = f"Error generating summary: {str(e)}"

        if not request.session.get("career_summary_payload") and not error_message:
            request.session["career_summary_payload"] = generate_career_summary_payload(
                extracted_details=extracted_details,
                target_role=profile.target_role or "Software Engineer",
            )
            request.session.modified = True

    # "Generate Summary" is no longer a separate user step. Keep this endpoint route-safe:
    # it only refreshes in-session payloads (if POST) and then returns the user to Upload dashboard.
    return redirect("student_upload")


def research_paper_analysis(request):
    """
    Bonus-only endpoint: analyze the uploaded research paper (if present in session).
    """
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "student":
        return redirect("home")

    profile, _ = StudentProfile.objects.get_or_create(user=user)
    resume_fields = request.session.get("resume_fields", {})
    paper_text = request.session.get("paper_text", "") or ""

    error_message = None
    paper_analysis_output = request.session.get("paper_analysis_output", "")

    if request.method == "POST":
        if not paper_text.strip():
            error_message = "Please upload a research paper PDF first (optional on the resume upload page)."
        else:
            try:
                # Rule-based only (NO API).
                paper_analysis_output = rule_based_research_paper_analysis(paper_text)
                request.session["paper_analysis_output"] = paper_analysis_output
                request.session.modified = True
            except Exception as e:
                error_message = f"Error generating research paper analysis: {str(e)}"

    # Keep route-safe and inside the Upload dashboard flow.
    return redirect("student_upload")


def faculty_student_approvals(request):
    """
    Faculty page: view submitted student profiles and approve/reject them.
    """
    user = _get_logged_in_user(request)
    if user is None:
        return redirect("login")

    if _get_effective_role(user) != "faculty":
        return redirect("home")

    if request.method == "POST":
        profile_id = request.POST.get("profile_id", "")
        decision = request.POST.get("decision", "")

        try:
            student_profile = StudentProfile.objects.get(id=int(profile_id))
        except Exception:
            messages.error(request, "Student profile not found.")
            return redirect("faculty_student_approvals")

        if decision == "approve":
            student_profile.approval_status = "Approved"
            student_profile.is_approved = True
            student_profile.approval_requested = True
            student_profile.save(update_fields=["approval_status", "is_approved", "approval_requested"])
            messages.success(request, "Student profile approved.")
        elif decision == "reject":
            student_profile.approval_status = "Rejected"
            student_profile.is_approved = False
            student_profile.approval_requested = True
            student_profile.save(update_fields=["approval_status", "is_approved", "approval_requested"])
            messages.warning(request, "Student profile rejected. Student can edit and request again.")
        else:
            messages.error(request, "Invalid action.")

        return redirect("faculty_student_approvals")

    submitted_profiles = StudentProfile.objects.filter(approval_requested=True).order_by("-created_at")

    return render(
        request,
        "faculty/student_approvals.html",
        {
            "submitted_profiles": submitted_profiles,
        },
    )


# =====================================================
# STATIC PAGES
# =====================================================

def settings(request):
    if "user_email" not in request.session:
        return redirect("login")
    return render(request, "settings.html")


def help(request):
    if "user_email" not in request.session:
        return redirect("login")
    return render(request, "help.html")


def payment(request):
    if "user_email" not in request.session:
        return redirect("login")
    return render(request, 'payment.html')


# =====================================================
# CUST VIEW - Add New Records
# =====================================================

def cust_view(request):
    if "user_email" not in request.session:
        return redirect("login")
    
    success_message = None
    error_message = None
    
    if request.method == "POST":
        try:
            fs = FileSystemStorage()
            output_file = fs.path("all_authors_publications.xlsx")
            
            # Get form data
            main_author = request.POST.get("main_author", "").strip()
            title = request.POST.get("title", "").strip()
            journal = request.POST.get("journal", "").strip() or "N/A"
            conference = request.POST.get("conference", "").strip() or "N/A"
            year = request.POST.get("year", "")
            cited_by = request.POST.get("cited_by", "0")
            
            if not main_author or not title:
                error_message = "Author name and title are required."
            else:
                # Create new record
                new_record = {
                    'Main Author': main_author,
                    'Title': title,
                    'Journal': journal,
                    'conference': conference,
                    'Year': int(year) if year else None,
                    'Publication Type': 'article',
                    'Cited by': int(cited_by) if cited_by else 0,
                    'co_author': main_author,
                    'Last Search Date': datetime.now().strftime("%Y-%m-%d")
                }
                
                # Load existing or create new
                if os.path.exists(output_file):
                    df = pd.read_excel(output_file)
                    df = pd.concat([df, pd.DataFrame([new_record])], ignore_index=True)
                else:
                    df = pd.DataFrame([new_record])
                
                df.to_excel(output_file, index=False)
                success_message = f"Successfully added publication: {title}"
                
        except Exception as e:
            error_message = f"Error adding record: {str(e)}"
    
    return render(request, 'cust.html', {
        'success_message': success_message,
        'error_message': error_message
    })


# =====================================================
# MISSVAL VIEW - Edit Missing Values
# =====================================================

def missVal_view(request):
    if "user_email" not in request.session:
        return redirect("login")
    
    fs = FileSystemStorage()
    output_file = fs.path("all_authors_publications.xlsx")
    
    authors = []
    Title = []
    selected_author = request.GET.get('author', 'All')
    selected_title = request.GET.get('title', None)
    prefill_data = {'journal_name': '', 'conference_name': '', 'year': ''}
    success_message = None
    error_message = None
    
    # Load data if exists
    if os.path.exists(output_file):
        try:
            df = pd.read_excel(output_file)
            authors = df['Main Author'].dropna().unique().tolist()
            
            # Filter titles by selected author
            if selected_author and selected_author != 'All':
                filtered_df = df[df['Main Author'] == selected_author]
                Title = filtered_df['Title'].dropna().unique().tolist()
            else:
                Title = df['Title'].dropna().unique().tolist()
            
            # Prefill data for selected title
            if selected_title and selected_title != 'None':
                title_row = df[df['Title'] == selected_title]
                if not title_row.empty:
                    row = title_row.iloc[0]
                    prefill_data = {
                        'journal_name': row.get('Journal', '') if row.get('Journal') != 'N/A' else '',
                        'conference_name': row.get('conference', '') if row.get('conference') != 'N/A' else '',
                        'year': str(int(row.get('Year'))) if pd.notna(row.get('Year')) else ''
                    }
                    
        except Exception as e:
            error_message = f"Error loading data: {str(e)}"
    
    # Handle form submission
    if request.method == "POST":
        try:
            journal_name = request.POST.get('journalName', '').strip()
            conference_name = request.POST.get('conferenceName', '').strip()
            year = request.POST.get('year', '').strip()
            
            if selected_title and selected_title != 'None' and os.path.exists(output_file):
                df = pd.read_excel(output_file)
                
                # Update the record
                mask = df['Title'] == selected_title
                if journal_name:
                    df.loc[mask, 'Journal'] = journal_name
                if conference_name:
                    df.loc[mask, 'conference'] = conference_name
                if year:
                    df.loc[mask, 'Year'] = int(year)
                
                df.to_excel(output_file, index=False)
                success_message = f"Successfully updated: {selected_title}"
            else:
                error_message = "Please select a title to update."
                
        except Exception as e:
            error_message = f"Error updating record: {str(e)}"
    
    return render(request, 'missVal.html', {
        'authors': authors,
        'Title': Title,
        'selected_author': selected_author,
        'selected_title': selected_title,
        'prefill_data': prefill_data,
        'success_message': success_message,
        'error_message': error_message
    })


# =====================================================
# UPLOAD REDIRECT
# =====================================================

def upload_redirect(request):
    if "user_email" not in request.session:
        return redirect("login")
    return redirect("upload")


# =====================================================
# FACULTY PROFILE MANAGEMENT
# =====================================================

def faculty_profile(request):
    """Dashboard view for faculty to manage their profile"""
    # Check if user is logged in
    if "user_email" not in request.session:
        return redirect("login")
    
    # Get the logged-in user
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    # Check if user is a faculty member (supports both legacy `user_category` and new `role`)
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    # Get or create faculty profile
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        # Create a new profile if it doesn't exist
        profile = FacultyProfile.objects.create(
            user=user,
            full_name=user.user_name  # Initialize with username
        )
        messages.info(request, "Profile created. Please complete your profile information.")
    
    # Get publications for this faculty
    publications = profile.publications.all()
    
    # Calculate metrics (prefer uploaded Excel publication data when available)
    total_publications = profile.get_total_publications()
    total_citations = profile.get_total_citations()
    h_index = profile.get_h_index()
    i10_index = profile.get_i10_index()
    
    context = {
        'profile': profile,
        'user': user,
        'publications': publications,
        'total_publications': total_publications,
        'total_citations': total_citations,
        'h_index': h_index,
        'i10_index': i10_index,
    }
    
    return render(request, 'faculty/profile.html', context)


def faculty_profile_edit(request):
    """View for editing faculty profile information"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST":
        form = FacultyProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Profile updated successfully!")
                return redirect("faculty_profile")
            except Exception as e:
                messages.error(request, f"Error saving profile: {str(e)}")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = FacultyProfileForm(instance=profile)
    
    return render(request, 'faculty/profile_edit.html', {
        'form': form,
        'profile': profile,
        'user': user
    })


def faculty_photo_change(request):
    """View for changing profile photo"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST" and 'profile_picture' in request.FILES:
        try:
            # Delete old photo if exists
            if profile.profile_picture:
                old_photo_path = profile.profile_picture.path
                if os.path.exists(old_photo_path):
                    os.remove(old_photo_path)
            
            # Save new photo
            profile.profile_picture = request.FILES['profile_picture']
            profile.save()
            messages.success(request, "Profile photo updated successfully!")
        except Exception as e:
            messages.error(request, f"Error updating photo: {str(e)}")
    
    return redirect("faculty_profile")


def faculty_photo_remove(request):
    """View for removing profile photo"""
    if "user_email" not in request.session:
        return redirect("login")
    
    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("faculty_profile")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    try:
        # Delete photo file if exists
        if profile.profile_picture:
            photo_path = profile.profile_picture.path
            if os.path.exists(photo_path):
                os.remove(photo_path)
            profile.profile_picture = None
            profile.save()
            messages.success(request, "Profile photo removed successfully!")
        else:
            messages.info(request, "No photo to remove.")
    except Exception as e:
        messages.error(request, f"Error removing photo: {str(e)}")
    
    return redirect("faculty_profile")


def faculty_publication_add(request):
    """View for adding a new publication"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
    except FacultyProfile.DoesNotExist:
        messages.error(request, "Profile not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST":
        try:
            title = request.POST.get("title", "").strip()
            year = request.POST.get("year", "")
            journal = request.POST.get("journal", "").strip()
            citations = request.POST.get("cited_by", "0")
            
            if not title:
                messages.error(request, "Title is required.")
                return redirect("faculty_profile")
            
            Publication.objects.create(
                main_author=profile.full_name or user.user_name,
                title=title,
                year=int(year) if year else datetime.now().year,
                cited_by=int(citations) if citations else 0,
                conference_journal=journal,
                faculty=profile
            )
            messages.success(request, "Publication added successfully!")
        except Exception as e:
            messages.error(request, f"Error adding publication: {str(e)}")
    
    return redirect("faculty_profile")


def faculty_publication_edit(request, pub_id):
    """View for editing a publication"""
    if "user_email" not in request.session:
        return redirect("login")
    
    try:
        user = Users_Publication.objects.get(user_email=request.session["user_email"])
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")
    
    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")
    
    try:
        profile = FacultyProfile.objects.get(user=user)
        publication = Publication.objects.get(id=pub_id, faculty=profile)
    except (FacultyProfile.DoesNotExist, Publication.DoesNotExist):
        messages.error(request, "Publication not found.")
        return redirect("faculty_profile")
    
    if request.method == "POST":
        try:
            publication.title = request.POST.get("title", "").strip()
            publication.year = int(request.POST.get("year", datetime.now().year))
            publication.conference_journal = request.POST.get("journal", "").strip()
            publication.cited_by = int(request.POST.get("cited_by", "0") or "0")
            publication.save()
            messages.success(request, "Publication updated successfully!")
            return redirect("faculty_profile")
        except Exception as e:
            messages.error(request, f"Error updating publication: {str(e)}")
    
    return render(request, 'faculty/publication_edit.html', {
        'publication': publication,
        'profile': profile
    })

@csrf_exempt
def chatbot(request):
    user = _get_logged_in_user(request)
    conversations = []
    active_conversation = None
    active_messages = []
    if user:
        conversations = TalvynConversation.objects.filter(user=user)[:30]
        conv_id = request.GET.get("conv", "").strip()
        if conv_id.isdigit():
            active_conversation = TalvynConversation.objects.filter(user=user, id=int(conv_id)).first()
        if active_conversation is None and conversations:
            active_conversation = conversations[0]
        if active_conversation:
            active_messages = list(active_conversation.messages.all()[:80])
    return render(
        request,
        "chatbot.html",
        {
            "conversations": conversations,
            "active_conversation": active_conversation,
            "active_messages": active_messages,
            "nav_active": "talvyn",
            "is_faculty": bool(user and _get_effective_role(user) == "faculty"),
        },
    )


def messages_page(request):
    user = _get_logged_in_user(request)
    if not user:
        return redirect("login")
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    return render(
        request,
        "auth/messages.html",
        {
            "is_faculty": _get_effective_role(user) == "faculty",
            "nav_active": "messages",
            "current_user_id": user.id,
            "current_user_name": user.user_name,
            "supabase_url": supabase_url,
            "supabase_anon_key": supabase_anon_key,
        },
    )


def chat_page(request):
    """
    Minimal Supabase-powered 1:1 chat page.
    - Django renders the page + peer list
    - Browser uses Supabase JS SDK (anon key) for messages + realtime
    """
    me = _get_logged_in_user(request)
    if not me:
        return redirect("login")

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()

    # Keep it simple: render a small peer list (client-side search filters it).
    peers = []
    for item in Users_Publication.objects.exclude(id=me.id).order_by("user_name")[:200]:
        peers.append(
            {
                "id": item.id,
                "name": item.user_name,
                "email": item.user_email,
                "role": _get_effective_role(item),
            }
        )

    return render(
        request,
        "auth/chat.html",
        {
            "is_faculty": _get_effective_role(me) == "faculty",
            "nav_active": "chat",
            "current_user_id": me.id,
            "current_user_name": me.user_name,
            "supabase_url": supabase_url,
            "supabase_anon_key": supabase_anon_key,
            "chat_peers_json": json.dumps(peers),
        },
    )


def search_users(request):
    user = _get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    query = (request.GET.get("q", "") or "").strip()
    users_qs = Users_Publication.objects.exclude(id=user.id)
    if query:
        users_qs = users_qs.filter(Q(user_name__icontains=query) | Q(user_email__icontains=query))

    rows = []
    for item in users_qs.order_by("user_name")[:50]:
        rows.append(
            {
                "id": item.id,
                "name": item.user_name,
                "email": item.user_email,
                "role": _get_effective_role(item),
            }
        )
    return JsonResponse({"results": rows})


@require_POST
def get_or_create_conversation(request):
    me = _get_logged_in_user(request)
    if not me:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    other_user_id = str(payload.get("other_user_id", "")).strip()
    if not other_user_id.isdigit():
        return JsonResponse({"error": "Invalid user id"}, status=400)

    other = Users_Publication.objects.filter(id=int(other_user_id)).first()
    if not other:
        return JsonResponse({"error": "User not found"}, status=404)

    try:
        client = _get_supabase_service_client()

        # Prefer Supabase Auth UUIDs when conversation columns are UUID typed.
        me_sid = _resolve_supabase_user_id(client, me.user_email) or str(me.id)
        other_sid = _resolve_supabase_user_id(client, other.user_email) or str(other.id)

        # Attempt lookup across known two-party column pairs.
        for c1, c2 in _conversation_field_pairs():
            expr = f"and({c1}.eq.{me_sid},{c2}.eq.{other_sid}),and({c1}.eq.{other_sid},{c2}.eq.{me_sid})"
            try:
                res = client.table("conversations").select("*").or_(expr).limit(1).execute()
                if res.data:
                    return JsonResponse({"conversation": res.data[0]})
            except Exception:
                continue

        # Create conversation with the first pair that matches schema/types.
        for c1, c2 in _conversation_field_pairs():
            try:
                created = client.table("conversations").insert({c1: me_sid, c2: other_sid}).execute()
                if created.data:
                    return JsonResponse({"conversation": created.data[0]})
            except Exception:
                continue

        return JsonResponse({"error": "Unable to create conversation with current schema."}, status=500)
    except Exception as exc:
        return JsonResponse({"error": f"Conversation error: {exc}"}, status=500)


@require_POST
def send_message(request):
    me = _get_logged_in_user(request)
    if not me:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    conversation_id = str(payload.get("conversation_id", "")).strip()
    content = (payload.get("content") or "").strip()
    message_id = str(payload.get("id") or payload.get("message_id") or "").strip()
    status = str(payload.get("status") or "sent").strip()
    if not conversation_id or not content:
        return JsonResponse({"error": "conversation_id and content are required"}, status=400)

    try:
        client = _get_supabase_service_client()
        sender_sid = _resolve_supabase_user_id(client, me.user_email) or str(me.id)
        row = _create_message_row(
            client,
            {
                **({"id": message_id} if message_id else {}),
                "conversation_id": conversation_id,
                "sender_id": sender_sid,
                "content": content,
                "status": status,
            },
        )
        return JsonResponse({"message": row})
    except Exception as exc:
        return JsonResponse({"error": f"Send failed: {exc}"}, status=500)


def get_messages(request):
    me = _get_logged_in_user(request)
    if not me:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    conversation_id = (request.GET.get("conversation_id") or "").strip()
    if not conversation_id:
        return JsonResponse({"error": "conversation_id is required"}, status=400)

    limit_raw = (request.GET.get("limit") or "20").strip()
    before = (request.GET.get("before") or "").strip()  # ISO timestamp string (created_at)
    try:
        limit = max(1, min(50, int(limit_raw)))
    except Exception:
        limit = 20

    try:
        client = _get_supabase_service_client()
        sender_sid = _resolve_supabase_user_id(client, me.user_email) or str(me.id)
        q = client.table("messages").select("*").eq("conversation_id", conversation_id)
        if before:
            q = q.lt("created_at", before)
        # Fetch newest chunk then reverse on server to keep UI stable.
        result = q.order("created_at", desc=True).limit(limit).execute()

        serialized = []
        rows = list(result.data or [])
        rows.reverse()
        for row in rows:
            sender_id = row.get("sender_id") or row.get("from_user_id") or row.get("user_id")
            serialized.append(
                {
                    "id": row.get("id"),
                    "conversation_id": row.get("conversation_id"),
                    "sender_id": sender_id,
                    "content": _resolve_message_text(row),
                    "created_at": _resolve_row_ts(row),
                    "is_me": str(sender_id) == str(sender_sid),
                    "status": row.get("status") or "sent",
                }
            )
        next_before = serialized[0]["created_at"] if serialized else None
        return JsonResponse({"messages": serialized, "next_before": next_before})
    except Exception as exc:
        return JsonResponse({"error": f"Load failed: {exc}"}, status=500)


@require_POST
def update_message_status(request):
    me = _get_logged_in_user(request)
    if not me:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    conversation_id = str(payload.get("conversation_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    ids = payload.get("message_ids") or payload.get("ids") or []
    if not conversation_id:
        return JsonResponse({"error": "conversation_id is required"}, status=400)
    if status not in {"delivered", "seen"}:
        return JsonResponse({"error": "status must be delivered or seen"}, status=400)
    if not isinstance(ids, list) or not ids:
        return JsonResponse({"ok": True, "updated": 0})

    cleaned = []
    for raw in ids[:200]:
        val = str(raw or "").strip()
        if val:
            cleaned.append(val)
    if not cleaned:
        return JsonResponse({"ok": True, "updated": 0})

    try:
        client = _get_supabase_service_client()
        res = (
            client.table("messages")
            .update({"status": status})
            .eq("conversation_id", conversation_id)
            .in_("id", cleaned)
            .execute()
        )
        updated = len(res.data or [])
        return JsonResponse({"ok": True, "updated": updated})
    except Exception as exc:
        return JsonResponse({"error": f"Update failed: {exc}"}, status=500)


@require_POST
def chatbot_api(request):
    try:
        data = json.loads(request.body)
        user_input = (data.get("query") or data.get("message") or "").strip()
        if not user_input:
            return JsonResponse({"error": "Please enter a message."}, status=400)

        user = _get_logged_in_user(request)
        conversation = None
        conv_raw = str(data.get("conversation_id", "")).strip()
        if user:
            if conv_raw.isdigit():
                conversation = TalvynConversation.objects.filter(user=user, id=int(conv_raw)).first()
            if conversation is None:
                conversation = TalvynConversation.objects.create(
                    user=user,
                    title=_talvyn_title_from_query(user_input),
                    preview=user_input[:250],
                )
            TalvynMessage.objects.create(conversation=conversation, role="user", content=user_input)

        profile_context = _build_user_profile_context(request)
        recent_context = _talvyn_recent_context(conversation)
        system_prompt = (
            "You are Talvyn, an AI career intelligence assistant for ResearchRadar. "
            "Use the provided profile context when the user asks profile-specific questions "
            "(like strengths, cons, ATS score, missing skills, improvement focus, etc.). "
            "If the user asks outside-profile/general questions, answer helpfully using general knowledge. "
            "If profile details are missing, clearly say what is missing and suggest uploading resume/generating analysis. "
            "Be concise, practical, and provide job or skill guidance when relevant."
            "\n\nProfile Context:\n"
            f"{profile_context}"
            "\n\nRecent Conversation Context:\n"
            f"{recent_context or 'No prior chat history.'}"
        )
        ai_response = generate_ai_response(user_input, system_prompt=system_prompt)
        if (ai_response or "").lower().startswith("error:"):
            # Never return backend exception traces to user-facing chat page.
            return JsonResponse(
                {"answer": "I could not process that right now. Please try again in a moment.", "citations": []}
            )
        if conversation:
            TalvynMessage.objects.create(conversation=conversation, role="assistant", content=ai_response)
            conversation.preview = ai_response[:250]
            conversation.save(update_fields=["preview", "updated_at"])
        return JsonResponse(
            {
                "answer": ai_response,
                "citations": [],
                "conversation_id": conversation.id if conversation else None,
                "conversation_title": conversation.title if conversation else "",
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        return JsonResponse(
            {"answer": "I could not process that right now. Please try again.", "citations": []}
        )


# =====================================================
# STUDENT PROFILE PICTURE & CERTIFICATIONS
# =====================================================

def student_photo_change(request):
    user = _get_logged_in_user(request)
    if not user or _get_effective_role(user) != "student":
        return redirect("home")
    profile, _ = StudentProfile.objects.get_or_create(user=user)
    
    if request.method == "POST" and 'profile_picture' in request.FILES:
        try:
            if getattr(profile, 'profile_picture', None):
                old_path = profile.profile_picture.path
                if os.path.exists(old_path):
                    os.remove(old_path)
            profile.profile_picture = request.FILES['profile_picture']
            profile.save()
            messages.success(request, "Profile photo updated successfully!")
        except Exception as e:
            messages.error(request, f"Error updating photo: {str(e)}")
    return redirect("student_profile")


def student_photo_remove(request):
    user = _get_logged_in_user(request)
    if not user or _get_effective_role(user) != "student":
        return redirect("home")
    profile, _ = StudentProfile.objects.get_or_create(user=user)
    
    if request.method == "POST":
        try:
            if getattr(profile, 'profile_picture', None):
                photo_path = profile.profile_picture.path
                if os.path.exists(photo_path):
                    os.remove(photo_path)
                profile.profile_picture = None
                profile.save()
                messages.success(request, "Profile photo removed successfully!")
        except Exception as e:
            messages.error(request, f"Error removing photo: {str(e)}")
    return redirect("student_profile")


def student_certification_upload(request):
    user = _get_logged_in_user(request)
    if not user or _get_effective_role(user) != "student":
        return redirect("home")
    profile, _ = StudentProfile.objects.get_or_create(user=user)
    
    if request.method == "POST":
        title = request.POST.get("name", "").strip()
        issuer = request.POST.get("issuer", "").strip()
        year = request.POST.get("year", "").strip()
        cert_file = request.FILES.get("cert_file")
        
        if not title:
            messages.error(request, "Certification title is required.")
            return redirect(f"{reverse('student_profile')}?edit=1")
            
        file_url = ""
        if cert_file:
            try:
                supabase_url = os.environ.get("SUPABASE_URL", "").strip()
                service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                client = create_client(supabase_url, service_role_key)
                import uuid
                ext = os.path.splitext(cert_file.name)[1]
                file_path = f"certifications/{user.id}_{uuid.uuid4()}{ext}"
                
                client.storage.from_("resumes").upload(
                    file_path,
                    cert_file.read(),
                    {"content-type": getattr(cert_file, "content_type", "application/octet-stream")}
                )
                file_url = client.storage.from_("resumes").get_public_url(file_path)
            except Exception as e:
                messages.error(request, f"File upload failed: {e}")
                return redirect(f"{reverse('student_profile')}?edit=1")
        
        certs = list(getattr(profile, "certification_entries", []) or [])
        certs.append({
            "name": title,
            "issuer": issuer,
            "year": year,
            "file_url": file_url,
            "file_name": cert_file.name if cert_file else ""
        })
        profile.certification_entries = certs
        profile.save(update_fields=["certification_entries"])
        messages.success(request, "Certification added successfully.")
        
    return redirect(f"{reverse('student_profile')}?edit=1")


def student_certification_delete(request, index):
    user = _get_logged_in_user(request)
    if not user or _get_effective_role(user) != "student":
        return redirect("home")
    profile, _ = StudentProfile.objects.get_or_create(user=user)
    
    if request.method == "POST":
        certs = list(getattr(profile, "certification_entries", []) or [])
        if 0 <= index < len(certs):
            file_url = certs[index].get("file_url")
            if file_url and "supabase" in file_url:
                try:
                    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
                    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                    client = create_client(supabase_url, service_role_key)
                    path_parts = file_url.split("/resumes/")
                    if len(path_parts) > 1:
                        client.storage.from_("resumes").remove([path_parts[1]])
                except Exception:
                    pass
            
            certs.pop(index)
            profile.certification_entries = certs
            profile.save(update_fields=["certification_entries"])
            messages.success(request, "Certification deleted.")
        else:
            messages.error(request, "Certification not found.")
            
    return redirect(f"{reverse('student_profile')}?edit=1")

# ---------------- DELETE PUBLICATION VIEW ----------------
def faculty_publication_delete(request, pub_id):
    """View for deleting a publication"""

    if "user_email" not in request.session:
        return redirect("login")

    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("faculty_profile")

    try:
        user = Users_Publication.objects.get(
            user_email=request.session["user_email"]
        )
    except Users_Publication.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect("login")

    if _get_effective_role(user) != "faculty":
        messages.warning(request, "This page is only accessible to faculty members.")
        return redirect("home")

    try:
        profile = FacultyProfile.objects.get(user=user)
        publication = Publication.objects.get(id=pub_id, faculty=profile)
        publication.delete()
        messages.success(request, "Publication deleted successfully!")

    except (FacultyProfile.DoesNotExist, Publication.DoesNotExist):
        messages.error(request, "Publication not found.")

    except Exception as e:
        messages.error(request, f"Error deleting publication: {str(e)}")

    return redirect("faculty_profile")