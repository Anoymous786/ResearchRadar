import os
import uuid
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from supabase import Client, create_client


SUPABASE_RESUME_BUCKET = os.environ.get("SUPABASE_RESUME_BUCKET", "resumes").strip() or "resumes"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Missing required env variable: {name}")
    return value


def get_supabase_client() -> Client:
    url = _required_env("SUPABASE_URL")
    # Prefer service role key server-side; fall back to anon key only if necessary.
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or _required_env("SUPABASE_ANON_KEY")
    return create_client(url, key)


def build_resume_storage_path(user_id: int, original_name: str) -> str:
    extension = os.path.splitext(original_name or "")[1].lower() or ".pdf"
    safe_ext = extension if extension in {".pdf", ".doc", ".docx"} else ".pdf"
    return f"student_{user_id}/resume_{uuid.uuid4().hex}{safe_ext}"


def upload_resume_to_supabase(user_id: int, original_name: str, file_bytes: bytes, content_type: str = "") -> dict:
    client = get_supabase_client()
    path = build_resume_storage_path(user_id=user_id, original_name=original_name)
    options = {"upsert": "true"}
    if content_type:
        options["content-type"] = content_type

    client.storage.from_(SUPABASE_RESUME_BUCKET).upload(path=path, file=file_bytes, file_options=options)
    public_url = client.storage.from_(SUPABASE_RESUME_BUCKET).get_public_url(path)
    return {
        "path": path,
        "public_url": public_url,
        "original_name": original_name,
    }


def _extract_storage_path_from_public_url(public_url: str) -> str:
    if not public_url:
        return ""
    parsed = urlparse(public_url)
    marker = f"/storage/v1/object/public/{SUPABASE_RESUME_BUCKET}/"
    if marker not in parsed.path:
        return ""
    return unquote(parsed.path.split(marker, 1)[1])


def delete_resume_from_supabase(public_url: str) -> bool:
    path = _extract_storage_path_from_public_url(public_url)
    if not path:
        return False
    client = get_supabase_client()
    client.storage.from_(SUPABASE_RESUME_BUCKET).remove([path])
    return True
