import io
import json
import re

from docx import Document

from graph_app.groq_client import generate_ai_response
from graph_app.student_ai import extract_pdf_text


def extract_resume_text(file_name: str, file_bytes: bytes) -> str:
    suffix = (file_name or "").lower().rsplit(".", 1)[-1] if "." in (file_name or "") else ""
    if suffix == "pdf":
        return extract_pdf_text(io.BytesIO(file_bytes))
    if suffix == "docx":
        doc = Document(io.BytesIO(file_bytes))
        return "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])
    if suffix == "doc":
        # Best-effort fallback for legacy .doc files.
        return file_bytes.decode("utf-8", errors="ignore")
    raise ValueError("Unsupported resume format. Please upload PDF, DOC, or DOCX.")


def _extract_json_payload(content: str) -> dict:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip().rstrip("`").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except Exception:
        return {}


def generate_resume_ai_insights(resume_text: str) -> dict:
    prompt = (
        "Analyze this resume text and return ONLY valid JSON with this exact schema:\n"
        "{\n"
        '  "ai_summary": "short professional summary in 3-4 lines",\n'
        '  "skills": ["skill1", "skill2"],\n'
        '  "ai_suggestions": ["suggestion1", "suggestion2"]\n'
        "}\n"
        "Rules:\n"
        "- skills must be concise technical/professional skills\n"
        "- ai_suggestions must be actionable\n"
        "- no markdown and no extra keys\n\n"
        f"Resume Text:\n{(resume_text or '')[:14000]}"
    )
    response = generate_ai_response(
        user_input=prompt,
        system_prompt="You are a strict resume analyzer returning JSON only.",
    )
    if (response or "").lower().startswith("error:"):
        return {"ai_summary": "", "skills": [], "ai_suggestions": [], "error": response}

    payload = _extract_json_payload(response)
    summary = str(payload.get("ai_summary", "") or "").strip()
    skills = payload.get("skills") if isinstance(payload.get("skills"), list) else []
    suggestions = payload.get("ai_suggestions") if isinstance(payload.get("ai_suggestions"), list) else []

    normalized_skills = [str(skill).strip() for skill in skills if str(skill).strip()][:40]
    normalized_suggestions = [str(item).strip() for item in suggestions if str(item).strip()][:20]
    return {
        "ai_summary": summary,
        "skills": normalized_skills,
        "ai_suggestions": normalized_suggestions,
    }
