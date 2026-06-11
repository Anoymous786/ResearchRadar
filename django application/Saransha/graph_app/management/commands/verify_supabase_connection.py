from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from graph_app.models import Users_Publication


class Command(BaseCommand):
    help = "Verify PostgreSQL DB connectivity and optionally insert sample faculty signup row."

    def add_arguments(self, parser):
        parser.add_argument(
            "--insert-sample",
            action="store_true",
            help="Insert a sample faculty user row into Users_Publication table.",
        )

    def handle(self, *args, **options):
        db = connection.settings_dict
        self.stdout.write(self.style.NOTICE("Active DB backend details:"))
        self.stdout.write(f"  ENGINE: {db.get('ENGINE')}")
        self.stdout.write(f"  NAME: {db.get('NAME')}")
        self.stdout.write(f"  HOST: {db.get('HOST')}")
        self.stdout.write(f"  PORT: {db.get('PORT')}")

        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
        self.stdout.write(self.style.SUCCESS("PostgreSQL connection OK."))
        self.stdout.write(f"  Server version: {version}")

        if options["insert_sample"]:
            stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            sample_email = f"faculty.supabase.test.{stamp}@example.com"
            sample_user = Users_Publication.objects.create(
                user_name="Supabase Faculty Test",
                user_email=sample_email,
                user_password="temp-password",
                user_category="faculty",
                role="faculty",
            )
            self.stdout.write(self.style.SUCCESS("Sample faculty signup inserted successfully."))
            self.stdout.write(f"  Inserted id: {sample_user.id}")
            self.stdout.write(f"  Inserted email: {sample_user.user_email}")
            self.stdout.write(f"  Inserted at: {timezone.now().isoformat()}")
