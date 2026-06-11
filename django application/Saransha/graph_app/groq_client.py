from groq import Groq

import json
import os
import re
from typing import Any, Dict, List

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_MODEL_FALLBACKS = [
    m.strip()
    for m in os.environ.get(
        "GROQ_MODEL_FALLBACKS",
        "llama-3.1-8b-instant,llama-3.3-70b-versatile,gemma2-9b-it",
    ).split(",")
    if m.strip()
]

# Keep cliet     e  gh5qj62w3e48r9p;0to6'.9iful,mykztn bdvsc
# nt creation lazy so missing env var doesn't crash imports.
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

def generate_ai_response(user_input, system_prompt="You are a helpful AI project assistant."):
    try:
        if client is None:
            return "Error: GROQ_API_KEY is not configured. Set it in your environment to use AI features."

        model_candidates = []
        if GROQ_MODEL:
            model_candidates.append(GROQ_MODEL)
        for m in GROQ_MODEL_FALLBACKS:
            if m not in model_candidates:
                model_candidates.append(m)

        last_error = None
        for model_name in model_candidates:
            try:
                chat_completion = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_input}
                    ],
                    model=model_name,
                )
                return chat_completion.choices[0].message.content
            except Exception as model_exc:
                last_error = model_exc
                # If model is not supported/decommissioned, try next model.
                # For any other runtime API error we still try fallbacks once.
                continue

        return f"Error: {str(last_error)}"

    except Exception as e:
        return f"Error: {str(e)}"


def _extract_json_object(text: str) -> Dict[str, Any]:
    """
    Extract a JSON object from a model response.
    Accepts fenced blocks and minor extra text; returns {} if parsing fails.
    """
    if not text:
        return {}
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = raw.rstrip("`").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except Exception:
        return {}


def generate_resume_insights(resume_text: str) -> Dict[str, Any]:
    """
    Groq-powered resume insights.
    Returns a dict:
      - career_summary: str
      - job_roles: list[str]
      - improvements: list[str]
    """
    if client is None:
        return {
            "error": "GROQ_API_KEY is not configured. Set it in your environment to use AI features.",
            "career_summary": "",
            "job_roles": [],
            "improvements": [],
        }

    text = (resume_text or "").strip()
    # Keep latency reasonable while still providing enough context.
    # (Most relevant resume signal is in first pages; long PDFs can be noisy.)
    max_chars = int(os.environ.get("GROQ_RESUME_MAX_CHARS", "12000") or 12000)
    if len(text) > max_chars:
        text = text[:max_chars]

    system_prompt = (
        "You are an expert resume career coach and ATS specialist.\n"
        "Return ONLY valid JSON. No markdown. No extra keys.\n"
        "Schema:\n"
        "{\n"
        '  "career_summary": "string (5-6 short lines, professional tone)",\n'
        '  "job_roles": ["5-10 relevant job roles"],\n'
        '  "improvements": ["8-14 bullet improvements, actionable and specific"]\n'
        "}\n"
        "Rules:\n"
        "- Use only information supported by the resume; don't invent degrees/companies.\n"
        "- Keep job roles realistic for the profile seniority.\n"
        "- Improvements must be concrete (metrics, keywords, structure, ATS).\n"
    )

    user_prompt = (
        "Resume text:\n"
        f"{text}\n\n"
        "Generate the JSON now."
    )

    response = generate_ai_response(user_prompt, system_prompt=system_prompt)
    if (response or "").lower().startswith("error:"):
        return {
            "error": response,
            "career_summary": "",
            "job_roles": [],
            "improvements": [],
        }

    payload = _extract_json_object(response)
    career_summary = str(payload.get("career_summary", "") or "").strip()
    job_roles = payload.get("job_roles") or []
    improvements = payload.get("improvements") or []

    if not isinstance(job_roles, list):
        job_roles = []
    if not isinstance(improvements, list):
        improvements = []

    job_roles = [str(x).strip() for x in job_roles if str(x).strip()][:10]
    improvements = [str(x).strip() for x in improvements if str(x).strip()][:16]

    return {
        "career_summary": career_summary,
        "job_roles": job_roles,
        "improvements": improvements,
    }