"""Check Supabase tables via REST API."""
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

from supabase import create_client
client = create_client(SUPABASE_URL, SERVICE_KEY)

tables_to_check = [
    "graph_app_users_publication",
    "graph_app_studentprofile",
    "graph_app_talvynconversation",
    "graph_app_talvynmessage",
    "graph_app_publication",
    "graph_app_facultyprofile",
    "auth_user_groups",
    "auth_user",
    "auth_permission",
    "auth_group_permissions",
    "auth_group",
    "django_migrations",
    "django_content_type",
    "django_session",
]

print("=== SUPABASE TABLE CHECK VIA REST API ===")
for tbl in tables_to_check:
    try:
        res = client.table(tbl).select("*", count="exact").limit(0).execute()
        count = res.count if hasattr(res, 'count') else '?'
        print(f"  {tbl}: EXISTS (count={count})")
    except Exception as e:
        err_str = str(e)
        if "does not exist" in err_str or "relation" in err_str.lower() or "404" in err_str:
            print(f"  {tbl}: MISSING - {err_str[:120]}")
        else:
            print(f"  {tbl}: ERROR - {err_str[:120]}")
