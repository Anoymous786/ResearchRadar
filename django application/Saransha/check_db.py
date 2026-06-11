"""Quick diagnostic script to check database tables and their schemas."""
import os, sys, django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Saransha.settings")
django.setup()

from django.db import connection

cursor = connection.cursor()

# 1. List all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cursor.fetchall()]
print("=== TABLES IN DATABASE ===")
for t in tables:
    print(f"  {t}")

# 2. Check each problematic table
problem_tables = [
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
]

print("\n=== CHECKING PROBLEMATIC TABLES ===")
for tbl in problem_tables:
    if tbl in tables:
        try:
            cursor.execute(f"PRAGMA table_info({tbl})")
            cols = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
            count = cursor.fetchone()[0]
            print(f"\n  {tbl} — EXISTS, {count} rows, {len(cols)} columns")
            for col in cols:
                print(f"    {col}")
        except Exception as e:
            print(f"\n  {tbl} — EXISTS but ERROR querying: {e}")
    else:
        print(f"\n  {tbl} — MISSING!")

# 3. Try model queries
print("\n=== TESTING DJANGO ORM QUERIES ===")
from graph_app.models import StudentProfile, TalvynConversation, TalvynMessage, Publication, FacultyProfile
from django.contrib.auth.models import User, Group, Permission

for model_name, model in [
    ("StudentProfile", StudentProfile),
    ("TalvynConversation", TalvynConversation),
    ("TalvynMessage", TalvynMessage),
    ("Publication", Publication),
    ("FacultyProfile", FacultyProfile),
    ("User", User),
    ("Group", Group),
    ("Permission", Permission),
]:
    try:
        count = model.objects.count()
        print(f"  {model_name}.objects.count() = {count}")
    except Exception as e:
        print(f"  {model_name} — ERROR: {e}")

# 4. Check expected vs actual columns for graph_app models
print("\n=== SCHEMA COMPARISON ===")
for model_name, model in [
    ("StudentProfile", StudentProfile),
    ("TalvynConversation", TalvynConversation),
    ("TalvynMessage", TalvynMessage),
    ("Publication", Publication),
    ("FacultyProfile", FacultyProfile),
]:
    tbl = model._meta.db_table
    expected_cols = {f.column for f in model._meta.get_fields() if hasattr(f, 'column')}
    cursor.execute(f"PRAGMA table_info({tbl})")
    actual_cols = {row[1] for row in cursor.fetchall()}
    missing = expected_cols - actual_cols
    extra = actual_cols - expected_cols
    if missing or extra:
        print(f"\n  {model_name} ({tbl}):")
        if missing:
            print(f"    MISSING columns in DB: {missing}")
        if extra:
            print(f"    EXTRA columns in DB (not in model): {extra}")
    else:
        print(f"  {model_name} ({tbl}): schema matches ✓")
