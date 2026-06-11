"""Diagnose Supabase/PostgreSQL remote database state."""
import os, sys
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = os.environ.get("DB_PORT", "5432")

print("=== REMOTE DB CONNECTION TEST ===")
print(f"  Host: {DB_HOST}")
print(f"  Port: {DB_PORT}")
print(f"  User: {DB_USER}")

import psycopg2

# Supabase default database name is 'postgres'
DB_NAME = "postgres"

try:
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        sslmode="require",
    )
    print(f"  Connected to: {DB_NAME}")
except Exception as e:
    print(f"  FAILED to connect: {e}")
    sys.exit(1)

cursor = conn.cursor()

# 1. List all tables in the public schema
cursor.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    ORDER BY table_name
""")
tables = [r[0] for r in cursor.fetchall()]
print(f"\n=== TABLES IN public SCHEMA ({len(tables)} total) ===")
for t in tables:
    print(f"  {t}")

# 2. Check django_migrations table
problem_tables = [
    "graph_app_studentprofile",
    "graph_app_talvynconversation",
    "graph_app_talvynmessage",
    "graph_app_publication",
    "graph_app_facultyprofile",
    "graph_app_users_publication",
    "auth_user_groups",
    "auth_user",
    "auth_permission",
    "auth_group_permissions",
    "auth_group",
    "django_migrations",
    "django_content_type",
    "django_session",
    "django_admin_log",
]

print("\n=== CHECKING EXPECTED TABLES ===")
for tbl in problem_tables:
    if tbl in tables:
        try:
            cursor.execute(f'SELECT COUNT(*) FROM public."{tbl}"')
            count = cursor.fetchone()[0]
            cursor.execute(f"""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_schema = 'public' AND table_name = '{tbl}'
                ORDER BY ordinal_position
            """)
            cols = cursor.fetchall()
            print(f"\n  {tbl} -- EXISTS, {count} rows, {len(cols)} columns")
            for col_name, col_type in cols:
                print(f"    {col_name}: {col_type}")
        except Exception as e:
            print(f"\n  {tbl} -- EXISTS but ERROR: {e}")
    else:
        print(f"\n  {tbl} -- MISSING!")

# 3. Check if django_migrations has any entries
if "django_migrations" in tables:
    print("\n=== DJANGO MIGRATIONS RECORDED ===")
    cursor.execute('SELECT app, name FROM public."django_migrations" ORDER BY app, name')
    for app, name in cursor.fetchall():
        print(f"  {app}: {name}")

conn.close()
print("\n=== DONE ===")
